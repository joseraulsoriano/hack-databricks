"""E/S contra Databricks: subir archivos al Volume y ejecutar SQL en el warehouse."""

import os
import re
import time
from pathlib import Path

from databricks.sdk.service.sql import StatementParameterListItem, StatementState

from data_pipeline import auth

CATALOG_SCHEMA = "workspace.lab"
VOLUME_ROOT = "/Volumes/workspace/lab/raw"
STAGING_COLUMNS = """
  doc_id, source, source_id, doc_type, title, CAST(authors AS ARRAY<STRING>) AS authors, CAST(year AS INT) AS year,
  doi, url, license, CAST(is_open_access AS BOOLEAN) AS is_open_access, abstract, full_text, raw_path, metadata,
  content_hash, query, fetched_by, CAST(fetched_at AS TIMESTAMP) AS fetched_at
"""


class Databricks:
    def __init__(self, profile: str | None = None, warehouse_id: str | None = None):
        self.w = auth.workspace(profile)
        self.warehouse_id = warehouse_id or os.environ.get("DATABRICKS_WAREHOUSE_ID") or self._first_warehouse()

    def _first_warehouse(self) -> str:
        warehouses = list(self.w.warehouses.list())
        if not warehouses:
            raise RuntimeError("No hay SQL warehouses en el workspace")
        return warehouses[0].id

    def sql(self, statement: str, params: dict[str, str] | None = None, timeout_s: int = 900) -> list[list]:
        """params se enlazan como :nombre en la sentencia (nunca interpolar texto del agente)."""
        resp = self.w.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id, statement=statement, wait_timeout="50s",
            parameters=[StatementParameterListItem(name=k, value=v) for k, v in (params or {}).items()],
        )
        deadline = time.monotonic() + timeout_s
        while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
            if time.monotonic() > deadline:
                raise TimeoutError(f"SQL sin terminar tras {timeout_s}s: {statement[:80]}")
            time.sleep(3)
            resp = self.w.statement_execution.get_statement(resp.statement_id)
        if resp.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"SQL falló ({resp.status.state}): {resp.status.error.message if resp.status.error else ''}")
        if not resp.result:
            return []
        # Un resultado grande llega en varios trozos (next_chunk_index). Leer solo el primero
        # devolvia filas de menos SIN avisar: con 13 columnas de documents_curated, 19 180 de 21 915.
        datos = list(resp.result.data_array or [])
        siguiente = resp.result.next_chunk_index
        while siguiente is not None:
            trozo = self.w.statement_execution.get_statement_result_chunk_n(resp.statement_id, siguiente)
            datos.extend(trozo.data_array or [])
            siguiente = trozo.next_chunk_index
        # Mas de 25 MB en un solo resultado (disposition INLINE) falla con un error explicito: en
        # ese caso hay que paginar con LIMIT/OFFSET, como hace curar.leer_staging.
        return datos

    def apply_sql_file(self, path: Path) -> int:
        # Solo líneas de comentario completas y ';' a final de línea: los COMMENT '...' pueden llevar ambos.
        text = re.sub(r"(?m)^\s*--.*$", "", path.read_text())
        statements = [s.strip() for s in re.split(r";\s*$", text, flags=re.M) if s.strip()]
        for s in statements:
            self.sql(s)
        return len(statements)

    def upload_dir(self, local_dir: Path, volume_dir: str) -> int:
        count = 0
        for f in sorted(p for p in local_dir.rglob("*") if p.is_file()):
            with f.open("rb") as fh:
                self.w.files.upload(f"{volume_dir}/{f.relative_to(local_dir).as_posix()}", fh, overwrite=True)
            count += 1
        return count

    def copy_into_staging(self, volume_staging_dir: str) -> list[list]:
        return self.sql(f"""
            COPY INTO {CATALOG_SCHEMA}.documents_staging
            FROM (SELECT {STAGING_COLUMNS} FROM '{volume_staging_dir}')
            FILEFORMAT = JSON
        """)

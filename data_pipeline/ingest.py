"""Descarga el corpus del nicho y lo deja en workspace.lab.documents_staging.

Uso:
  uv run python -m data_pipeline.ingest --apply-schema --sink databricks     # todo el corpus PETasa
  uv run python -m data_pipeline.ingest --sources europepmc --limit 20       # prueba rápida en local

Cada ejecución crea data/runs/<run_id>/ con:
  raw/<source>.jsonl        payload original de la API, una línea por documento (inmutable)
  staging/<source>.jsonl    filas normalizadas para documents_staging
  structures/<id>.cif       estructuras PDB (con --download-structures)
y con --sink databricks se sube a /Volumes/workspace/lab/raw/runs/<run_id>/ y se carga con COPY INTO.
"""

import argparse
import json
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from data_pipeline.connectors import alphafold, europepmc, openalex, pdb, zenodo
from data_pipeline.connectors.base import Http
from data_pipeline.schema import Fetched

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ("europepmc", "openalex", "pdb", "alphafold", "zenodo")
LITERATURE_QUERY = 'PETase OR "PET hydrolase" OR "poly(ethylene terephthalate) hydrolase"'
PDB_TERMS = ("PETase", "PET hydrolase")
# El buscador de Zenodo rechaza paréntesis dentro de frases entrecomilladas.
ZENODO_QUERY = 'PETase OR "PET hydrolase"'


class RunWriter:
    def __init__(self, run_id: str, base: Path = ROOT / "data" / "runs"):
        self.run_id = run_id
        self.dir = base / run_id
        self.volume_dir = f"/Volumes/workspace/lab/raw/runs/{run_id}"
        self.counts: Counter[str] = Counter()

    def write(self, source: str, items: Iterable[Fetched], fetched_by: str) -> list[Fetched]:
        (self.dir / "raw").mkdir(parents=True, exist_ok=True)
        (self.dir / "staging").mkdir(parents=True, exist_ok=True)
        kept: list[Fetched] = []
        with (self.dir / "raw" / f"{source}.jsonl").open("a") as raw_f, \
             (self.dir / "staging" / f"{source}.jsonl").open("a") as stg_f:
            for item in items:
                item.doc.raw_path = f"{self.volume_dir}/raw/{source}.jsonl"
                item.doc.fetched_by = fetched_by
                raw_f.write(json.dumps({"doc_id": item.doc.doc_id, "payload": item.raw}, ensure_ascii=False) + "\n")
                stg_f.write(item.doc.model_dump_json() + "\n")
                self.counts[source] += 1
                kept.append(item)
        return kept

    def write_file(self, rel_path: str, content: bytes) -> None:
        path = self.dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def run(sources: Iterable[str], limit: int | None, full_text: bool, download_structures: bool,
        fetched_by: str = "ingest_cli", run_id: str | None = None,
        literature_query: str = LITERATURE_QUERY, pdb_terms: Iterable[str] = PDB_TERMS) -> RunWriter:
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    writer = RunWriter(run_id)
    sources = list(sources)
    uniprot: list[str] = [alphafold.IS_PETASE]

    if "europepmc" in sources:
        writer.write("europepmc", europepmc.search(literature_query, limit, full_text=full_text), fetched_by)
        print(f"europepmc: {writer.counts['europepmc']}")
    if "openalex" in sources:
        writer.write("openalex", openalex.search(literature_query, limit), fetched_by)
        print(f"openalex: {writer.counts['openalex']}")
    if "pdb" in sources:
        http = Http()
        ids = list(dict.fromkeys(i for term in pdb_terms for i in pdb.search_ids(term, http, limit)))
        entries = pdb.fetch_entries(ids, http)
        kept = writer.write("pdb", (pdb.to_fetched(e, " | ".join(pdb_terms)) for e in entries), fetched_by)
        for e in entries:
            uniprot.extend(pdb.uniprot_accessions(e))
        print(f"pdb: {writer.counts['pdb']} entradas, {len(set(uniprot))} accesiones UniProt")
        if download_structures:
            for item in kept:
                writer.write_file(f"structures/{item.doc.source_id}.cif", pdb.download_cif(item.doc.source_id, http))
            print(f"pdb: {len(kept)} archivos .cif descargados")
    if "alphafold" in sources:
        writer.write("alphafold", alphafold.fetch(uniprot, query="uniprot from pdb"), fetched_by)
        print(f"alphafold: {writer.counts['alphafold']}")
    if "zenodo" in sources:
        writer.write("zenodo", zenodo.search(ZENODO_QUERY, limit), fetched_by)
        writer.write("zenodo", zenodo.fetch_records([zenodo.MEGASCALE_STABILITY], query="curated: mega-scale stability"), fetched_by)
        print(f"zenodo: {writer.counts['zenodo']}")
    return writer


def publish(writer: RunWriter, apply_schema: bool, profile: str | None, warehouse_id: str | None) -> None:
    from data_pipeline.databricks_io import Databricks

    db = Databricks(profile=profile, warehouse_id=warehouse_id)
    if apply_schema:
        n = db.apply_sql_file(ROOT / "sql" / "001_schema.sql")
        print(f"esquema: {n} sentencias aplicadas")
    n = db.upload_dir(writer.dir, writer.volume_dir)
    print(f"volume: {n} archivos subidos a {writer.volume_dir}")
    result = db.copy_into_staging(f"{writer.volume_dir}/staging/")
    print(f"documents_staging: COPY INTO -> {result}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sources", default=",".join(SOURCES))
    p.add_argument("--limit", type=int, default=None, help="Máximo por fuente (por defecto: todo)")
    p.add_argument("--no-full-text", action="store_true", help="No descargar texto completo de Europe PMC")
    p.add_argument("--download-structures", action="store_true", help="Descargar los .cif de PDB")
    p.add_argument("--sink", choices=("local", "databricks"), default="local")
    p.add_argument("--apply-schema", action="store_true", help="Crear las tablas de sql/001_schema.sql")
    p.add_argument("--profile", default=None)
    p.add_argument("--warehouse-id", default=None)
    args = p.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = set(sources) - set(SOURCES)
    if unknown:
        p.error(f"Fuentes desconocidas: {', '.join(sorted(unknown))}")
    writer = run(sources, args.limit, not args.no_full_text, args.download_structures)
    print(f"run {writer.run_id}: {dict(writer.counts)} en {writer.dir}")
    if args.sink == "databricks":
        publish(writer, args.apply_schema, args.profile, args.warehouse_id)


if __name__ == "__main__":
    main()

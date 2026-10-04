"""Camino rápido: consulta el índice vectorial y devuelve evidencia citable.

Separado del bucle completo a propósito. La voz necesita responder en menos de 1,5 s y el
laboratorio tarda de 9 a 13 s, así que este módulo no habla con los agentes: solo recupera.

Las credenciales de Databricks viven aquí, en el servidor. El navegador nunca las ve.
"""

import asyncio
import html
import os
import re
import time

from databricks.sdk import WorkspaceClient

from ar_vr_bridge.contract import Citation

INDEX = os.environ.get("VS_INDEX", "workspace.lab.rag_v0_idx")
COLUMNS = ["chunk_id", "doc_id", "title", "year", "doi", "url", "source"]

# Medido sobre rag_v0_idx: las consultas con respuesta real puntúan 0.80-0.85; las que solo
# rozan el tema se quedan en 0.65-0.66. Por debajo de este umbral no afirmamos nada.
SCORE_THRESHOLD = float(os.environ.get("VS_SCORE_THRESHOLD", "0.70"))

# El endpoint serverless se enfría: el p95 medido salta a 4,4 s tras unos minutos parado.
KEEPWARM_SECONDS = int(os.environ.get("VS_KEEPWARM_SECONDS", "45"))

_TAGS = re.compile(r"<[^>]+>")
_client: WorkspaceClient | None = None


def _workspace() -> WorkspaceClient:
    global _client
    if _client is None:
        _client = WorkspaceClient(profile=os.environ.get("DATABRICKS_CONFIG_PROFILE", "hack"))
    return _client


def clean(text: str) -> str:
    """Europe PMC devuelve títulos con HTML (<i>Is</i>PETase). Fuera, o se ve mal en el visor."""
    return _TAGS.sub("", html.unescape(text or "")).strip()


def _query(text: str, num_results: int) -> list[list]:
    resp = _workspace().api_client.do(
        "POST", f"/api/2.0/vector-search/indexes/{INDEX}/query",
        body={"query_text": text, "columns": COLUMNS, "num_results": num_results},
    )
    return resp.get("result", {}).get("data_array", []) or []


def _to_citations(rows: list[list], limit: int) -> list[Citation]:
    """Colapsa por DOI: 943 artículos están en Europe PMC y OpenAlex a la vez.

    Sin esto, una consulta de 5 resultados puede devolver solo 3 artículos distintos.
    Las estructuras del PDB nunca se colapsan: comparten el DOI del artículo de origen
    pero son registros diferentes.
    """
    seen: set[str] = set()
    citations: list[Citation] = []
    for chunk_id, doc_id, title, year, doi, url, source, score in rows:
        key = doi if (doi and source != "pdb") else chunk_id
        if key in seen:
            continue
        seen.add(key)
        citations.append(Citation(
            id=f"c{len(citations) + 1}", doc_id=doc_id, title=clean(title),
            year=int(year) if year else None, doi=doi or "", url=url or "",
            source=source or "", score=round(float(score), 3),
        ))
        if len(citations) >= limit:
            break
    return citations


def search(query: str, num_results: int = 5) -> tuple[list[Citation], bool, int]:
    """Devuelve (citas, hay_evidencia, latencia_ms).

    Pide el triple de resultados para que el colapso por DOI no deje la lista corta.
    """
    started = time.perf_counter()
    rows = _query(query, num_results * 3)
    citations = _to_citations(rows, num_results)
    latency_ms = int((time.perf_counter() - started) * 1000)
    has_evidence = bool(citations) and citations[0].score >= SCORE_THRESHOLD
    return citations, has_evidence, latency_ms


async def keep_warm() -> None:
    """Consulta periódica para evitar el arranque en frío durante la demo."""
    while True:
        try:
            await asyncio.to_thread(_query, "PET hydrolase", 1)
        except Exception:
            pass  # un fallo puntual no debe tumbar la tarea de fondo
        await asyncio.sleep(KEEPWARM_SECONDS)

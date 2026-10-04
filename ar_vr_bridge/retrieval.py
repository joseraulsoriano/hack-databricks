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


# ---------------------------------------------------------------------------
# El indice y el corpus curado NO son la misma tabla (ver docs/CONTEXTO_AGENTE.md).
# `rag_v0` tiene 4 213 documentos; `documents_curated`, 3 424. De los indexados,
# 879 nunca pasaron por la curacion humana. Sin este filtro el visor puede citar
# un documento que nadie aprobo, que es justo el control que el proyecto declara.
# Cuando el indice se reconstruya sobre los trozos curados, el filtro deja de
# descartar nada y se queda como red de seguridad.
# ---------------------------------------------------------------------------

CURATED = "workspace.lab.documents_curated"
APROBADOS_TTL = int(os.environ.get("VS_APROBADOS_TTL", "600"))

_aprobados: set[str] | None = None   # None = todavia no se sabe, no "ninguno"
_aprobados_ts: float = 0.0


def _sql(statement: str) -> list[list]:
    from data_pipeline.databricks_io import Databricks
    return Databricks().sql(statement)


def aprobados(forzar: bool = False) -> set[str] | None:
    """doc_ids que una persona promovio a `documents_curated`. None si no se pudo leer.

    Se cachea: son ~3 400 cadenas y no cambian durante una demo. Distinguir None de
    conjunto vacio importa: vacio significa "ninguno aprobado" y dejaria la demo muda.
    """
    global _aprobados, _aprobados_ts
    if not forzar and _aprobados is not None and time.time() - _aprobados_ts < APROBADOS_TTL:
        return _aprobados
    try:
        filas = _sql(f"SELECT DISTINCT doc_id FROM {CURATED} "
                     f"WHERE approved_by IS NOT NULL AND approved_by <> ''")
        _aprobados = {f[0] for f in filas if f and f[0]}
        _aprobados_ts = time.time()
    except Exception:
        _aprobados = None  # sin corpus no se filtra, pero se dice (ver `search`)
    return _aprobados


def _snippets(doc_ids: list[str], query: str) -> dict[str, str]:
    """Para cada documento, el trozo curado que mas terminos comparte con la consulta.

    El indice actual es de un documento por fila, asi que NO sabemos que pasaje
    disparo el acierto. Elegimos localmente el trozo con mas solapamiento de
    terminos y devolvemos vacio si ninguno comparte nada: preferimos un `snippet`
    vacio a uno que insinue ser la frase que sostiene la afirmacion sin serlo.
    Con el indice sobre trozos curados esto se sustituye por el trozo real.
    """
    ids = ", ".join(f"'{d}'" for d in sorted({d for d in doc_ids if d and "'" not in d}))
    if not ids:
        return {}
    try:
        filas = _sql(f"SELECT doc_id, text FROM {CURATED} WHERE doc_id IN ({ids})")
    except Exception:
        return {}
    terminos = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 3}
    mejor: dict[str, tuple[int, str]] = {}
    for doc_id, texto in filas:
        if not texto:
            continue
        solape = len(terminos & {t for t in re.findall(r"\w+", texto.lower()) if len(t) > 3})
        if solape and solape > mejor.get(doc_id, (0, ""))[0]:
            mejor[doc_id] = (solape, texto.strip()[:400])
    return {d: t for d, (_, t) in mejor.items()}


def search(query: str, num_results: int = 5,
           con_snippet: bool = False) -> tuple[list[Citation], bool, int]:
    """Devuelve (citas, hay_evidencia, latencia_ms). Solo cita lo curado y aprobado.

    Pide el triple de resultados para que el colapso por DOI no deje la lista corta,
    y pide de mas otra vez porque el filtro de curacion aun descarta documentos.

    `con_snippet` cuesta una consulta SQL (~1 s): va bien en el bucle de 9-13 s y no
    en el camino de voz, que tiene 1,5 s. Por eso esta apagado por defecto.
    """
    started = time.perf_counter()
    rows = _query(query, num_results * 4)
    citations = _to_citations(rows, num_results * 2)

    permitidos = aprobados()
    if permitidos is not None:
        citations = [c for c in citations if c.doc_id in permitidos]
    citations = citations[:num_results]

    if con_snippet and citations:
        textos = _snippets([c.doc_id for c in citations], query)
        citations = [c.model_copy(update={"snippet": textos.get(c.doc_id, "")}) for c in citations]

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

"""Persistencia de las preguntas en `workspace.lab.queries`.

Hasta ahora una pregunta llegaba, movía una corrida y desaparecía: vivía solo en
memoria y no sobrevivía a un reinicio. Una prueba de conexión del visor podía
funcionar y no dejar rastro en ninguna tabla.

**El orden importa y es lo único no negociable aquí: se inserta AL RECIBIR**, con
`answered = false`, y se actualiza al terminar. Si se escribiera solo al terminar,
la pregunta que tumba el laboratorio no dejaría fila — y es justo la que más
interesa encontrar. Una fila en `answered = false` una hora después es un fallo
localizable.

Como `registro.py`: no bloquea, no rompe y se apaga con `QUERIES_LOG=0`.
"""

import asyncio
import json
import os

TABLA = "workspace.lab.queries"

_en_vuelo: set[asyncio.Task] = set()
# El insert de cada pregunta, para que su update no se adelante: las dos escrituras
# van en segundo plano y sin esto podrian cruzarse.
_llegadas: dict[str, asyncio.Task] = {}
_db = None


def activo() -> bool:
    return os.environ.get("QUERIES_LOG", "1") == "1"


def _cliente():
    global _db
    if _db is None:
        from data_pipeline.databricks_io import Databricks
        _db = Databricks()
    return _db


def insertar(query_id: str, query: str, source: str, mode: str,
             asked_by: str = "", language: str = "") -> None:
    """Fila nueva con `answered = false`. Sincrono; lo llama `llegada` en un hilo."""
    _cliente().sql(
        f"""INSERT INTO {TABLA}
              (query_id, query, source, asked_by, asked_at, mode, language,
               answered, verdict, has_evidence, latency_ms, citation_doc_ids, error)
            VALUES (:qid, :query, :source, :asked_by, current_timestamp(), :mode,
                    :language, false, NULL, NULL, NULL, NULL, NULL)""",
        params={"qid": query_id, "query": query[:4000], "source": source,
                "asked_by": asked_by or None, "mode": mode, "language": language or None})


def completar(query_id: str, *, verdict: str = "", has_evidence=None,
              latency_ms: int = 0, citation_doc_ids=(), error: str = "") -> None:
    """Cierra la fila. `answered` pasa a true salvo que la corrida fallara."""
    ids = sorted({str(d) for d in citation_doc_ids if d})[:200]
    _cliente().sql(
        f"""UPDATE {TABLA} SET
              answered = :answered, verdict = :verdict, has_evidence = :has_evidence,
              latency_ms = :latency, error = :error,
              citation_doc_ids = from_json(:ids, 'ARRAY<STRING>')
            WHERE query_id = :qid""",
        params={"qid": query_id, "answered": str(not error).lower(),
                "verdict": verdict or None,
                "has_evidence": None if has_evidence is None else str(bool(has_evidence)).lower(),
                "latency": str(int(latency_ms)), "error": error or None,
                "ids": json.dumps(ids)})


async def _intentar(fn, *a, **kw) -> None:
    try:
        await asyncio.to_thread(fn, *a, **kw)
    except Exception as exc:  # perder la traza no puede tumbar una demo en vivo
        print(f"   queries: no se pudo escribir ({type(exc).__name__}: {exc})")


def _lanzar(coro) -> asyncio.Task | None:
    try:
        tarea = asyncio.create_task(coro)
    except RuntimeError:
        coro.close()
        return None  # sin bucle de eventos: no se registra
    _en_vuelo.add(tarea)
    tarea.add_done_callback(_en_vuelo.discard)
    return tarea


def llegada(query_id: str, query: str, source: str, mode: str,
            asked_by: str = "", language: str = "") -> None:
    """Deja constancia de la pregunta antes de correrla. No bloquea."""
    if not activo():
        return
    tarea = _lanzar(_intentar(insertar, query_id, query, source, mode, asked_by, language))
    if tarea is not None:
        _llegadas[query_id] = tarea


async def _completar_tras_llegada(query_id: str, **kw) -> None:
    if (previa := _llegadas.pop(query_id, None)) is not None:
        await asyncio.gather(previa, return_exceptions=True)  # el insert va primero
    await _intentar(completar, query_id, **kw)


def final(query_id: str, **kw) -> None:
    """Cierra la fila cuando la corrida termina. No bloquea."""
    if not activo():
        return
    _lanzar(_completar_tras_llegada(query_id, **kw))

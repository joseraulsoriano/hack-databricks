"""Traza del puente en `workspace.lab.research_record`.

El brief pide un registro compartido donde cada decisión se pueda reconstruir. Hasta
ahora la tabla solo recogía las aprobaciones de curación, así que una prueba de
conexión desde el visor podía funcionar y ser **invisible** en Databricks. Esto lo
arregla: lo que entra por el puente deja rastro.

Tres reglas, porque esto vive en el camino de una demo en vivo:

1. **No bloquea.** `anotar()` lanza la escritura en segundo plano y devuelve el
   control de inmediato. El visor nunca espera a Databricks, y la voz menos.
2. **No rompe.** Si el warehouse no responde, se pierde la traza y ya. Un registro
   caído no puede tumbar la demo; se avisa por consola y se sigue.
3. **No inunda.** Se anota lo que es una decisión —consulta recibida, veredicto de
   una hipótesis, aprobación humana resuelta—, no cada nodo del grafo. Son ~3
   escrituras por sesión, no 30.

Se apaga con `RESEARCH_RECORD=0`.
"""

import asyncio
import json
import os
import uuid

TABLA = "workspace.lab.research_record"

# Las tareas en vuelo se guardan para que el recolector de basura no las cancele
# a medias: asyncio solo mantiene referencias débiles a las tareas sueltas.
_en_vuelo: set[asyncio.Task] = set()
_db = None


def activo() -> bool:
    return os.environ.get("RESEARCH_RECORD", "1") == "1"


def _cliente():
    """Un solo cliente para todo el proceso: construirlo lista warehouses y cuesta."""
    global _db
    if _db is None:
        from data_pipeline.databricks_io import Databricks
        _db = Databricks()
    return _db


def registrar(kind: str, summary: str, *, session_id: str, from_agent: str,
              to_agent: str = "bridge", flow: str = "evidence", weight: float = 1.0,
              refs=(), payload: dict | None = None) -> None:
    """Escribe una fila. Síncrono y con SQL parametrizado; lo llama `anotar` en un hilo."""
    _cliente().sql(
        f"""INSERT INTO {TABLA}
              (event_id, session_id, ts, from_agent, to_agent, kind, flow, weight,
               summary, refs, payload)
            VALUES (:evento, :sesion, current_timestamp(), :desde, :hacia, :kind,
                    :flow, :peso, :resumen, from_json(:refs, 'ARRAY<STRING>'), :payload)""",
        params={"evento": str(uuid.uuid4()), "sesion": session_id, "desde": from_agent,
                "hacia": to_agent, "kind": kind, "flow": flow, "peso": str(weight),
                "resumen": summary[:900],
                "refs": json.dumps(sorted({str(r) for r in refs if r})[:200]),
                "payload": json.dumps(payload or {}, ensure_ascii=False)[:8000]})


async def _intentar(**kw) -> None:
    try:
        await asyncio.to_thread(registrar, **kw)
    except Exception as exc:  # una traza perdida no puede tumbar una demo en vivo
        print(f"   research_record: no se pudo anotar ({type(exc).__name__}: {exc})")


def anotar(kind: str, summary: str, **kw) -> None:
    """Deja la traza en segundo plano. Devuelve de inmediato y nunca lanza."""
    if not activo():
        return
    try:
        tarea = asyncio.create_task(_intentar(kind=kind, summary=summary, **kw))
    except RuntimeError:
        return  # sin bucle de eventos (p. ej. en una prueba síncrona): no se anota
    _en_vuelo.add(tarea)
    tarea.add_done_callback(_en_vuelo.discard)

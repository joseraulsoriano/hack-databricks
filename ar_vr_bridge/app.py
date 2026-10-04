"""Puente entre el laboratorio de agentes y el visor WebXR.

Rutas:
  GET  /                    cliente WebXR (se abre en el navegador de las Quest)
  GET  /health              comprobación
  WS   /ws/explore          bucle completo en streaming: el grafo crece mientras el agente trabaja
  POST /api/v1/explore      misma respuesta, de una pieza, para clientes sin WebSocket
  POST /api/v1/approve/{id} respuesta a una petición de aprobación del Safety Agent

Arranque:
  uv run uvicorn ar_vr_bridge.app:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ar_vr_bridge import mock, retrieval
from ar_vr_bridge.contract import (
    SCHEMA_VERSION, Answer, AskRequest, AskResponse, ExploreRequest, ExploreResponse,
)

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Scientific Discovery Lab — puente AR/VR", version=SCHEMA_VERSION)
# El visor puede servirse desde otro origen durante el desarrollo.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Aprobaciones pendientes: approval_id -> Future que resuelve con la decisión humana.
_pending_approvals: dict[str, asyncio.Future] = {}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "schema_version": SCHEMA_VERSION}


@app.on_event("startup")
async def _startup() -> None:
    """Mantiene caliente el índice: en frío el p95 medido salta a 4,4 s."""
    if os.environ.get("VS_KEEPWARM", "1") == "1":
        app.state.keepwarm = asyncio.create_task(retrieval.keep_warm())


@app.post("/api/v1/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Camino rápido de la voz: solo consulta el RAG. Presupuesto 1,5 s.

    No lanza hipótesis ni experimento: por eso cabe donde el bucle completo no cabe.
    Si no hay evidencia por encima del umbral, lo dice en vez de improvisar.
    """
    query_id = f"q_{uuid.uuid4().hex[:8]}"
    citations, has_evidence, latency_ms = await asyncio.to_thread(
        retrieval.search, request.query, request.num_results,
    )
    tts_text = "" if has_evidence else (
        "No encuentro evidencia suficiente sobre eso en el corpus. "
        "Puedo buscar algo relacionado si quieres."
    )
    return AskResponse(query_id=query_id, citations=citations, has_evidence=has_evidence,
                       tts_text=tts_text, latency_ms=latency_ms)


async def _events(query: str, query_id: str, mode: str):
    """Fuente de eventos. 'mock' simula; 'live' delega en el lab de agentes cuando exista."""
    if mode == "live":
        try:
            from agent_lab.runtime import stream_discovery  # aún no implementado
        except ImportError:
            mode = "mock"
        else:
            async for event in stream_discovery(query, query_id):
                yield event
            return
    async for event in mock.stream(query, query_id):
        yield event


@app.websocket("/ws/explore")
async def ws_explore(ws: WebSocket) -> None:
    """Un mensaje {"query": "...", "mode": "mock"} inicia un bucle; los eventos llegan en orden."""
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            payload = json.loads(raw)

            # Respuesta a una aprobación pendiente, no una pregunta nueva.
            if approval_id := payload.get("approval_id"):
                if fut := _pending_approvals.get(approval_id):
                    if not fut.done():
                        fut.set_result(payload.get("decision", "reject"))
                continue

            request = ExploreRequest(**payload)
            query_id = request.query_id or f"q_{uuid.uuid4().hex[:8]}"
            async for event in _events(request.query, query_id, request.mode):
                await ws.send_text(event.model_dump_json())
    except WebSocketDisconnect:
        return


@app.post("/api/v1/explore", response_model=ExploreResponse)
async def explore(request: ExploreRequest) -> ExploreResponse:
    """Ejecuta el bucle completo y devuelve el resultado acumulado."""
    query_id = request.query_id or f"q_{uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    response = ExploreResponse(query_id=query_id, answer=Answer(headline=""))
    async for event in _events(request.query, query_id, request.mode):
        kind = event.event
        if kind == "node":
            response.nodes.append(event.node)
        elif kind == "edge":
            response.edges.append(event.edge)
        elif kind == "citations":
            response.citations.extend(event.citations)
        elif kind == "answer":
            response.answer = event.answer
        elif kind == "validation":
            response.validation = event.validation
        elif kind == "error":
            response.status = "error"
    response.latency_ms = int((time.monotonic() - started) * 1000)
    return response


@app.post("/api/v1/approve/{approval_id}")
async def approve(approval_id: str, decision: str = "approve") -> dict:
    """El visor responde a una puerta de aprobación del Safety Agent."""
    fut = _pending_approvals.get(approval_id)
    if fut is None:
        return {"status": "unknown_approval", "approval_id": approval_id}
    if not fut.done():
        fut.set_result(decision)
    return {"status": "ok", "approval_id": approval_id, "decision": decision}


async def request_approval(approval_id: str, timeout_s: float = 120.0) -> str:
    """Lo llama el Safety Agent: bloquea hasta que una persona responda o venza el plazo."""
    fut = asyncio.get_running_loop().create_future()
    _pending_approvals[approval_id] = fut
    try:
        return await asyncio.wait_for(fut, timeout_s)
    except TimeoutError:
        return "timeout"
    finally:
        _pending_approvals.pop(approval_id, None)

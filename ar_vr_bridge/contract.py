"""Contrato entre el laboratorio de agentes y el visor.

Un solo archivo define lo que viaja por el WebSocket. Si cambia algo aquí, cambia en el visor:
no hay otra fuente de verdad. Pensado para render incremental: el grafo crece mientras el
agente trabaja, así los segundos de latencia son parte de la demo y no una pantalla de espera.
"""

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

# Capas del grafo: determinan la profundidad a la que se dibuja cada nodo.
NodeType = Literal["question", "evidence", "variable", "hypothesis", "experiment", "result"]
LAYER = {"question": 0, "evidence": 1, "variable": 2, "hypothesis": 3, "experiment": 4, "result": 5}

Stage = Literal["received", "retrieving", "hypothesizing", "experimenting", "validating", "done", "error"]
Verdict = Literal["PASS", "WARN", "FAIL", "PENDING"]


class Citation(BaseModel):
    id: str                      # 'c1', referenciado desde nodos y aristas
    doc_id: str                  # 'europepmc:29374183'
    title: str
    authors_short: str = ""      # 'Joo et al.'
    year: int | None = None
    doi: str = ""
    url: str = ""
    source: str = ""             # europepmc | openalex | pdb | alphafold | zenodo
    snippet: str = ""            # frase exacta que sostiene la afirmación
    score: float = 0.0


class Node(BaseModel):
    id: str
    type: NodeType
    label: str                   # texto corto para la etiqueta 3D (<= 48 caracteres)
    detail: str = ""             # texto largo, visible al enfocar el nodo
    layer: int = 0
    confidence: float = 1.0      # 0-1; controla opacidad y tamaño
    agent_generated: bool = False  # true => se dibuja con borde discontinuo (exigencia del brief)
    citation_ids: list[str] = Field(default_factory=list)
    props: list[dict[str, str]] = Field(default_factory=list)  # lista, no dict: Unity y JSON plano


class Edge(BaseModel):
    source: str
    target: str
    relation: str                # studies | supports | contradicts | tests | validated_by
    weight: float = 1.0          # 0-1; controla grosor
    citation_ids: list[str] = Field(default_factory=list)


class Check(BaseModel):
    name: str                    # citation_grounding | source_diversity | cv_stability | overfit_gap
    passed: bool
    value: float
    threshold: float
    detail: str = ""


class Validation(BaseModel):
    verdict: Verdict = "PENDING"
    confidence: float = 0.0      # producto de los checks, nunca lo dice el LLM
    checks: list[Check] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    headline: str                # una línea, la que se lee primero en el visor
    conclusion: str = ""
    justification: str = ""
    tts_text: str = ""           # <= 40 palabras, para voz


# ---------------------------------------------------------------------------
# Eventos del WebSocket. Todos llevan 'event' y 'query_id'.
# ---------------------------------------------------------------------------

class StageEvent(BaseModel):
    event: Literal["stage"] = "stage"
    query_id: str
    stage: Stage
    message: str = ""            # texto a mostrar mientras tanto
    progress: float = 0.0        # 0-1, para la barra


class NodeEvent(BaseModel):
    event: Literal["node"] = "node"
    query_id: str
    node: Node


class EdgeEvent(BaseModel):
    event: Literal["edge"] = "edge"
    query_id: str
    edge: Edge


class CitationsEvent(BaseModel):
    event: Literal["citations"] = "citations"
    query_id: str
    citations: list[Citation]


class AnswerEvent(BaseModel):
    event: Literal["answer"] = "answer"
    query_id: str
    answer: Answer


class ValidationEvent(BaseModel):
    event: Literal["validation"] = "validation"
    query_id: str
    validation: Validation


class ApprovalEvent(BaseModel):
    """El Safety Agent pide aprobación. El visor muestra el panel y el grafo se congela."""
    event: Literal["approval_request"] = "approval_request"
    query_id: str
    approval_id: str
    question: str
    context: str = ""
    options: list[str] = Field(default_factory=lambda: ["approve", "reject"])


class DoneEvent(BaseModel):
    event: Literal["done"] = "done"
    query_id: str
    latency_ms: int = 0
    next_question: str = ""      # lo que el lab investigaría a continuación


class ErrorEvent(BaseModel):
    event: Literal["error"] = "error"
    query_id: str
    message: str


class ExploreRequest(BaseModel):
    query: str
    query_id: str = ""
    mode: Literal["live", "mock"] = "live"


class ExploreResponse(BaseModel):
    """Respuesta completa de la ruta REST, para clientes que no usan WebSocket."""
    schema_version: str = SCHEMA_VERSION
    query_id: str
    status: Literal["ok", "error"] = "ok"
    answer: Answer
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation)
    latency_ms: int = 0

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
    fatal: bool = True           # false => es un aviso; no tumba el veredicto


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


class AskRequest(BaseModel):
    """Camino rápido para la voz: solo recuperación, sin bucle de agentes."""
    query: str = Field(min_length=1, max_length=500)   # transcripción, tal como se dijo
    num_results: int = Field(default=5, ge=1, le=10)
    query_id: str = ""        # lo manda el cliente para correlacionar con /ws/explore
    language: str = ""        # BCP-47; pista para el idioma de la respuesta
    asked_by: str = ""        # revisor, del ?reviewer= de la gate
    source: str = "voice"     # voice | text | agent


class AskResponse(BaseModel):
    schema_version: str = SCHEMA_VERSION
    query_id: str
    answer: str = ""          # prosa completa, para el panel
    citations: list[Citation] = Field(default_factory=list)
    has_evidence: bool = False
    # <= 40 palabras, se lee en voz alta y debe sostenerse sin las citas.
    tts_text: str = ""
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Pasajes para el agente. Lo que devuelve `evidence_span` se pega TAL CUAL en
# una hipótesis: la puerta lo coteja literal (ver docs/VERIFICABILIDAD.md).
# ---------------------------------------------------------------------------

class EvidenceRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    num_results: int = Field(default=8, ge=1, le=30)
    doc_id: str = ""      # busca DENTRO de un documento: «mira en este paper»
    section: str = ""     # filtra por sección (Introduction, Methods, …)


class Passage(BaseModel):
    chunk_id: str
    doc_id: str
    source: str = ""
    title: str = ""
    year: int | None = None
    doi: str = ""
    url: str = ""
    section: str = ""
    evidence_span: str    # literal; se pega sin tocar un carácter
    overlap: int = 0      # términos de la consulta presentes en el pasaje


class EvidenceResponse(BaseModel):
    schema_version: str = SCHEMA_VERSION
    query: str = ""
    count: int = 0
    doc_ids: list[str] = Field(default_factory=list)   # fuentes distintas halladas
    passages: list[Passage] = Field(default_factory=list)
    latency_ms: int = 0


class DocumentResponse(BaseModel):
    """Un documento curado entero: lo más parecido a abrir el PDF que hay aquí."""
    schema_version: str = SCHEMA_VERSION
    doc_id: str = ""
    source: str = ""
    title: str = ""
    year: int | None = None
    doi: str = ""
    url: str = ""
    chunks: int = 0
    sections: list[str] = Field(default_factory=list)
    passages: list[Passage] = Field(default_factory=list)
    latency_ms: int = 0


class ExploreRequest(BaseModel):
    query: str
    query_id: str = ""
    mode: Literal["live", "mock"] = "live"


# ---------------------------------------------------------------------------
# Entrada de hipotesis. El lab no recibe preguntas abiertas: recibe hipotesis
# que ya traen su respaldo, y la puerta de procedencia decide si entran.
# Logica en agent_lab/procedencia.py; criterio en docs/VERIFICABILIDAD.md.
# ---------------------------------------------------------------------------

Verdicto = Literal["ADMITIDA", "ADMITIDA_CON_AVISOS", "RECHAZADA"]


class Respaldo(BaseModel):
    """Una pieza de evidencia que la hipotesis dice que la sostiene."""
    doc_id: str                  # 'europepmc:29374183'; tiene que estar en documents_curated
    evidence_span: str           # la frase EXACTA; se coteja literal contra el documento
    record_id: str = ""          # fila de mutant_stability, si el dato es numerico
    value: float | None = None   # el numero afirmado; debe aparecer en evidence_span
    unit: str = ""


class HypothesisRequest(BaseModel):
    statement: str               # la hipotesis, afirmativa y concreta
    prediction: str              # que se deberia observar si es cierta (medible)
    variables: list[str] = Field(default_factory=list)   # columnas de pet_activity_ml
    respaldo: list[Respaldo] = Field(default_factory=list)
    submitted_by: str = ""


class HypothesisReceipt(BaseModel):
    """Recibo de admision. Reproducible: mismo input y mismo corpus, mismo hash."""
    schema_version: str = SCHEMA_VERSION
    receipt_hash: str = ""
    verdict: Verdicto = "RECHAZADA"
    admitted: bool = False       # true para ADMITIDA y ADMITIDA_CON_AVISOS
    checks: list[Check] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latency_ms: int = 0


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

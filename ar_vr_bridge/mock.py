"""Generador de sesiones simuladas para desarrollar el visor sin esperar a los agentes.

Produce la misma secuencia de eventos que producirá el lab real, con los mismos tiempos
aproximados. Si hay conexión a Databricks usa citas reales de documents_staging; si no,
cae a un conjunto fijo para poder trabajar sin red.
"""

import asyncio
import random
from collections.abc import AsyncIterator

from ar_vr_bridge.contract import (
    LAYER, Answer, AnswerEvent, Check, Citation, CitationsEvent, DoneEvent, Edge, EdgeEvent,
    Node, NodeEvent, StageEvent, Validation, ValidationEvent,
)

FALLBACK_CITATIONS = [
    Citation(id="c1", doc_id="europepmc:29374183", title="Structural insight into molecular mechanism of poly(ethylene terephthalate) degradation",
             authors_short="Joo et al.", year=2018, doi="10.1038/s41467-018-02881-1", source="europepmc",
             url="https://europepmc.org/article/MED/29374183", score=0.91,
             snippet="The narrow active site cleft accommodates the aromatic substrate."),
    Citation(id="c2", doc_id="pdb:5XJH", title="Crystal structure of PETase from Ideonella sakaiensis",
             authors_short="Joo et al.", year=2018, source="pdb", url="https://www.rcsb.org/structure/5XJH",
             score=0.88, snippet="Resolución 1.54 A; EC 3.1.1.101."),
    Citation(id="c3", doc_id="zenodo:15417757", title="Activity across temperature and pH of PET hydrolase candidates",
             authors_short="Norton-Baker et al.", year=2025, doi="10.5281/zenodo.15417757", source="zenodo",
             url="https://zenodo.org/records/15417757", score=0.86,
             snippet="213 candidatos medidos a pH 4.5-8.5 y 40/60 C sobre dos sustratos."),
]


def _sample_citations() -> list[Citation]:
    """Intenta citas reales del corpus; si Databricks no responde, usa las fijas."""
    try:
        from data_pipeline.databricks_io import Databricks
        rows = Databricks().sql("""
            SELECT doc_id, title, year, doi, url, source, substr(abstract, 1, 180)
            FROM workspace.lab.documents_staging
            WHERE length(abstract) > 200 AND title ILIKE '%PET%'
            ORDER BY year DESC NULLS LAST LIMIT 3
        """)
    except Exception:
        return FALLBACK_CITATIONS
    if not rows:
        return FALLBACK_CITATIONS
    return [
        Citation(id=f"c{i+1}", doc_id=r[0], title=r[1] or "", year=int(r[2]) if r[2] else None,
                 doi=r[3] or "", url=r[4] or "", source=r[5] or "", snippet=r[6] or "",
                 score=round(0.92 - i * 0.04, 2))
        for i, r in enumerate(rows)
    ]


def _node(nid: str, ntype: str, label: str, detail: str = "", confidence: float = 1.0,
          agent_generated: bool = False, citation_ids: list[str] | None = None,
          props: list[dict[str, str]] | None = None) -> Node:
    return Node(id=nid, type=ntype, label=label, detail=detail, layer=LAYER[ntype],
                confidence=confidence, agent_generated=agent_generated,
                citation_ids=citation_ids or [], props=props or [])


async def stream(query: str, query_id: str, speed: float = 1.0) -> AsyncIterator[object]:
    """Emite la secuencia completa de eventos. speed<1 acelera (útil al desarrollar)."""
    async def pause(seconds: float) -> None:
        await asyncio.sleep(seconds * speed)

    citations = _sample_citations()
    cids = [c.id for c in citations]

    yield StageEvent(query_id=query_id, stage="received", message="Pregunta recibida", progress=0.05)
    yield NodeEvent(query_id=query_id, node=_node("q", "question", query[:48], query))
    await pause(0.6)

    yield StageEvent(query_id=query_id, stage="retrieving", message="Buscando evidencia en el corpus", progress=0.2)
    await pause(1.2)
    yield CitationsEvent(query_id=query_id, citations=citations)
    for i, c in enumerate(citations):
        nid = f"e{i+1}"
        yield NodeEvent(query_id=query_id, node=_node(
            nid, "evidence", f"{c.authors_short or c.source} {c.year or ''}".strip(), c.title,
            confidence=c.score, citation_ids=[c.id],
            props=[{"k": "fuente", "v": c.source}, {"k": "doi", "v": c.doi}]))
        yield EdgeEvent(query_id=query_id, edge=Edge(source="q", target=nid, relation="studies",
                                                     weight=c.score, citation_ids=[c.id]))
        await pause(0.45)

    yield StageEvent(query_id=query_id, stage="hypothesizing", message="Formulando hipótesis", progress=0.45)
    await pause(1.0)
    variables = [
        ("v1", "Temperatura de ensayo", "40 C frente a 60 C; el dataset mide ambas", 0.92),
        ("v2", "Hidrofobicidad de superficie", "Fracción de residuos hidrofóbicos de la secuencia", 0.78),
        ("v3", "Tipo de sustrato", "Polvo cristalino frente a film amorfo", 0.85),
    ]
    for vid, label, detail, conf in variables:
        yield NodeEvent(query_id=query_id, node=_node(vid, "variable", label, detail, conf, citation_ids=cids[:2]))
        yield EdgeEvent(query_id=query_id, edge=Edge(source=f"e{random.randint(1, len(citations))}", target=vid,
                                                     relation="supports", weight=conf, citation_ids=cids[:1]))
        await pause(0.4)

    yield NodeEvent(query_id=query_id, node=_node(
        "h1", "hypothesis", "Temperatura domina sobre composicion",
        "La temperatura de ensayo explica mas varianza de la actividad que los descriptores de secuencia.",
        confidence=0.71, agent_generated=True, citation_ids=cids))
    for vid in ("v1", "v2", "v3"):
        yield EdgeEvent(query_id=query_id, edge=Edge(source=vid, target="h1", relation="tests", weight=0.7))
    await pause(0.8)

    yield StageEvent(query_id=query_id, stage="experimenting", message="Ejecutando prueba sobre pet_activity_ml", progress=0.7)
    yield NodeEvent(query_id=query_id, node=_node(
        "x1", "experiment", "Baseline vs modelo propuesto",
        "Mismos folds agrupados por enzyme_id, early stopping, 1570 mediciones.",
        confidence=1.0, props=[{"k": "n", "v": "1570"}, {"k": "cv", "v": "5-fold agrupado"}]))
    yield EdgeEvent(query_id=query_id, edge=Edge(source="h1", target="x1", relation="tests", weight=0.9))
    await pause(2.0)

    yield NodeEvent(query_id=query_id, node=_node(
        "r1", "result", "macro-F1 0.68 +/- 0.04",
        "El modelo con temperatura supera al baseline de solo composicion (0.54 +/- 0.06).",
        confidence=0.68, agent_generated=True, citation_ids=["c3"],
        props=[{"k": "metrica", "v": "macro-F1"}, {"k": "delta", "v": "+0.14"}]))
    yield EdgeEvent(query_id=query_id, edge=Edge(source="x1", target="r1", relation="validated_by", weight=0.8))
    await pause(0.8)

    yield StageEvent(query_id=query_id, stage="validating", message="Comprobaciones de robustez", progress=0.9)
    await pause(1.0)
    validation = Validation(
        verdict="WARN", confidence=0.68,
        checks=[
            Check(name="citation_grounding", passed=True, value=1.0, threshold=0.9,
                  detail="Todas las afirmaciones tienen documento de origen"),
            Check(name="source_diversity", passed=True, value=3, threshold=2, detail="3 fuentes distintas"),
            Check(name="cv_stability", passed=True, value=0.04, threshold=0.05, detail="Desviacion entre folds"),
            Check(name="overfit_gap", passed=False, value=0.09, threshold=0.05, detail="Gap train/val del 9%"),
        ],
        warnings=["Gap train/val del 9%: posible sobreajuste", "Dataset pequeno: 213 enzimas"])
    yield ValidationEvent(query_id=query_id, validation=validation)
    await pause(0.5)

    yield AnswerEvent(query_id=query_id, answer=Answer(
        headline="La temperatura de ensayo pesa mas que la composicion de secuencia",
        conclusion="En las 1570 mediciones disponibles, incluir la temperatura sube el macro-F1 de 0.54 a 0.68.",
        justification="Validacion cruzada 5-fold agrupada por enzima para evitar fuga entre condiciones. "
                      "El gap train/val del 9% supera el umbral, asi que el veredicto es WARN y no PASS.",
        tts_text="La temperatura del ensayo predice la actividad mejor que la composicion de la secuencia, "
                 "pero el modelo muestra senales de sobreajuste."))
    yield DoneEvent(query_id=query_id, latency_ms=int(9000 * speed),
                    next_question="Anadir descriptores estructurales del sitio activo y repetir la comparacion")

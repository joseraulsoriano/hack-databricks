"""Herramientas de datos para los agentes de Omnigent (tools de tipo `function`).

El agente puede ampliar el corpus (fetch_sources), pero lo que trae cae en documents_staging:
solo entra al RAG cuando la persona curadora lo aprueba y lo pasa a documents_curated.
"""

import json

from data_pipeline import ingest
from data_pipeline.databricks_io import Databricks

AGENT_SOURCES = {"europepmc", "openalex", "pdb", "zenodo"}
MAX_AGENT_LIMIT = 100

_db: Databricks | None = None


def _databricks() -> Databricks:
    global _db
    if _db is None:
        _db = Databricks()
    return _db


def fetch_sources(query: str, sources: str = "europepmc,openalex", limit: int = 25, agent: str = "literature_agent") -> str:
    """Busca documentos nuevos en fuentes científicas abiertas y los propone para curación.

    query: consulta de búsqueda (admite OR y frases entre comillas).
    sources: lista separada por comas de europepmc, openalex, pdb, zenodo.
    limit: máximo de documentos por fuente (tope 100).
    Devuelve un resumen JSON con los doc_id añadidos a workspace.lab.documents_staging.
    """
    chosen = [s.strip() for s in sources.split(",") if s.strip() in AGENT_SOURCES]
    if not chosen:
        return json.dumps({"error": f"sources debe incluir alguna de {sorted(AGENT_SOURCES)}"})
    limit = max(1, min(limit, MAX_AGENT_LIMIT))
    writer = ingest.run(
        chosen, limit, full_text=True, download_structures=False, fetched_by=f"agent:{agent}",
        literature_query=query, pdb_terms=(query,),
    )
    ingest.publish(writer, apply_schema=False, profile=None, warehouse_id=None)
    doc_ids = [json.loads(line)["doc_id"] for f in sorted((writer.dir / "staging").glob("*.jsonl")) for line in f.open()]
    return json.dumps({"run_id": writer.run_id, "added": dict(writer.counts), "doc_ids": doc_ids[:50],
                       "status": "pending_curation"}, ensure_ascii=False)


def search_staging(text: str, limit: int = 10) -> str:
    """Busca por texto en el corpus descargado (título y resumen) mientras el índice RAG no existe.

    Devuelve JSON con doc_id, título, año, fuente, DOI y un fragmento del resumen para citar.
    """
    rows = _databricks().sql(
        """
        SELECT doc_id, title, year, source, doi, substr(abstract, 1, 400)
        FROM workspace.lab.documents_staging
        WHERE title ILIKE concat('%', :q, '%') OR abstract ILIKE concat('%', :q, '%')
        ORDER BY year DESC NULLS LAST
        LIMIT """ + str(max(1, min(limit, 50))),
        params={"q": text},
    )
    keys = ("doc_id", "title", "year", "source", "doi", "snippet")
    return json.dumps([dict(zip(keys, r)) for r in rows], ensure_ascii=False)


def staging_stats() -> str:
    """Resumen del corpus: documentos por fuente y tipo, cuántos con texto completo y cuántos ya curados."""
    db = _databricks()
    by_source = db.sql("""
        SELECT source, doc_type, count(*), count_if(length(full_text) > 0), count(DISTINCT doi)
        FROM workspace.lab.documents_staging GROUP BY ALL ORDER BY 1, 2
    """)
    curated = db.sql("SELECT count(DISTINCT doc_id) FROM workspace.lab.documents_curated")
    return json.dumps({
        "staging": [dict(zip(("source", "doc_type", "docs", "with_full_text", "distinct_doi"), r)) for r in by_source],
        "curated_docs": curated[0][0] if curated else "0",
    }, ensure_ascii=False)

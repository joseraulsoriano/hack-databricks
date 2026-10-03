"""OpenAlex: grafo de publicaciones y citas (artículos, tesis, preprints)."""

import os
from collections.abc import Iterator

from data_pipeline.connectors.base import Http
from data_pipeline.schema import Document, Fetched, dump_metadata, normalize_doi

API = "https://api.openalex.org/works"
DOC_TYPES = {"dissertation": "thesis", "preprint": "preprint", "dataset": "dataset"}


def _abstract(inverted: dict[str, list[int]] | None) -> str:
    if not inverted:
        return ""
    positions = {pos: word for word, idxs in inverted.items() for pos in idxs}
    return " ".join(positions[i] for i in sorted(positions))


def search(query: str, limit: int | None = None, work_type: str | None = None, http: Http | None = None) -> Iterator[Fetched]:
    """work_type filtra por tipo de OpenAlex, p. ej. 'dissertation' para quedarse solo con tesis."""
    http = http or Http(min_interval=0.12)
    params = {"search": query, "per-page": 200, "cursor": "*"}
    if work_type:
        params["filter"] = f"type:{work_type}"
    # OpenAlex da más cuota con un correo de contacto; solo se envía si el equipo lo configura.
    if os.environ.get("OPENALEX_MAILTO"):
        params["mailto"] = os.environ["OPENALEX_MAILTO"]
    seen = 0
    while True:
        data = http.get_json(API, params=params)
        for w in data.get("results", []):
            source_id = w["id"].rsplit("/", 1)[-1]
            loc = w.get("best_oa_location") or w.get("primary_location") or {}
            oa = w.get("open_access") or {}
            doc = Document(
                doc_id=f"openalex:{source_id}",
                source="openalex",
                source_id=source_id,
                doc_type=DOC_TYPES.get(w.get("type", ""), "article"),
                title=w.get("display_name") or "",
                authors=[a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author", {}).get("display_name")],
                year=w.get("publication_year"),
                doi=normalize_doi(w.get("doi")),
                url=oa.get("oa_url") or loc.get("landing_page_url") or w["id"],
                license=loc.get("license") or "",
                is_open_access=oa.get("is_oa"),
                abstract=_abstract(w.get("abstract_inverted_index")),
                metadata=dump_metadata(
                    openalex_type=w.get("type"), cited_by=w.get("cited_by_count"),
                    venue=(loc.get("source") or {}).get("display_name"), pdf_url=loc.get("pdf_url"),
                    institutions=sorted({i["display_name"] for a in w.get("authorships", []) for i in a.get("institutions", []) if i.get("display_name")}),
                    topics=[t["display_name"] for t in w.get("topics", [])[:5]],
                    referenced_works=len(w.get("referenced_works", [])),
                ),
                query=query,
            ).finalize()
            yield Fetched(doc=doc, raw=w)
            seen += 1
            if limit is not None and seen >= limit:
                return
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            return
        params["cursor"] = cursor

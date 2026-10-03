"""Zenodo: datasets publicados (p. ej. Tsuboyama 2023, estabilidad de ~776k mutantes)."""

import re
from collections.abc import Iterable, Iterator

from data_pipeline.connectors.base import Http
from data_pipeline.schema import Document, Fetched, dump_metadata, normalize_doi

API = "https://zenodo.org/api/records"
# Mega-scale experimental analysis of protein folding stability (Tsuboyama et al., Nature 2023).
MEGASCALE_STABILITY = "7992926"


def _to_fetched(rec: dict, query: str) -> Fetched:
    md = rec.get("metadata", {})
    rid = str(rec["id"])
    files = [{"key": f.get("key"), "size": f.get("size"), "url": (f.get("links") or {}).get("self")} for f in rec.get("files", [])]
    date = md.get("publication_date", "")
    doc = Document(
        doc_id=f"zenodo:{rid}",
        source="zenodo",
        source_id=rid,
        doc_type=(md.get("resource_type") or {}).get("type", "dataset"),
        title=md.get("title", ""),
        authors=[c.get("name", "") for c in md.get("creators", [])],
        year=int(date[:4]) if date[:4].isdigit() else None,
        doi=normalize_doi(rec.get("doi") or md.get("doi")),
        url=(rec.get("links") or {}).get("self_html") or f"https://zenodo.org/records/{rid}",
        license=(md.get("license") or {}).get("id", ""),
        is_open_access=(md.get("access_right") == "open"),
        abstract=re.sub(r"<[^>]+>", " ", md.get("description", "")).strip(),
        metadata=dump_metadata(files=files, keywords=md.get("keywords"), version=md.get("version")),
        query=query,
    ).finalize()
    return Fetched(doc=doc, raw=rec)


def search(query: str, limit: int | None = None, http: Http | None = None) -> Iterator[Fetched]:
    http = http or Http(min_interval=0.5)
    page, seen = 1, 0
    while True:
        # Sin token, Zenodo limita el tamaño de página a 25.
        hits = http.get_json(API, params={"q": query, "size": 25, "page": page}).get("hits", {}).get("hits", [])
        if not hits:
            return
        for rec in hits:
            yield _to_fetched(rec, query)
            seen += 1
            if limit is not None and seen >= limit:
                return
        page += 1


def fetch_records(record_ids: Iterable[str], query: str = "", http: Http | None = None) -> Iterator[Fetched]:
    http = http or Http(min_interval=0.5)
    for rid in record_ids:
        yield _to_fetched(http.get_json(f"{API}/{rid}"), query)

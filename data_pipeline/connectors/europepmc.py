"""Europe PMC: literatura biomédica, con texto completo para el subconjunto de acceso abierto."""

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from data_pipeline.connectors.base import Http
from data_pipeline.schema import Document, Fetched, dump_metadata, normalize_doi

API = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _full_text(http: Http, pmcid: str) -> str:
    """Cuerpo del artículo en texto plano, con títulos de sección y tablas (donde suelen estar los mutantes)."""
    try:
        xml = http.request("GET", f"{API}/{pmcid}/fullTextXML").text
        root = ET.fromstring(xml)
    except Exception:
        return ""
    body = root.find(".//body")
    if body is None:
        return ""
    parts: list[str] = []
    for el in body.iter():
        if el.tag == "title" and el.text:
            parts.append(f"\n## {el.text.strip()}\n")
        elif el.tag in ("p", "table-wrap"):
            text = " ".join(t.strip() for t in el.itertext() if t.strip())
            if text:
                parts.append(text)
    return "\n".join(parts)


def search(query: str, limit: int | None = None, full_text: bool = True, http: Http | None = None) -> Iterator[Fetched]:
    http = http or Http(min_interval=0.1)
    cursor, seen = "*", 0
    while True:
        page_size = 1000 if limit is None else min(1000, limit - seen)
        data = http.get_json(f"{API}/search", params={
            "query": query, "format": "json", "resultType": "core", "pageSize": page_size, "cursorMark": cursor,
        })
        results = data.get("resultList", {}).get("result", [])
        if not results:
            return
        texts: dict[str, str] = {}
        if full_text:
            pmcids = [r["pmcid"] for r in results if r.get("pmcid") and r.get("isOpenAccess") == "Y"]
            with ThreadPoolExecutor(max_workers=6) as pool:
                texts = dict(zip(pmcids, pool.map(lambda p: _full_text(Http(), p), pmcids)))
        for r in results:
            source_id = r.get("pmid") or r.get("pmcid") or r["id"]
            pub_types = r.get("pubTypeList", {}).get("pubType", [])
            is_preprint = r.get("source") == "PPR" or any("preprint" in t.lower() for t in pub_types)
            authors = [a.get("fullName", "") for a in r.get("authorList", {}).get("author", []) if a.get("fullName")]
            doc = Document(
                doc_id=f"europepmc:{source_id}",
                source="europepmc",
                source_id=source_id,
                doc_type="preprint" if is_preprint else "article",
                title=r.get("title", ""),
                authors=authors,
                year=int(r["pubYear"]) if r.get("pubYear", "").isdigit() else None,
                doi=normalize_doi(r.get("doi")),
                url=f"https://europepmc.org/article/{r.get('source', 'MED')}/{r['id']}",
                license=r.get("license", ""),
                is_open_access=r.get("isOpenAccess") == "Y",
                abstract=r.get("abstractText", ""),
                full_text=texts.get(r.get("pmcid", ""), ""),
                metadata=dump_metadata(
                    pmid=r.get("pmid"), pmcid=r.get("pmcid"), journal=r.get("journalInfo", {}).get("journal", {}).get("title"),
                    pub_types=pub_types, cited_by=r.get("citedByCount"),
                    mesh=[m.get("descriptorName") for m in r.get("meshHeadingList", {}).get("meshHeading", [])],
                    keywords=r.get("keywordList", {}).get("keyword", []),
                ),
                query=query,
            ).finalize()
            yield Fetched(doc=doc, raw=r)
            seen += 1
            if limit is not None and seen >= limit:
                return
        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            return
        cursor = next_cursor

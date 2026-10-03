"""RCSB PDB: estructuras cristalinas, con mutaciones declaradas por entidad y mapeo a UniProt."""

from collections.abc import Iterator

from data_pipeline.connectors.base import Http
from data_pipeline.schema import Document, Fetched, dump_metadata, normalize_doi

SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
GRAPHQL = "https://data.rcsb.org/graphql"
CIF_URL = "https://files.rcsb.org/download/{}.cif"

ENTRY_FIELDS = """
  rcsb_id
  struct { title pdbx_descriptor }
  rcsb_accession_info { initial_release_date }
  exptl { method }
  rcsb_entry_info { resolution_combined }
  rcsb_primary_citation { title year pdbx_database_id_DOI pdbx_database_id_PubMed rcsb_authors journal_abbrev }
  polymer_entities {
    rcsb_polymer_entity { pdbx_description pdbx_mutation pdbx_ec }
    entity_poly { pdbx_seq_one_letter_code_can }
    rcsb_polymer_entity_container_identifiers { reference_sequence_identifiers { database_name database_accession } }
    rcsb_entity_source_organism { scientific_name }
  }
"""


def search_ids(query: str, http: Http, limit: int | None = None) -> list[str]:
    payload = {
        "query": {"type": "terminal", "service": "full_text", "parameters": {"value": query}},
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": limit or 10000}},
    }
    return [r["identifier"] for r in http.post_json(SEARCH, payload).get("result_set", [])]


def fetch_entries(ids: list[str], http: Http) -> list[dict]:
    entries: list[dict] = []
    for i in range(0, len(ids), 50):
        batch = ", ".join(f'"{x}"' for x in ids[i:i + 50])
        data = http.post_json(GRAPHQL, {"query": f"{{ entries(entry_ids: [{batch}]) {{ {ENTRY_FIELDS} }} }}"})
        entries.extend(e for e in data.get("data", {}).get("entries") or [] if e)
    return entries


def uniprot_accessions(entry: dict) -> list[str]:
    accs = {
        ref["database_accession"]
        for ent in entry.get("polymer_entities") or []
        for ref in (ent.get("rcsb_polymer_entity_container_identifiers") or {}).get("reference_sequence_identifiers") or []
        if ref.get("database_name") == "UniProt"
    }
    return sorted(accs)


def to_fetched(entry: dict, query: str) -> Fetched:
    pdb_id = entry["rcsb_id"]
    cite = entry.get("rcsb_primary_citation") or {}
    entities = entry.get("polymer_entities") or []
    entity_rows = [
        {
            "description": (e.get("rcsb_polymer_entity") or {}).get("pdbx_description"),
            "mutation": (e.get("rcsb_polymer_entity") or {}).get("pdbx_mutation"),
            "ec": (e.get("rcsb_polymer_entity") or {}).get("pdbx_ec"),
            "organism": [o.get("scientific_name") for o in e.get("rcsb_entity_source_organism") or []],
            "sequence": (e.get("entity_poly") or {}).get("pdbx_seq_one_letter_code_can"),
        }
        for e in entities
    ]
    mutations = [row["mutation"] for row in entity_rows if row["mutation"]]
    title = (entry.get("struct") or {}).get("title") or ""
    # Texto indexable: lo que un científico leería en la ficha de la entrada.
    summary = "\n".join(filter(None, [
        title,
        f"Primary citation: {cite.get('title')}" if cite.get("title") else "",
        f"Mutations: {'; '.join(mutations)}" if mutations else "",
        f"Entities: {'; '.join(r['description'] or '' for r in entity_rows)}",
    ]))
    release = (entry.get("rcsb_accession_info") or {}).get("initial_release_date") or ""
    doc = Document(
        doc_id=f"pdb:{pdb_id}",
        source="pdb",
        source_id=pdb_id,
        doc_type="structure",
        title=title,
        authors=cite.get("rcsb_authors") or [],
        year=int(release[:4]) if release[:4].isdigit() else cite.get("year"),
        doi=normalize_doi(cite.get("pdbx_database_id_DOI")),
        url=f"https://www.rcsb.org/structure/{pdb_id}",
        license="CC0-1.0",
        is_open_access=True,
        abstract=summary,
        metadata=dump_metadata(
            method=[m.get("method") for m in entry.get("exptl") or []],
            resolution=(entry.get("rcsb_entry_info") or {}).get("resolution_combined"),
            uniprot=uniprot_accessions(entry), mutations=mutations, entities=entity_rows,
            citation_pubmed=cite.get("pdbx_database_id_PubMed"), journal=cite.get("journal_abbrev"),
            cif_url=CIF_URL.format(pdb_id),
        ),
        query=query,
    ).finalize()
    return Fetched(doc=doc, raw=entry)


def search(query: str, limit: int | None = None, http: Http | None = None) -> Iterator[Fetched]:
    http = http or Http()
    for entry in fetch_entries(search_ids(query, http, limit), http):
        yield to_fetched(entry, query)


def download_cif(pdb_id: str, http: Http | None = None) -> bytes:
    return (http or Http()).request("GET", CIF_URL.format(pdb_id)).content

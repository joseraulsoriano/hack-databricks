"""AlphaFold DB: estructuras predichas por accesión UniProt (el vínculo con el Nobel 2024)."""

from collections.abc import Iterable, Iterator

import httpx

from data_pipeline.connectors.base import Http
from data_pipeline.schema import Document, Fetched, dump_metadata

API = "https://alphafold.ebi.ac.uk/api/prediction/{}"
# PETasa de Ideonella sakaiensis, la enzima de referencia del nicho.
IS_PETASE = "A0A0K8P6T7"


def fetch(accessions: Iterable[str], query: str = "", http: Http | None = None) -> Iterator[Fetched]:
    http = http or Http(min_interval=0.1)
    for acc in dict.fromkeys(accessions):
        try:
            predictions = http.get_json(API.format(acc))
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:  # sin predicción para esa accesión
                continue
            raise
        for p in predictions:
            entry_id = p["entryId"]
            description = p.get("uniprotDescription", "")
            organism = p.get("organismScientificName", "")
            doc = Document(
                doc_id=f"alphafold:{entry_id}",
                source="alphafold",
                source_id=entry_id,
                doc_type="prediction",
                title=f"AlphaFold prediction {entry_id}: {description} ({organism})",
                year=int(p["modelCreatedDate"][:4]) if p.get("modelCreatedDate") else None,
                url=f"https://alphafold.ebi.ac.uk/entry/{acc}",
                license="CC-BY-4.0",
                is_open_access=True,
                abstract=f"{description}. Organism: {organism}. Mean pLDDT: {p.get('globalMetricValue')}. "
                         f"Sequence length: {len(p.get('uniprotSequence') or p.get('sequence') or '')}.",
                metadata=dump_metadata(
                    uniprot=acc, gene=p.get("gene"), organism=organism, plddt=p.get("globalMetricValue"),
                    model_version=p.get("latestVersion"), pdb_url=p.get("pdbUrl"), cif_url=p.get("cifUrl"),
                    pae_url=p.get("paeDocUrl"), sequence=p.get("uniprotSequence") or p.get("sequence"),
                ),
                query=query,
            ).finalize()
            yield Fetched(doc=doc, raw=p)

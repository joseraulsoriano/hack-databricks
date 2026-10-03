"""Esquema común de documentos: una fila de workspace.lab.documents_staging."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class Document(BaseModel):
    doc_id: str
    source: str
    source_id: str
    doc_type: str
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str = ""
    url: str = ""
    license: str = ""
    is_open_access: bool | None = None
    abstract: str = ""
    full_text: str = ""
    raw_path: str = ""
    metadata: str = "{}"
    content_hash: str = ""
    query: str = ""
    fetched_by: str = "ingest_cli"
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def finalize(self) -> "Document":
        body = f"{self.title}\n{self.abstract}\n{self.full_text}".encode()
        self.content_hash = hashlib.sha256(body).hexdigest()
        return self


class Fetched(BaseModel):
    """Documento normalizado más el payload original de la fuente, que se guarda intacto en raw."""

    doc: Document
    raw: Any


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi


def dump_metadata(**fields: Any) -> str:
    return json.dumps({k: v for k, v in fields.items() if v not in (None, "", [], {})}, ensure_ascii=False)

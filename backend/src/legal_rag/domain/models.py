from datetime import datetime

from pydantic import BaseModel, Field


class DocumentRecord(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    number: str = Field(min_length=3, max_length=100)
    kind: str = Field(min_length=2, max_length=100)
    text: str = Field(min_length=30, max_length=1_000_000)
    source_url: str
    source_kind: str = "unknown"
    source_document_id: str = ""
    reported_status: str = "unknown"
    verified_at: datetime | None = None
    authority: str = ""
    scope: str = "central"
    effective_from: str | None = None
    effective_to: str | None = None
    effective_from_evidence: str | None = None
    effective_to_evidence: str | None = None
    issued_at: str | None = None
    license_evidence: str | None = None
    relations: list[dict] = Field(default_factory=list, max_length=100)

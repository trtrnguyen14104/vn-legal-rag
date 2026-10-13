from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

Intent = Literal[
    "legal_lookup", "legal_explanation", "compare_versions", "out_of_scope", "clarification_needed"
]
Status = Literal["answered", "clarification_needed", "insufficient_evidence", "out_of_scope", "unavailable"]


class QueryInput(BaseModel):
    question: str = Field(min_length=3, max_length=4000)
    conversation_id: UUID | None = None
    as_of_date: date | None = None
    version_ids: list[UUID] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def validate_versions(self):
        self.question = self.question.strip()
        if len(self.question) < 3:
            raise ValueError("question is too short")
        if len(self.version_ids) == 1 or len(set(self.version_ids)) != len(self.version_ids):
            raise ValueError("comparison requires two different version ids")
        return self


class Claim(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class GeneratedAnswer(BaseModel):
    # Render these claims directly. There is no separate unchecked free-form answer.
    claims: list[Claim] = Field(min_length=1, max_length=12)


class Citation(BaseModel):
    source_id: str
    document_id: str
    version_id: str
    title: str
    number: str
    locator: str
    quote: str
    url: str
    verified_at: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    temporal_status: str = "unknown"


class QueryResult(BaseModel):
    intent: Intent
    status: Status
    # Internal diagnostic persisted by the worker, never included in API results.
    error_code: str | None = Field(default=None, exclude=True)
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    clarification: str | None = None
    as_of_date: date
    corpus_snapshot_id: str | None = None
    freshness: dict = Field(default_factory=dict)
    comparison: list[dict] = Field(default_factory=list)

from datetime import date

from pydantic import BaseModel, Field


class FeedbackInput(BaseModel):
    helpful: bool
    comment: str = Field(default="", max_length=1000)


class RunInput(BaseModel):
    source: str = Field(pattern=r"^(vanban|congbao|hf|local)$")


class ReviewInput(BaseModel):
    approved: bool
    evidence: str = Field(min_length=10, max_length=4000)
    effective_from: date | None = None
    effective_to: date | None = None

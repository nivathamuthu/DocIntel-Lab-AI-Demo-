"""Schemas for the versioned prompts. Module 5 will store approved rules; this is the extraction shape."""
from pydantic import BaseModel, Field


class AnswerOutput(BaseModel):
    """A grounded answer, or an explicit unknown."""

    answer: str = Field(min_length=1, max_length=800)
    known: bool
    citation_indexes: list[int] = Field(default_factory=list)


class RuleOutput(BaseModel):
    """One rule found in a chunk, or found=false when the chunk states none."""

    found: bool
    condition: str = ""
    action: str = ""
    exception: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


SCHEMAS = {
    "answer_with_citations": AnswerOutput,
    "extract_single_rule": RuleOutput,
}

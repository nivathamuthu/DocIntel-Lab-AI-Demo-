"""What the Q&A generator and its judges must return."""
from typing import Literal

from pydantic import BaseModel, Field

QType = Literal["factual", "multi_hop", "unanswerable"]
UNANSWERABLE = "unanswerable"


class QAItem(BaseModel):
    qtype: QType
    question: str = Field(min_length=8, max_length=300)
    answer: str = Field(min_length=1, max_length=600)
    supporting_quotes: list[str] = Field(min_length=1, max_length=2)


class QASet(BaseModel):
    items: list[QAItem] = Field(default_factory=list, max_length=8)


class Groundedness(BaseModel):
    score: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=400)


class AnswerJudgment(BaseModel):
    correct: bool
    reason: str = Field(min_length=1, max_length=300)

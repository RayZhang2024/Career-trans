from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.career_assessment import AlignmentConfidence


class Recommendation(StrEnum):
    APPLY = "apply"
    CONSIDER = "consider"
    SKIP = "skip"


class RecommendationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: Recommendation
    fit_score: float = Field(ge=0.0, le=100.0)
    career_alignment_score: float = Field(ge=0.0, le=100.0)
    career_alignment_confidence: AlignmentConfidence
    rule_id: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
    key_strengths: list[str] = Field(default_factory=list)
    key_tradeoffs: list[str] = Field(default_factory=list)
    hard_blockers: list[int] = Field(default_factory=list)

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CareerAlignmentDimension(StrEnum):
    TARGET_ROLE = "target_role"
    CAPABILITY_GROWTH = "capability_growth"
    INDUSTRY_DOMAIN = "industry_domain"
    SENIORITY_PROGRESSION = "seniority_progression"
    LONG_TERM_OPTIONALITY = "long_term_optionality"
    PREFERENCE_CONSTRAINT = "preference_constraint"


class AlignmentConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CareerDimensionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: CareerAlignmentDimension
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)


class CareerAlignmentJudgement(BaseModel):
    """Semantic alignment output before deterministic score aggregation."""

    model_config = ConfigDict(extra="forbid")

    confidence: AlignmentConfidence
    dimensions: list[CareerDimensionAssessment]
    strategic_strengths: list[str] = Field(default_factory=list)
    strategic_tradeoffs: list[str] = Field(default_factory=list)
    reasoning: str = Field(min_length=1)


class CareerAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    career_alignment_score: float = Field(ge=0.0, le=100.0)
    confidence: AlignmentConfidence
    dimensions: list[CareerDimensionAssessment]
    strategic_strengths: list[str] = Field(default_factory=list)
    strategic_tradeoffs: list[str] = Field(default_factory=list)
    reasoning: str = Field(min_length=1)

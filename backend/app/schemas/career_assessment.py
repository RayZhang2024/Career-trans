from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import CandidateCareerProfile


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


class CareerAlignmentJobProfile(BaseModel):
    """Purpose-specific job view for strategic career alignment."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company: str | None = None
    location: str | None = None
    work_arrangement: str | None = None
    seniority: str | None = None
    salary: str | None = None
    employment_type: str | None = None
    application_deadline: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    domain_knowledge: list[str] = Field(default_factory=list)
    security_requirements: list[str] = Field(default_factory=list)
    work_authorization_requirements: list[str] = Field(default_factory=list)


class CareerAlignmentFitSummary(BaseModel):
    """Counts and headline scores only; detailed requirement data stays local."""

    model_config = ConfigDict(extra="forbid")

    fit_score: float = Field(ge=0.0, le=100.0)
    essential_score: float | None = Field(default=None, ge=0.0, le=100.0)
    desirable_score: float | None = Field(default=None, ge=0.0, le=100.0)
    strength_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    hard_blocker_count: int = Field(ge=0)


class CareerAlignmentInput(BaseModel):
    """Compact semantic input that leaves full profiles in deterministic code."""

    model_config = ConfigDict(extra="forbid")

    job_profile: CareerAlignmentJobProfile
    candidate_context: CandidateCareerProfile
    fit_assessment: CareerAlignmentFitSummary

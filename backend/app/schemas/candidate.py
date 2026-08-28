from pydantic import BaseModel, ConfigDict, Field


class CareerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)


class CandidateEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_authorisation: list[str] = Field(default_factory=list)
    security_clearances: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class CandidateAdviserContext(BaseModel):
    """Bounded confirmed adviser interpretation for strategic stages only."""

    model_config = ConfigDict(extra="forbid")

    professional_identity: str = ""
    career_strategy_summary: str = ""
    job_search_strategy_summary: str = ""
    role_hypotheses: list[str] = Field(default_factory=list)
    development_priorities: list[str] = Field(default_factory=list)


class CandidateContext(BaseModel):
    """User-agnostic candidate context consumed by matching workflows.

    Production versions of this schema are populated from authenticated user data.
    Adviser context is deliberately separated so requirement matching can exclude
    semantic adviser inference while discovery/career stages can use it.
    """

    model_config = ConfigDict(extra="forbid")

    source_name: str | None = None
    profile_text: str = ""
    skills_text: str = ""
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)
    adviser: CandidateAdviserContext = Field(default_factory=CandidateAdviserContext)
    evidence: list[CareerEvidence] = Field(default_factory=list)


class CandidateContextSummary(BaseModel):
    """Safe readiness diagnostic for confirmed persisted candidate context."""

    model_config = ConfigDict(extra="forbid")

    ready: bool
    employment_count: int = Field(ge=0)
    education_count: int = Field(ge=0)
    skill_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    career_strategy_configured: bool = False
    job_search_criteria_configured: bool = False


class CandidateSearchProfile(BaseModel):
    """Small, purpose-built candidate context for job relevance/discovery screening."""

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    skills: list[str] = Field(default_factory=list)
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""
    adviser: CandidateAdviserContext = Field(default_factory=CandidateAdviserContext)


class CandidateCareerProfile(BaseModel):
    """Small, purpose-built candidate context for career-alignment assessment."""

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)
    adviser: CandidateAdviserContext = Field(default_factory=CandidateAdviserContext)


class CandidateMatchingProfile(BaseModel):
    """Evidence-limited candidate context for semantic requirement matching.

    Adviser interpretation is intentionally absent from this schema.
    """

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    skills: list[str] = Field(default_factory=list)
    evidence: list[CareerEvidence] = Field(default_factory=list)

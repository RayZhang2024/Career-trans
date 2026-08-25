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

class CandidateContext(BaseModel):
    """User-agnostic candidate context consumed by matching workflows.

    Production versions of this schema will be populated from authenticated user data.
    Repository demo profiles may be loaded into the same schema for development/tests.
    """

    model_config = ConfigDict(extra="forbid")

    source_name: str | None = None
    profile_text: str = ""
    skills_text: str = ""
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)
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
    """Small, purpose-built candidate context for job relevance screening."""

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    skills: list[str] = Field(default_factory=list)
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""


class CandidateCareerProfile(BaseModel):
    """Small, purpose-built candidate context for career-alignment assessment."""

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)


class CandidateMatchingProfile(BaseModel):
    """Evidence-limited candidate context for semantic requirement matching."""

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    skills: list[str] = Field(default_factory=list)
    evidence: list[CareerEvidence] = Field(default_factory=list)

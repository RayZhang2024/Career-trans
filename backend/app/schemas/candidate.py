from pydantic import BaseModel, ConfigDict, Field


class CareerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)


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
    evidence: list[CareerEvidence] = Field(default_factory=list)

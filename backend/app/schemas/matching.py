from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import CandidateContext
from app.schemas.job import JobProfile, JobRequirement


class MatchType(StrEnum):
    DEMONSTRATED = "demonstrated"
    TRANSFERABLE = "transferable"
    INFERRED = "inferred"
    MISSING = "missing"


class RequirementMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_index: int = Field(ge=0)
    requirement: JobRequirement
    match_type: MatchType
    score: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    reasoning: str = Field(min_length=1)


class RequirementMatchSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[RequirementMatch] = Field(default_factory=list)


class JobMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_profile: JobProfile
    candidate_context: CandidateContext


class JobMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[RequirementMatch]

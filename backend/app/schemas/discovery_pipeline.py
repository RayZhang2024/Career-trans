from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import CandidateContext
from app.schemas.discovery import DiscoveredJobState, JobDiscoveryResponse, JobSearchQuery
from app.schemas.job_ranking import JobRankingResponse
from app.schemas.job_sources import CompanySourceResolution, CompanyTarget


class DiscoverAndRankRequest(BaseModel):
    """Resolve requested public ATS sources, persist the refresh, then rank its matches."""

    model_config = ConfigDict(extra="forbid")

    companies: list[CompanyTarget] = Field(min_length=1)
    query: JobSearchQuery
    candidate_context: CandidateContext
    max_semantic_candidates: int = Field(default=30, ge=1, le=100)
    max_full_analyses: int = Field(default=10, ge=1, le=30)
    min_relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)


class DiscoveryLifecycleCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new: int = 0
    updated: int = 0
    unchanged: int = 0
    inactive: int = 0


class DiscoverAndRankResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolutions: list[CompanySourceResolution]
    discovery: JobDiscoveryResponse
    job_states: dict[str, DiscoveredJobState] = Field(default_factory=dict)
    lifecycle_counts: DiscoveryLifecycleCounts = Field(default_factory=DiscoveryLifecycleCounts)
    ranking: JobRankingResponse

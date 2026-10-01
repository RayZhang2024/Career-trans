from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.discovery import JobSearchQuery
from app.schemas.job_ranking import RankedJobOpportunity
from app.schemas.job_ranking import PostingLegitimacyAssessment
from app.schemas.semantic_runtime_attribution import SemanticRuntimeAttribution
from app.schemas.user_job_decision import UserJobDecisionRead


class DiscoveryRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"


class DiscoveryRunJobOutcome(StrEnum):
    NEWLY_EVALUATED = "newly_evaluated"
    REUSED_EVALUATION = "reused_evaluation"
    NOT_ACTIONABLE = "not_actionable"
    PRESEMANTIC_FILTERED = "presemantic_filtered"
    OUTSIDE_SEMANTIC_BUDGET = "outside_semantic_budget"
    SEMANTIC_REJECTED = "semantic_rejected"
    OUTSIDE_DEEP_ANALYSIS_BUDGET = "outside_deep_analysis_budget"
    ANALYSIS_FAILED = "analysis_failed"


class DiscoveryRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: JobSearchQuery
    client_request_id: UUID | None = None
    discovered_job_ids: list[str] = Field(min_length=1, max_length=100)
    max_semantic_candidates: int = Field(default=10, ge=1, le=100)
    max_full_analyses: int = Field(default=5, ge=1, le=30)
    min_relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)


class DiscoveryRunJobRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    discovered_job_id: str
    evaluation_id: str | None = None
    outcome: DiscoveryRunJobOutcome
    failure_stage: str | None = None
    failure_kind: str | None = None
    opportunity: RankedJobOpportunity | None = None


class DiscoveryRunRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: DiscoveryRunStatus
    search_input_fingerprint: str
    candidate_evaluation_fingerprint: str
    evaluation_contract_fingerprint: str
    run_input: dict[str, object] = Field(default_factory=dict)
    funnel: dict[str, int] = Field(default_factory=dict)
    failure_summary: dict[str, int] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime | None = None
    jobs: list[DiscoveryRunJobRead] = Field(default_factory=list)


class UserOpportunityRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_id: str
    discovered_job_id: str
    opportunity: RankedJobOpportunity


class UserOpportunityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opportunities: list[UserOpportunityRead] = Field(default_factory=list)


class UserOpportunitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_id: str
    discovered_job_id: str
    recommendation: str
    title: str
    company: str | None = None
    location: str | None = None
    work_arrangement: str | None = None
    fit_score: float
    career_alignment_score: float
    career_alignment_confidence: str
    relevance_score: float
    archetype: str
    url: str
    posting_recency: PostingLegitimacyAssessment
    decision: UserJobDecisionRead


class UserOpportunitySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[UserOpportunitySummary] = Field(default_factory=list)
    limit: int
    truncated: bool


class DiscoveryRunSummaryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: DiscoveryRunStatus
    run_input: dict[str, object] = Field(default_factory=dict)
    funnel: dict[str, int] = Field(default_factory=dict)
    failure_summary: dict[str, int] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime | None = None


class DiscoveryRunSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[DiscoveryRunSummaryRead] = Field(default_factory=list)
    limit: int
    truncated: bool


class DiscoveryRunJobSummaryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    discovered_job_id: str
    evaluation_id: str | None = None
    outcome: DiscoveryRunJobOutcome
    failure_stage: str | None = None
    failure_kind: str | None = None
    opportunity: UserOpportunitySummary | None = None


class DiscoveryRunDetailRead(DiscoveryRunSummaryRead):
    jobs: list[DiscoveryRunJobSummaryRead] = Field(default_factory=list)


class DiscoveryRunJobDetailRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    discovered_job_id: str
    evaluation_id: str | None = None
    outcome: DiscoveryRunJobOutcome
    failure_stage: str | None = None
    failure_kind: str | None = None
    opportunity: RankedJobOpportunity | None = None
    runtime_attribution: SemanticRuntimeAttribution | None = None

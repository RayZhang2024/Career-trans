from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.job_ranking import RankedJobOpportunity
from app.schemas.semantic_runtime_attribution import SemanticRuntimeAttribution
from app.schemas.user_job_decision import UserJobDecisionRead
from app.schemas.application_preparation import ApplicationTargetKind
from app.schemas.application_tracking import ApplicationTrackingStatus


class WorkspaceCurrentFitStatus(StrEnum):
    CURRENT = "current"
    NONE = "none"
    UNAVAILABLE = "unavailable"


class WorkspaceCurrentFitReason(StrEnum):
    NO_CURRENT_EVALUATION = "no_current_evaluation"
    JOB_NOT_ACTIONABLE = "job_not_actionable"
    CANDIDATE_NOT_READY = "candidate_not_ready"
    CANDIDATE_EVIDENCE_INCOMPLETE = "candidate_evidence_incomplete"
    RUNTIME_CONFIGURATION_UNAVAILABLE = "runtime_configuration_unavailable"


class WorkspaceEvaluationApplicability(StrEnum):
    CURRENT = "current"
    HISTORICAL = "historical"
    UNKNOWN = "unknown"


class WorkspaceJobRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    description: str | None = None
    posted_at: datetime | None = None
    work_arrangement: str | None = None
    employment_type: str | None = None
    detail_authority: str
    verification_status: str
    verification_reason: str | None = None
    state: str
    actionable: bool
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime


class WorkspaceProvenanceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    runtime: str
    source_ref: str | None = None
    discovered_via: str | None = None
    imported_at: datetime


class WorkspaceProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceProvenanceRead] = Field(default_factory=list)
    count: int
    limit: int
    truncated: bool


class WorkspaceEvaluationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: datetime
    applicability: WorkspaceEvaluationApplicability
    opportunity: RankedJobOpportunity
    runtime_attribution: SemanticRuntimeAttribution | None = None


class WorkspaceEvaluationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceEvaluationRead] = Field(default_factory=list)
    limit: int
    truncated: bool


class WorkspaceCurrentFitRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WorkspaceCurrentFitStatus
    reason: WorkspaceCurrentFitReason | None = None
    evaluation: WorkspaceEvaluationRead | None = None


class WorkspaceApplicationResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout_status: str
    target_pages: int
    actual_pdf_pages: int
    has_cover_letter: bool
    answer_count: int


class WorkspaceApplicationTrackingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    preparation_id: str
    current_status: ApplicationTrackingStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class WorkspaceApplicationTargetRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: ApplicationTargetKind
    canonical_discovered_job_id: str | None = None
    title: str
    company: str | None = None
    location: str | None = None
    public_url: str | None = None
    work_arrangement: str | None = None
    employment_type: str | None = None
    job_content_hash: str


class WorkspaceApplicationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preparation_id: str
    created_at: datetime
    target: WorkspaceApplicationTargetRead
    snapshot_status: str
    result_summary: WorkspaceApplicationResultSummary | None = None
    tracking: WorkspaceApplicationTrackingSummary | None = None


class WorkspaceApplicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkspaceApplicationRead] = Field(default_factory=list)
    limit: int
    truncated: bool


class JobWorkspaceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: WorkspaceJobRead
    provenance: WorkspaceProvenanceResponse
    current_fit: WorkspaceCurrentFitRead
    evaluations: WorkspaceEvaluationResponse
    decision: UserJobDecisionRead
    applications: WorkspaceApplicationResponse

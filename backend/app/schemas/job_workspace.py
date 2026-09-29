from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.job_ranking import RankedJobOpportunity
from app.schemas.semantic_runtime_attribution import SemanticRuntimeAttribution
from app.schemas.user_job_decision import UserJobDecisionRead


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


class JobWorkspaceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: WorkspaceJobRead
    provenance: WorkspaceProvenanceResponse
    current_fit: WorkspaceCurrentFitRead
    evaluations: WorkspaceEvaluationResponse
    decision: UserJobDecisionRead

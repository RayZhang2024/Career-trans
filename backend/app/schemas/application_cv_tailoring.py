"""Typed reviewable CV tailoring lifecycle."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.application_preparation import ApplicationIdentitySnapshot, ApplicationSourceRef, ApplicationTargetSnapshot, CVWritingDraft
from app.schemas.semantic_runtime_attribution import SemanticRuntimeAttribution


class TailoringAction(StrEnum):
    FEATURE = "feature"
    REORDER = "reorder"
    CONDENSE = "condense"
    OMIT = "omit"


class TailoringDecision(StrEnum):
    KEEP = "keep"
    REORDER = "reorder"
    DEEMPHASISE = "deemphasise"
    SKIP = "skip"


class TailoringStatus(StrEnum):
    STRATEGY_IN_PROGRESS = "strategy_in_progress"
    STRATEGY_REVIEW = "strategy_review"
    STRATEGY_ACCEPTED = "strategy_accepted"
    STRATEGY_REJECTED = "strategy_rejected"
    STRATEGY_FAILED = "strategy_failed"
    STRATEGY_INVALID = "strategy_invalid"
    STRATEGY_UNCERTAIN = "strategy_uncertain"
    DRAFT_IN_PROGRESS = "draft_in_progress"
    DRAFT_REVIEW = "draft_review"
    DRAFT_FAILED = "draft_failed"
    DRAFT_INVALID = "draft_invalid"
    DRAFT_UNCERTAIN = "draft_uncertain"
    STALE = "stale"
    APPROVED = "approved"


class TailoringAttemptStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RESULT_INVALID = "result_invalid"
    OUTCOME_UNCERTAIN = "outcome_uncertain"
    STALE = "stale"


class TailoringRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation_id: str = Field(pattern=r"^requirement-[0-9]{1,3}$")
    requirement_index: int = Field(ge=0, le=99)
    action: TailoringAction
    rationale: str = Field(min_length=1, max_length=360)
    suggested_emphasis: str | None = Field(default=None, max_length=300)
    source_refs: list[ApplicationSourceRef] = Field(min_length=1, max_length=4)


class TailoringGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirement_index: int = Field(ge=0, le=99)
    reason: str = Field(min_length=1, max_length=320)


class TailoringStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    positioning: str = Field(min_length=1, max_length=500)
    summary_emphasis: str = Field(min_length=1, max_length=500)
    recommendations: list[TailoringRecommendation] = Field(default_factory=list, max_length=5)
    gaps_to_clarify: list[TailoringGap] = Field(default_factory=list, max_length=8)
    not_to_claim: list[str] = Field(default_factory=list, max_length=6)


class TailoringStrategyReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendation_id: str = Field(pattern=r"^requirement-[0-9]{1,3}$")
    decision: TailoringDecision
    order: int = Field(ge=0, le=20)


class TailoringStrategyReviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_revision: int = Field(ge=1)
    accepted: bool | None = None
    positioning_override: str | None = Field(default=None, max_length=500)
    choices: list[TailoringStrategyReview] = Field(default_factory=list, max_length=5)


class TailoringStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    client_request_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    discovered_job_id: str = Field(min_length=1, max_length=36)
    target_pages: int = Field(default=2, ge=1, le=3)


class TailoringDraftGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    client_request_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    expected_strategy_revision: int = Field(ge=1)


class TailoringDraftReviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_revision: int = Field(ge=1)
    draft: CVWritingDraft


class TailoringApproveWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_strategy_revision: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)


class TailoringEmploymentOutline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    employment_index: int = Field(ge=0)
    employer: str
    title: str
    start_date: str | None = None
    end_date: str | None = None
    location: str | None = None


class TailoringProjectOutline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_index: int = Field(ge=0)
    name: str


class TailoringProfileOutline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    employment: list[TailoringEmploymentOutline] = Field(default_factory=list, max_length=20)
    projects: list[TailoringProjectOutline] = Field(default_factory=list, max_length=30)
    education: list[str] = Field(default_factory=list, max_length=30)
    credentials: list[str] = Field(default_factory=list, max_length=30)


class TailoringSessionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    client_request_id: str
    discovered_job_id: str
    status: TailoringStatus
    strategy_status: TailoringAttemptStatus
    draft_status: TailoringAttemptStatus
    is_current: bool
    target: ApplicationTargetSnapshot
    identity: ApplicationIdentitySnapshot
    profile_outline: TailoringProfileOutline
    strategy: TailoringStrategy | None = None
    strategy_accepted: bool = False
    strategy_positioning_override: str | None = None
    strategy_choices: list[TailoringStrategyReview] = Field(default_factory=list)
    strategy_revision: int = Field(ge=0)
    draft: CVWritingDraft | None = None
    draft_revision: int = Field(ge=0)
    target_pages: int = Field(ge=1, le=3)
    failure_code: str | None = None
    approved_preparation_id: str | None = None
    runtime_attribution: SemanticRuntimeAttribution | None = None
    created_at: datetime
    updated_at: datetime


class TailoringPreflightRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    discovered_job_id: str
    job_title: str
    state: Literal["ready", "candidate_not_ready", "identity_required", "job_not_actionable", "current_evaluation_required", "input_too_large"]
    provider: str
    model: str
    strategy_output_token_limit: int
    draft_output_token_limit: int
    timeout_seconds: int
    max_requirements: int = Field(ge=1)
    max_input_characters: int = Field(ge=1)
    strategy_request_count: int = 1
    draft_request_count: int = 1
    sessions: list[TailoringSessionRead] = Field(default_factory=list, max_length=10)

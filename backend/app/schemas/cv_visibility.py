from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.job import RequirementCategory, RequirementImportance
from app.schemas.matching import EvidenceRef, MatchType
from app.schemas.semantic_runtime_attribution import SemanticRuntimeAttribution


class CVVisibilityStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PREREQUISITE_FAILED = "prerequisite_failed"
    FAILED = "failed"
    RESULT_INVALID = "result_invalid"
    OUTCOME_UNCERTAIN = "outcome_uncertain"
    STALE = "stale"


class CVVisibilityProjection(StrEnum):
    PRESENTATION_OPPORTUNITY = "presentation_opportunity"
    CLARIFICATION_NEEDED = "clarification_needed"
    CAPABILITY_EVIDENCE_GAP = "capability_evidence_gap"
    ELIGIBILITY_CONCERN = "eligibility_concern"
    SUPPORTED_AND_VISIBLE = "supported_and_visible"
    NOT_ASSESSED = "not_assessed"


class CVVisibilityLabel(StrEnum):
    CLEARLY_COMMUNICATED = "clearly_communicated"
    PRESENT_BUT_WEAK = "present_but_weak"
    NOT_EVIDENT_IN_SELECTED_CV = "not_evident_in_selected_cv"
    UNCERTAIN = "uncertain"
    NOT_ASSESSABLE = "not_assessable"


class CVVisibilitySourceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    draft_state: str
    document_index: int = Field(ge=0)
    filename: str
    media_type: str
    document_sha256: str
    segment_ids: list[str]
    character_count: int
    assessable: bool
    created_at: datetime


class CVVisibilityRequirementRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_index: int
    text: str
    importance: RequirementImportance
    category: RequirementCategory
    match_type: MatchType
    candidate_coverage_summary: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=20)


class CVVisibilityCitationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    quote: str


class CVVisibilityItemRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_index: int
    requirement: str
    candidate_match_type: MatchType
    # Nullable/defaulted for reports written before V2A exposed the independent
    # semantic visibility axis. Never infer this historical value from projection.
    visibility: CVVisibilityLabel | None = None
    projection: CVVisibilityProjection
    explanation: str
    suggestion: str | None = None
    authority_note: str | None = None
    citations: list[CVVisibilityCitationRead] = Field(default_factory=list)
    candidate_evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=20)


class CVVisibilityAssessmentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    client_request_id: str
    status: CVVisibilityStatus
    is_current: bool
    source_document_sha256: str
    source_draft_id: str
    source_document_index: int | None = None
    source_filename: str
    segment_ids: list[str]
    provider: str
    model: str
    created_at: datetime
    completed_at: datetime | None = None
    result: list[CVVisibilityItemRead] = Field(default_factory=list)
    failure_code: str | None = None
    runtime_attribution: SemanticRuntimeAttribution | None = None


class CVVisibilityPreflightRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    discovered_job_id: str
    job_title: str
    state: Literal["ready", "current_evaluation_required", "candidate_not_ready", "source_required", "input_too_large", "evaluation_unavailable"]
    current_evaluation_id: str | None = None
    requirements: list[CVVisibilityRequirementRead] = Field(default_factory=list)
    sources: list[CVVisibilitySourceRead] = Field(default_factory=list)
    assessments: list[CVVisibilityAssessmentRead] = Field(default_factory=list)
    provider: str
    model: str
    max_input_characters: int
    max_evidence_characters: int
    max_total_input_characters: int
    max_output_tokens: int


class CVVisibilityAssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    client_request_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
    source_draft_id: str = Field(min_length=1, max_length=36)
    source_document_index: int = Field(ge=0, le=4)
    source_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment_ids: list[str] = Field(min_length=1, max_length=120)
    consent: Literal[True]


class CVVisibilityCitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    segment_id: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=120)


class CVVisibilityJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirement_index: int = Field(ge=0)
    visibility: CVVisibilityLabel
    explanation: str = Field(min_length=1, max_length=120)
    suggestion: str = Field(min_length=1, max_length=120)
    citations: list[CVVisibilityCitationInput] = Field(default_factory=list, max_length=1)


class CVVisibilityJudgementSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    judgements: list[CVVisibilityJudgement] = Field(min_length=1, max_length=40)

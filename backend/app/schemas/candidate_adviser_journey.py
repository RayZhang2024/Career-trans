from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate_adviser import CandidateAdviserAssessmentStatus


class AdviserNextAction(StrEnum):
    COMPLETE_PROFILE = "complete_profile"
    START_INTAKE = "start_intake"
    CREATE_ASSESSMENT = "create_assessment"
    REVIEW_ASSESSMENT = "review_assessment"
    CONFIRM_CLARIFICATION = "confirm_clarification"
    ANSWER_CLARIFICATION = "answer_clarification"
    REVIEW_PROFILE_ENRICHMENT = "review_profile_enrichment"
    UPDATE_ASSESSMENT = "update_assessment"
    FIND_JOBS = "find_jobs"


class AdviserStatusCategory(StrEnum):
    SETUP = "setup"
    REVIEW = "review"
    UPDATE = "update"
    UP_TO_DATE = "up_to_date"
    UNAVAILABLE = "unavailable"


class CandidateAdviserEnrichmentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str
    question_text: str
    confirmed_context_summary: str
    has_profile_evidence: bool


class CandidateAdviserJourneyRead(BaseModel):
    """Deterministic, provider-free view of one user's persisted Adviser journey."""

    model_config = ConfigDict(extra="forbid")

    candidate_context_ready: bool
    job_search_ready: bool
    intake_exists: bool
    assessment_status: CandidateAdviserAssessmentStatus | None = None
    confirmed_guidance_active: bool
    clarification_session_active: bool = False
    current_follow_up_available: bool
    clarification_interpretation_awaiting_confirmation: bool
    unresolved_profile_enrichment_count: int = Field(ge=0)
    next_enrichment_clarification_id: str | None = None
    next_enrichment: CandidateAdviserEnrichmentRead | None = None
    active_profile_draft: bool
    next_action: AdviserNextAction
    status_category: AdviserStatusCategory
    confirmed_clarification_count: int = Field(ge=0)

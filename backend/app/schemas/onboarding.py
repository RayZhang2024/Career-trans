from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.candidate_adviser import CandidateAdviserAssessmentStatus


class OnboardingCVDraftRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    state: str
    created_at: datetime
    updated_at: datetime


class OnboardingAdviserStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_exists: bool
    assessment_status: CandidateAdviserAssessmentStatus | None = None
    confirmed_clarification_count: int


class OnboardingStatusRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_exists: bool
    candidate_context_ready: bool
    latest_cv_draft: OnboardingCVDraftRead | None = None
    adviser: OnboardingAdviserStatus

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import (
    CandidateEligibility,
    CandidateEvidenceMaterializationStatus,
    CareerEvidence,
)
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessmentContent,
    CandidateAdviserIntakeRead,
)
from app.schemas.candidate_profile import CandidateProfileRead
from app.schemas.cv_ingestion import CandidateCVData, CVIngestionState


class CandidateAdviserReadStatus(StrEnum):
    NOT_AVAILABLE = "not_available"
    REVIEW_READY = "review_ready"
    CONFIRMED = "confirmed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class CandidateReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structured_profile_available: bool
    ready_for_candidate_context: bool
    evidence_materialization_status: CandidateEvidenceMaterializationStatus
    expected_evidence_count: int = Field(ge=0)
    materialized_evidence_count: int = Field(ge=0)
    missing_evidence_count: int = Field(ge=0)
    stale_evidence_count: int = Field(ge=0)
    latest_cv_draft_state: CVIngestionState | None = None


class CanonicalCandidateReadSnapshot(BaseModel):
    """Non-persisted, user-scoped composition of current candidate domains.

    Uploaded source text and generated application wording are intentionally
    absent. ``adviser_assessment`` is present only for a confirmed assessment
    whose semantic-input fingerprint remains current.
    """

    model_config = ConfigDict(extra="forbid")

    profile: CandidateProfileRead | None = None
    structured_profile: CandidateCVData | None = None
    active_evidence: list[CareerEvidence] = Field(default_factory=list)
    adviser_intake: CandidateAdviserIntakeRead | None = None
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)
    adviser_assessment: CandidateAdviserAssessmentContent | None = None
    adviser_assessment_status: CandidateAdviserReadStatus = CandidateAdviserReadStatus.NOT_AVAILABLE
    readiness: CandidateReadiness

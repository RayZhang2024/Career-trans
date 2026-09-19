"""Provider-free, database-only onboarding state projection."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.candidate_adviser import (
    CandidateAdviserAssessmentRecord,
    CandidateAdviserClarificationRecord,
    CandidateAdviserIntakeRecord,
)
from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.schemas.candidate_adviser import CandidateAdviserAssessmentStatus
from app.schemas.onboarding import OnboardingAdviserStatus, OnboardingCVDraftRead, OnboardingStatusRead
from app.services.candidate_adviser_service import CandidateAdviserService


class OnboardingStatusService:
    """Read-only status service; it must never construct semantic clients."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def read(self, user_id: str) -> OnboardingStatusRead:
        latest = self._session.scalar(
            select(CandidateCVIngestionDraft)
            .where(CandidateCVIngestionDraft.user_id == user_id)
            .order_by(CandidateCVIngestionDraft.created_at.desc(), CandidateCVIngestionDraft.id.desc())
            .limit(1)
        )
        assessment = self._session.scalar(
            select(CandidateAdviserAssessmentRecord).where(
                CandidateAdviserAssessmentRecord.user_id == user_id
            )
        )
        adviser = CandidateAdviserService(self._session)
        effective_status = None
        if assessment is not None:
            fingerprint = adviser.input_fingerprint(user_id, read_only=True)
            effective_status = CandidateAdviserService._read_assessment(assessment, fingerprint).status
        confirmed_count = self._session.scalar(
            select(func.count())
            .select_from(CandidateAdviserClarificationRecord)
            .where(
                CandidateAdviserClarificationRecord.user_id == user_id,
                CandidateAdviserClarificationRecord.status == "confirmed",
            )
        ) or 0
        return OnboardingStatusRead(
            profile_exists=self._session.scalar(
                select(CandidateProfile.id).where(CandidateProfile.user_id == user_id)
            ) is not None,
            candidate_context_ready=self._session.scalar(
                select(CandidateStructuredProfile.id).where(CandidateStructuredProfile.user_id == user_id)
            ) is not None,
            latest_cv_draft=(
                OnboardingCVDraftRead(
                    id=latest.id,
                    state=latest.state,
                    created_at=latest.created_at,
                    updated_at=latest.updated_at,
                )
                if latest is not None
                else None
            ),
            adviser=OnboardingAdviserStatus(
                intake_exists=self._session.scalar(
                    select(CandidateAdviserIntakeRecord.id).where(
                        CandidateAdviserIntakeRecord.user_id == user_id
                    )
                ) is not None,
                assessment_status=effective_status,
                confirmed_clarification_count=confirmed_count,
            ),
        )

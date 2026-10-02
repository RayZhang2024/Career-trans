"""Provider-free, database-only onboarding state projection."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.schemas.onboarding import OnboardingAdviserStatus, OnboardingCVDraftRead, OnboardingStatusRead
from app.services.candidate_adviser_journey_service import CandidateAdviserJourneyService


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
        journey = CandidateAdviserJourneyService(self._session).read(user_id)
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
                intake_exists=journey.intake_exists,
                assessment_status=journey.assessment_status,
                confirmed_clarification_count=journey.confirmed_clarification_count,
                journey=journey,
            ),
        )

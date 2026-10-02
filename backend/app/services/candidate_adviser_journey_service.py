import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_adviser import (
    CandidateAdviserAssessmentRecord,
    CandidateAdviserClarificationRecord,
    CandidateAdviserIntakeRecord,
)
from app.models.candidate_adviser_profile_proposal import (
    CandidateAdviserEnrichmentRecord,
    CandidateAdviserProfileProposalRecord,
)
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessmentStatus,
    ClarificationInterpretation,
)
from app.schemas.candidate_adviser_journey import (
    AdviserNextAction,
    AdviserStatusCategory,
    CandidateAdviserEnrichmentRead,
    CandidateAdviserJourneyRead,
)
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.profile_revision_service import CandidateProfileRevisionService


class CandidateAdviserJourneyService:
    """Resolve Adviser status from scoped persisted authorities without writes or providers."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def read(self, user_id: str) -> CandidateAdviserJourneyRead:
        with self._session.no_autoflush:
            snapshot = CanonicalCandidateReadService(self._session).read(user_id)
            candidate_context_ready = snapshot.readiness.structured_profile_available
            job_search_ready = snapshot.readiness.ready_for_candidate_context
            intake_exists = self._session.scalar(
                select(CandidateAdviserIntakeRecord.id).where(
                    CandidateAdviserIntakeRecord.user_id == user_id
                )
            ) is not None
            current = (
                CandidateAdviserService(self._session).get_assessment_read_only(user_id)
                if intake_exists
                else None
            )
            assessment_status = current.status if current is not None else None
            confirmed = assessment_status is CandidateAdviserAssessmentStatus.CONFIRMED
            current_fingerprint = current.input_fingerprint if confirmed and current else None

            confirmed_records = self._session.scalars(
                select(CandidateAdviserClarificationRecord)
                .where(
                    CandidateAdviserClarificationRecord.user_id == user_id,
                    CandidateAdviserClarificationRecord.status == "confirmed",
                )
                .order_by(
                    CandidateAdviserClarificationRecord.confirmed_at,
                    CandidateAdviserClarificationRecord.created_at,
                    CandidateAdviserClarificationRecord.clarification_id,
                )
            ).all()
            confirmed_question_keys = {row.question_key for row in confirmed_records}
            current_rows = self._session.scalars(
                select(CandidateAdviserClarificationRecord).where(
                    CandidateAdviserClarificationRecord.user_id == user_id,
                    CandidateAdviserClarificationRecord.origin_assessment_fingerprint == current_fingerprint,
                )
            ).all() if current_fingerprint else []
            review_pending = any(row.status == "review_ready" for row in current_rows)

            follow_up_available = False
            if confirmed and current is not None:
                for question in current.content.open_questions:
                    _, question_key, _ = CandidateAdviserService._clarification_identity(
                        current.input_fingerprint, question
                    )
                    if question_key not in confirmed_question_keys:
                        follow_up_available = True
                        break

            enrichment_rows = {
                row.clarification_id: row
                for row in self._session.scalars(
                    select(CandidateAdviserEnrichmentRecord).where(
                        CandidateAdviserEnrichmentRecord.user_id == user_id
                    )
                ).all()
            }
            proposal_sources = set(self._session.scalars(
                select(CandidateAdviserProfileProposalRecord.source_clarification_id).where(
                    CandidateAdviserProfileProposalRecord.user_id == user_id
                ).distinct()
            ).all())
            pending_enrichments: list[CandidateAdviserClarificationRecord] = []
            for row in confirmed_records:
                if not self._is_enrichment_eligible(row):
                    continue
                state = enrichment_rows.get(row.clarification_id)
                resolved_state = state.state if state is not None else (
                    "proposals_created" if row.clarification_id in proposal_sources else "pending"
                )
                if resolved_state == "pending":
                    pending_enrichments.append(row)

            active_profile_draft = CandidateProfileRevisionService(self._session).active(user_id) is not None
            next_enrichment = pending_enrichments[0].clarification_id if pending_enrichments else None
            next_enrichment_read = None
            if pending_enrichments:
                first = pending_enrichments[0]
                interpretation = ClarificationInterpretation.model_validate_json(first.interpretation_json)
                next_enrichment_read = CandidateAdviserEnrichmentRead(
                    clarification_id=first.clarification_id,
                    question_text=first.question_text,
                    confirmed_context_summary=interpretation.confirmed_context_summary,
                    has_profile_evidence=bool(interpretation.proposed_evidence),
                )
            next_action, category = self._next_action(
                candidate_context_ready=candidate_context_ready,
                intake_exists=intake_exists,
                assessment_status=assessment_status,
                clarification_review_pending=review_pending,
                pending_enrichment=bool(pending_enrichments),
            )
            return CandidateAdviserJourneyRead(
                candidate_context_ready=candidate_context_ready,
                job_search_ready=job_search_ready,
                intake_exists=intake_exists,
                assessment_status=assessment_status,
                confirmed_guidance_active=confirmed,
                current_follow_up_available=follow_up_available,
                clarification_interpretation_awaiting_confirmation=review_pending,
                unresolved_profile_enrichment_count=len(pending_enrichments),
                next_enrichment_clarification_id=next_enrichment,
                next_enrichment=next_enrichment_read,
                active_profile_draft=active_profile_draft,
                next_action=next_action,
                status_category=category,
                confirmed_clarification_count=len(confirmed_records),
            )

    @staticmethod
    def _is_enrichment_eligible(row: CandidateAdviserClarificationRecord) -> bool:
        if not row.interpretation_json:
            return False
        try:
            interpretation = ClarificationInterpretation.model_validate(
                json.loads(row.interpretation_json)
            )
        except (ValueError, TypeError):
            return False
        return interpretation.answer_kind.value in {"career_fact", "mixed"}

    @staticmethod
    def _next_action(
        *,
        candidate_context_ready: bool,
        intake_exists: bool,
        assessment_status: CandidateAdviserAssessmentStatus | None,
        clarification_review_pending: bool,
        pending_enrichment: bool,
    ) -> tuple[AdviserNextAction, AdviserStatusCategory]:
        if not candidate_context_ready:
            return AdviserNextAction.COMPLETE_PROFILE, AdviserStatusCategory.SETUP
        if not intake_exists:
            return AdviserNextAction.START_INTAKE, AdviserStatusCategory.SETUP
        if assessment_status is CandidateAdviserAssessmentStatus.REVIEW_READY:
            return AdviserNextAction.REVIEW_ASSESSMENT, AdviserStatusCategory.REVIEW
        if assessment_status is None:
            return AdviserNextAction.CREATE_ASSESSMENT, AdviserStatusCategory.SETUP
        if clarification_review_pending:
            return AdviserNextAction.CONFIRM_CLARIFICATION, AdviserStatusCategory.REVIEW
        if pending_enrichment:
            return AdviserNextAction.REVIEW_PROFILE_ENRICHMENT, AdviserStatusCategory.REVIEW
        if assessment_status is CandidateAdviserAssessmentStatus.STALE:
            return AdviserNextAction.UPDATE_ASSESSMENT, AdviserStatusCategory.UPDATE
        if assessment_status is CandidateAdviserAssessmentStatus.CONFIRMED:
            return AdviserNextAction.FIND_JOBS, AdviserStatusCategory.UP_TO_DATE
        return AdviserNextAction.CREATE_ASSESSMENT, AdviserStatusCategory.UNAVAILABLE

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
    CandidateAdviserAreaRead,
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
            adviser_service = CandidateAdviserService(self._session)
            refinement = adviser_service._current_refinement_journey(user_id)
            bounded_contract = bool(current and current.contract_version == "clarification_areas_v1")
            refinement_areas = adviser_service._journey_areas(user_id, refinement.journey_key) if refinement else []
            displayed_area_round = 2 if refinement and refinement.state == "round1_assessment_review" else refinement.round_number if refinement else 1
            current_round_areas = [area for area in refinement_areas if area.round_number == displayed_area_round]
            round_records = adviser_service._round_question_records(user_id, refinement.journey_key, refinement.round_number) if refinement else []
            active_session = adviser_service.active_clarification_session(user_id)
            current_fingerprint = (
                active_session[0] if active_session else current.input_fingerprint if confirmed and current else None
            )

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
            current_rows = active_session[1] if active_session else self._session.scalars(
                select(CandidateAdviserClarificationRecord).where(
                    CandidateAdviserClarificationRecord.user_id == user_id,
                    CandidateAdviserClarificationRecord.origin_assessment_fingerprint == current_fingerprint,
                )
            ).all() if current_fingerprint else []
            review_pending = any(row.status == "review_ready" for row in current_rows)
            unanswered_available = any(row.status == "unanswered" for row in current_rows)

            follow_up_available = False
            if active_session:
                follow_up_available = unanswered_available
            elif confirmed and current is not None:
                for question in current.content.open_questions:
                    _, question_key, _ = CandidateAdviserService._clarification_identity(current.input_fingerprint, question)
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
            pending_enrichments: list[CandidateAdviserClarificationRecord] = []
            enrichment_scope = active_session[1] if active_session else current_rows
            for row in enrichment_scope:
                if not self._is_enrichment_eligible(row):
                    continue
                state = enrichment_rows.get(row.clarification_id)
                proposal_states = self._session.scalars(select(CandidateAdviserProfileProposalRecord.state).where(
                    CandidateAdviserProfileProposalRecord.user_id == user_id,
                    CandidateAdviserProfileProposalRecord.source_clarification_id == row.clarification_id,
                )).all()
                resolved_state = state.state if state is not None else "pending"
                if resolved_state not in {"deferred", "reviewed_no_update"} and (
                    resolved_state == "pending" or any(value == "pending" for value in proposal_states)
                ):
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
                clarification_session_active=active_session is not None,
                clarification_unanswered=unanswered_available,
                bounded_contract=bounded_contract,
                refinement_state=refinement.state if refinement else None,
            )
            return CandidateAdviserJourneyRead(
                candidate_context_ready=candidate_context_ready,
                job_search_ready=job_search_ready,
                intake_exists=intake_exists,
                assessment_status=assessment_status,
                confirmed_guidance_active=confirmed or active_session is not None,
                clarification_session_active=active_session is not None,
                current_follow_up_available=follow_up_available,
                clarification_interpretation_awaiting_confirmation=review_pending,
                unresolved_profile_enrichment_count=len(pending_enrichments),
                next_enrichment_clarification_id=next_enrichment,
                next_enrichment=next_enrichment_read,
                active_profile_draft=active_profile_draft,
                next_action=next_action,
                status_category=category,
                confirmed_clarification_count=len(confirmed_records),
                assessment_contract_version=current.contract_version.value if current else "legacy_questions",
                refinement_journey_id=refinement.journey_key if refinement else None,
                refinement_round_number=refinement.round_number if refinement else None,
                refinement_rounds_completed=refinement.rounds_completed if refinement else 0,
                refinement_state=refinement.state if refinement else None,
                refinement_areas=[CandidateAdviserAreaRead(
                    area_key=area.area_key,
                    title=area.title,
                    rationale=area.rationale,
                    priority_index=area.priority_index,
                    selection_state=area.selection_state,
                    round_number=area.round_number,
                ) for area in current_round_areas],
                round_question_count=len(round_records),
                round_questions_resolved=sum(1 for row in round_records if row.status == "confirmed"),
                refinement_complete=bool(refinement and refinement.state == "complete"),
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
        clarification_session_active: bool = False,
        clarification_unanswered: bool = False,
        bounded_contract: bool = False,
        refinement_state: str | None = None,
    ) -> tuple[AdviserNextAction, AdviserStatusCategory]:
        if not candidate_context_ready:
            return AdviserNextAction.COMPLETE_PROFILE, AdviserStatusCategory.SETUP
        if not intake_exists:
            return AdviserNextAction.START_INTAKE, AdviserStatusCategory.SETUP
        if assessment_status is CandidateAdviserAssessmentStatus.REVIEW_READY:
            return AdviserNextAction.REVIEW_ASSESSMENT, AdviserStatusCategory.REVIEW
        if assessment_status is None:
            return AdviserNextAction.CREATE_ASSESSMENT, AdviserStatusCategory.SETUP
        if assessment_status is CandidateAdviserAssessmentStatus.STALE and not clarification_session_active:
            return AdviserNextAction.UPDATE_ASSESSMENT, AdviserStatusCategory.UPDATE
        if bounded_contract and refinement_state == "area_selection":
            return AdviserNextAction.SELECT_CLARIFICATION_AREAS, AdviserStatusCategory.REVIEW
        if bounded_contract and refinement_state == "questions_pending":
            return AdviserNextAction.GENERATE_ROUND_QUESTIONS, AdviserStatusCategory.REVIEW
        if clarification_review_pending:
            return AdviserNextAction.CONFIRM_CLARIFICATION, AdviserStatusCategory.REVIEW
        if bounded_contract and refinement_state in {"questions_active", "assessment_update"}:
            if clarification_session_active and clarification_unanswered:
                return AdviserNextAction.ANSWER_CLARIFICATION, AdviserStatusCategory.REVIEW
            if pending_enrichment:
                return AdviserNextAction.REVIEW_PROFILE_ENRICHMENT, AdviserStatusCategory.REVIEW
            return AdviserNextAction.UPDATE_ASSESSMENT, AdviserStatusCategory.UPDATE
        if bounded_contract and refinement_state == "complete":
            return AdviserNextAction.REFINEMENT_COMPLETE, AdviserStatusCategory.UP_TO_DATE
        if pending_enrichment:
            return AdviserNextAction.REVIEW_PROFILE_ENRICHMENT, AdviserStatusCategory.REVIEW
        if clarification_session_active and clarification_unanswered:
            return AdviserNextAction.ANSWER_CLARIFICATION, AdviserStatusCategory.REVIEW
        if assessment_status is CandidateAdviserAssessmentStatus.STALE:
            return AdviserNextAction.UPDATE_ASSESSMENT, AdviserStatusCategory.UPDATE
        if assessment_status is CandidateAdviserAssessmentStatus.CONFIRMED:
            return AdviserNextAction.FIND_JOBS, AdviserStatusCategory.UP_TO_DATE
        return AdviserNextAction.CREATE_ASSESSMENT, AdviserStatusCategory.UNAVAILABLE

import hashlib
import json
import re
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.candidate_adviser import CandidateAdviserAgent
from app.agents.candidate_adviser_clarification import CandidateAdviserClarificationInterpreter
from app.models.candidate_adviser import CandidateAdviserAssessmentRecord, CandidateAdviserClarificationRecord, CandidateAdviserIntakeRecord
from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.schemas.candidate_adviser import (
    AdviserInsight,
    CandidateAdviserAssessmentContent,
    CandidateAdviserAssessmentRead,
    CandidateAdviserAssessmentStatus,
    CandidateAdviserClarificationAnswer,
    CandidateAdviserClarificationRead,
    CandidateAdviserClarificationStatus,
    CandidateAdviserIntake,
    CandidateAdviserIntakeRead,
    CandidateAdviserSemanticInput,
    ClarificationAnswerKind,
    ClarificationInterpretation,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.services.candidate_adviser_compaction import compact_candidate_adviser_input
from app.services.candidate_adviser_references import candidate_adviser_reference_catalog, candidate_adviser_reference_is_allowed
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver


class CandidateAdviserService:
    def __init__(self, session: Session, *, agent: CandidateAdviserAgent | None = None, clarification_interpreter: CandidateAdviserClarificationInterpreter | None = None) -> None:
        self._session = session
        self._agent = agent
        self._clarification_interpreter = clarification_interpreter

    def get_intake(self, user_id: str) -> CandidateAdviserIntakeRead | None:
        record = self._session.scalar(select(CandidateAdviserIntakeRecord).where(CandidateAdviserIntakeRecord.user_id == user_id))
        if record is None:
            return None
        return CandidateAdviserIntakeRead(**json.loads(record.intake_json), updated_at=record.updated_at)

    def save_intake(self, user_id: str, intake: CandidateAdviserIntake) -> CandidateAdviserIntakeRead:
        record = self._session.scalar(select(CandidateAdviserIntakeRecord).where(CandidateAdviserIntakeRecord.user_id == user_id))
        encoded = json.dumps(intake.model_dump(mode="json"), sort_keys=True)
        if record is None:
            record = CandidateAdviserIntakeRecord(user_id=user_id, intake_json=encoded)
            self._session.add(record)
        else:
            record.intake_json = encoded
        self._session.commit()
        self._session.refresh(record)
        return CandidateAdviserIntakeRead(**intake.model_dump(mode="json"), updated_at=record.updated_at)

    def assess(self, user_id: str) -> CandidateAdviserAssessmentRead:
        if self._agent is None:
            raise ValueError("No semantic candidate adviser is configured.")
        intake = self._intake(user_id)
        if not self._session.scalar(select(CandidateStructuredProfile.id).where(CandidateStructuredProfile.user_id == user_id)):
            raise ValueError("Candidate adviser requires a confirmed CV before assessment.")
        semantic_input = self._semantic_input(user_id, intake=intake)
        content = self._agent.assess(semantic_input=semantic_input)
        self._validate_sources(content, semantic_input)
        fingerprint = self.input_fingerprint(user_id, semantic_input=semantic_input)
        record = self._session.scalar(select(CandidateAdviserAssessmentRecord).where(CandidateAdviserAssessmentRecord.user_id == user_id))
        encoded = json.dumps(content.model_dump(mode="json"), sort_keys=True)
        if record is None:
            record = CandidateAdviserAssessmentRecord(user_id=user_id, input_fingerprint=fingerprint, status=CandidateAdviserAssessmentStatus.REVIEW_READY, assessment_json=encoded)
            self._session.add(record)
        else:
            record.input_fingerprint = fingerprint
            record.status = CandidateAdviserAssessmentStatus.REVIEW_READY
            record.assessment_json = encoded
        self._session.commit()
        self._session.refresh(record)
        return self._read_assessment(record, fingerprint)

    def confirm_assessment(self, user_id: str) -> CandidateAdviserAssessmentRead:
        record = self._session.scalar(select(CandidateAdviserAssessmentRecord).where(CandidateAdviserAssessmentRecord.user_id == user_id))
        if record is None:
            raise ValueError("Candidate adviser assessment has not been generated.")
        fingerprint = self.input_fingerprint(user_id)
        assessment = self._read_assessment(record, fingerprint)
        if assessment.status is CandidateAdviserAssessmentStatus.STALE:
            raise ValueError("Candidate adviser assessment is stale; generate a new review draft before confirming.")
        if assessment.status is CandidateAdviserAssessmentStatus.CONFIRMED:
            return assessment
        if assessment.status is not CandidateAdviserAssessmentStatus.REVIEW_READY:
            raise ValueError("Candidate adviser assessment is not ready for confirmation.")
        record.status = CandidateAdviserAssessmentStatus.CONFIRMED
        self._session.commit()
        self._session.refresh(record)
        return self._read_assessment(record, fingerprint)

    def get_assessment(self, user_id: str) -> CandidateAdviserAssessmentRead | None:
        record = self._session.scalar(select(CandidateAdviserAssessmentRecord).where(CandidateAdviserAssessmentRecord.user_id == user_id))
        if record is None:
            return None
        fingerprint = self.input_fingerprint(user_id)
        return self._read_assessment(record, fingerprint)

    def current_assessment(self, user_id: str) -> CandidateAdviserAssessmentRead | None:
        assessment = self.get_assessment(user_id)
        return assessment if assessment and assessment.status is CandidateAdviserAssessmentStatus.CONFIRMED else None

    def list_clarifications(self, user_id: str) -> list[CandidateAdviserClarificationRead]:
        found = self.get_assessment(user_id)
        if found is None:
            raise ValueError("A confirmed candidate adviser assessment is required before clarifications.")
        assessment = found if found.status is CandidateAdviserAssessmentStatus.CONFIRMED else None
        if assessment is None:
            # A stale predecessor's siblings are historical, rather than a
            # new current question set.
            return []
        self._materialize_clarifications(user_id, assessment)
        records = self._session.scalars(select(CandidateAdviserClarificationRecord).where(
            CandidateAdviserClarificationRecord.user_id == user_id,
            CandidateAdviserClarificationRecord.origin_assessment_fingerprint == assessment.input_fingerprint,
        ).order_by(CandidateAdviserClarificationRecord.priority_index, CandidateAdviserClarificationRecord.clarification_id)).all()
        return [self._read_clarification(record) for record in records]

    def answer_clarification(
        self,
        user_id: str,
        clarification_id: str,
        payload: CandidateAdviserClarificationAnswer,
    ) -> CandidateAdviserClarificationRead:
        record = self._current_clarification(user_id, clarification_id)
        if record.status == CandidateAdviserClarificationStatus.CONFIRMED:
            raise ValueError("Confirmed clarification records are immutable.")
        if self._clarification_interpreter is None:
            raise ValueError("No semantic clarification interpreter is configured.")
        interpretation = self._clarification_interpreter.interpret(
            question_text=record.question_text,
            answer_text=payload.answer_text,
        )
        self._validate_interpretation(interpretation)
        record.answer_text = payload.answer_text
        record.interpretation_json = json.dumps(interpretation.model_dump(mode="json"), sort_keys=True)
        record.status = CandidateAdviserClarificationStatus.REVIEW_READY
        self._session.commit()
        self._session.refresh(record)
        return self._read_clarification(record)

    def confirm_clarification(self, user_id: str, clarification_id: str) -> CandidateAdviserClarificationRead:
        record = self._current_clarification(user_id, clarification_id, allow_confirmed=True)
        if record.status == CandidateAdviserClarificationStatus.CONFIRMED:
            return self._read_clarification(record)
        if record.status != CandidateAdviserClarificationStatus.REVIEW_READY or not record.interpretation_json:
            raise ValueError("Clarification is not ready for confirmation.")
        from datetime import datetime, timezone
        # Confirmation and active-evidence reconciliation are one atomic
        # transition. A failed reconciliation must not leave a confirmed
        # clarification that was never made active.
        with self._session.begin_nested():
            record.status = CandidateAdviserClarificationStatus.CONFIRMED
            record.confirmed_at = datetime.now(timezone.utc)
            self._session.flush()
            # The resolver remains the only active-evidence authority.
            self._resolve_active_evidence(user_id)
        self._session.commit()
        self._session.refresh(record)
        return self._read_clarification(record)

    def input_fingerprint(self, user_id: str, *, semantic_input: CandidateAdviserSemanticInput | None = None) -> str:
        payload = (semantic_input or self._semantic_input(user_id)).model_dump(mode="json")
        # Preserve pre-#152 fingerprints when the user has no confirmation
        # state at all; bounded projection does not replace authoritative state.
        if not payload.get("clarifications"):
            payload.pop("clarifications", None)
        confirmed_state = self._confirmed_clarification_state(user_id)
        if confirmed_state:
            payload["confirmed_clarification_state"] = confirmed_state
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _intake(self, user_id: str) -> CandidateAdviserIntake:
        found = self.get_intake(user_id)
        if found is None:
            raise ValueError("Candidate adviser intake has not been provided.")
        return CandidateAdviserIntake.model_validate(found.model_dump(exclude={"updated_at"}))

    def _semantic_input(self, user_id: str, *, intake: CandidateAdviserIntake | None = None) -> CandidateAdviserSemanticInput:
        structured = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
        data = CandidateCVData.model_validate(json.loads(structured.structured_json)) if structured else CandidateCVData()
        active_evidence = ActiveCandidateEvidenceResolver(self._session).resolve(user_id, data)
        evidence = [
            {
                "evidence_id": item.evidence_id,
                "evidence_type": item.evidence_type,
                "title": item.title,
                "text": item.text,
                "skills": item.skills,
            }
            for item in active_evidence
        ]
        return compact_candidate_adviser_input(
            intake=intake or self._intake(user_id),
            structured_cv=data,
            career_evidence=evidence,
            clarifications=self._confirmed_clarification_projection(user_id),
        )

    @staticmethod
    def _validate_sources(content: CandidateAdviserAssessmentContent, semantic_input: CandidateAdviserSemanticInput) -> None:
        catalog = candidate_adviser_reference_catalog(semantic_input)
        for insight in CandidateAdviserService._insights(content):
            for reference in insight.source_references:
                if reference.source_type == "career_evidence" and not candidate_adviser_reference_is_allowed(
                    source_type=reference.source_type,
                    reference=reference.reference,
                    catalog=catalog,
                ):
                    raise ValueError("Candidate adviser output referenced evidence that was not supplied.")
                if reference.source_type == "intake" and not candidate_adviser_reference_is_allowed(
                    source_type=reference.source_type,
                    reference=reference.reference,
                    catalog=catalog,
                ):
                    raise ValueError("Candidate adviser output referenced an intake field that was not supplied.")
                if reference.source_type == "clarification" and not candidate_adviser_reference_is_allowed(
                    source_type=reference.source_type,
                    reference=reference.reference,
                    catalog=catalog,
                ):
                    raise ValueError("Candidate adviser output referenced a clarification that was not supplied.")

    @staticmethod
    def _insights(content: CandidateAdviserAssessmentContent) -> Iterable[AdviserInsight]:
        yield content.professional_positioning
        yield from content.transferable_strengths
        yield from content.development_gaps
        yield from content.role_hypotheses
        yield content.transition_assessment
        yield from content.open_questions
        yield content.career_strategy_summary
        yield content.job_search_strategy_summary

    @staticmethod
    def _read_assessment(record: CandidateAdviserAssessmentRecord, fingerprint: str) -> CandidateAdviserAssessmentRead:
        status = CandidateAdviserAssessmentStatus(record.status) if record.input_fingerprint == fingerprint else CandidateAdviserAssessmentStatus.STALE
        return CandidateAdviserAssessmentRead(
            input_fingerprint=record.input_fingerprint,
            status=status,
            content=CandidateAdviserAssessmentContent.model_validate(json.loads(record.assessment_json)),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _materialize_clarifications(self, user_id: str, assessment: CandidateAdviserAssessmentRead) -> None:
        existing_confirmed_keys = set(self._session.scalars(select(CandidateAdviserClarificationRecord.question_key).where(
            CandidateAdviserClarificationRecord.user_id == user_id,
            CandidateAdviserClarificationRecord.status == CandidateAdviserClarificationStatus.CONFIRMED,
        )).all())
        # A current assessment can itself contain semantically identical
        # questions with different source references. Ask once; the first
        # ordered question remains the deterministic representative.
        seen_question_keys = set(existing_confirmed_keys)
        for priority, question in enumerate(assessment.content.open_questions):
            clarification_id, question_key, references = self._clarification_identity(
                assessment.input_fingerprint, question,
            )
            if question_key in seen_question_keys:
                continue
            seen_question_keys.add(question_key)
            record = CandidateAdviserClarificationRecord(
                user_id=user_id,
                clarification_id=clarification_id,
                question_key=question_key,
                origin_assessment_fingerprint=assessment.input_fingerprint,
                question_text=question.text,
                question_source_references_json=json.dumps(references, sort_keys=True),
                priority_index=priority,
                status=CandidateAdviserClarificationStatus.UNANSWERED,
            )
            try:
                with self._session.begin_nested():
                    self._session.add(record)
                    self._session.flush()
            except IntegrityError:
                # The user-scoped uniqueness boundary makes repeated/racing
                # materialisation idempotent.
                continue
        self._session.commit()

    def _current_clarification(self, user_id: str, clarification_id: str, *, allow_confirmed: bool = False) -> CandidateAdviserClarificationRecord:
        record = self._session.scalar(select(CandidateAdviserClarificationRecord).where(
            CandidateAdviserClarificationRecord.user_id == user_id,
            CandidateAdviserClarificationRecord.clarification_id == clarification_id,
        ))
        if record is None:
            raise LookupError("Clarification not found.")
        if allow_confirmed and record.status == CandidateAdviserClarificationStatus.CONFIRMED:
            return record
        assessment = self.current_assessment(user_id)
        if assessment is None or record.origin_assessment_fingerprint != assessment.input_fingerprint:
            raise ValueError("Clarification is no longer current.")
        return record

    @staticmethod
    def _clarification_identity(origin_fingerprint: str, question: AdviserInsight) -> tuple[str, str, list[dict[str, str]]]:
        canonical_question = " ".join(question.text.split()).casefold()
        references = sorted(
            [reference.model_dump(mode="json") for reference in question.source_references],
            key=lambda item: (item["source_type"], item["reference"]),
        )
        question_key = hashlib.sha256(canonical_question.encode("utf-8")).hexdigest()
        clarification_id = hashlib.sha256(json.dumps({
            "origin_assessment_fingerprint": origin_fingerprint,
            "question": canonical_question,
            "source_references": references,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return clarification_id, question_key, references

    @staticmethod
    def _validate_interpretation(value: ClarificationInterpretation) -> None:
        if value.answer_kind in {
            ClarificationAnswerKind.ELIGIBILITY_FACT,
            ClarificationAnswerKind.PREFERENCE_INTENT,
            ClarificationAnswerKind.INSUFFICIENT,
        } and value.proposed_evidence:
            raise ValueError("This clarification answer kind cannot propose career evidence.")
        for proposal in value.proposed_evidence:
            if re.search(r"\b(?:never|not|do not|don't|no experience|lack(?:s|ing)?)\b", f"{proposal.title} {proposal.text}", re.IGNORECASE):
                raise ValueError("Negative or absence claims cannot become career evidence.")
            if re.search(r"\b(?:work authori[sz]ation|security clearance|clearance|eligible|eligibility|right to work|location)\b", f"{proposal.title} {proposal.text}", re.IGNORECASE):
                raise ValueError("Eligibility claims cannot become career evidence.")

    def _confirmed_clarifications(self, user_id: str) -> list[CandidateAdviserClarificationRecord]:
        return list(self._session.scalars(select(CandidateAdviserClarificationRecord).where(
            CandidateAdviserClarificationRecord.user_id == user_id,
            CandidateAdviserClarificationRecord.status == CandidateAdviserClarificationStatus.CONFIRMED,
        ).order_by(CandidateAdviserClarificationRecord.confirmed_at, CandidateAdviserClarificationRecord.clarification_id)))

    def _confirmed_clarification_projection(self, user_id: str) -> list[dict[str, object]]:
        # The bounded provider context must contain the latest confirmations,
        # while the full confirmed state remains part of the fingerprint.
        records = self._confirmed_clarifications(user_id)
        records = records[-12:]
        projection: list[dict[str, object]] = []
        for record in records:
            if not record.interpretation_json:
                continue
            interpretation = ClarificationInterpretation.model_validate(json.loads(record.interpretation_json))
            projection.append({
                "clarification_id": record.clarification_id,
                "question": record.question_text,
                "confirmed_context_summary": interpretation.confirmed_context_summary,
                "answer_kind": interpretation.answer_kind.value,
            })
        return projection

    def _confirmed_clarification_state(self, user_id: str) -> list[dict[str, object]]:
        # Include all confirmed context for staleness, even when the provider
        # projection is bounded.
        return [
            {
                "clarification_id": record.clarification_id,
                "question_key": record.question_key,
                "interpretation": json.loads(record.interpretation_json or "{}"),
            }
            for record in self._confirmed_clarifications(user_id)
        ]

    def _resolve_active_evidence(self, user_id: str) -> None:
        structured = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
        data = CandidateCVData.model_validate(json.loads(structured.structured_json)) if structured else CandidateCVData()
        ActiveCandidateEvidenceResolver(self._session).resolve(user_id, data)

    @staticmethod
    def _read_clarification(record: CandidateAdviserClarificationRecord) -> CandidateAdviserClarificationRead:
        return CandidateAdviserClarificationRead(
            clarification_id=record.clarification_id,
            question_text=record.question_text,
            question_source_references=json.loads(record.question_source_references_json),
            priority_index=record.priority_index,
            status=CandidateAdviserClarificationStatus(record.status),
            answer_text=record.answer_text,
            interpretation=(ClarificationInterpretation.model_validate(json.loads(record.interpretation_json)) if record.interpretation_json else None),
            created_at=record.created_at,
            updated_at=record.updated_at,
            confirmed_at=record.confirmed_at,
        )

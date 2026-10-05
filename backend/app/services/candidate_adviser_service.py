import hashlib
import json
import re
from collections.abc import Callable, Iterable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.candidate_adviser import CandidateAdviserAgent
from app.agents.candidate_adviser_clarification import CandidateAdviserClarificationInterpreter
from app.models.candidate_adviser import CandidateAdviserAssessmentRecord, CandidateAdviserClarificationRecord, CandidateAdviserIntakeRecord
from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.models.candidate_adviser_profile_proposal import CandidateAdviserEnrichmentRecord, CandidateAdviserProfileProposalRecord
from app.schemas.candidate_adviser import (
    AdviserOpenQuestion,
    AdviserInsight,
    CandidateAdviserAssessmentContent,
    CandidateAdviserAssessmentRead,
    CandidateAdviserAssessmentStatus,
    CandidateAdviserClarificationAnswer,
    CandidateAdviserClarificationInterpretationInput,
    CandidateAdviserClarificationRead,
    CandidateAdviserSuggestedAnswer,
    CandidateAdviserStructuredResponse,
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
    def __init__(self, session: Session, *, agent: CandidateAdviserAgent | None = None, clarification_interpreter: CandidateAdviserClarificationInterpreter | None = None, agent_factory: Callable[[], CandidateAdviserAgent] | None = None, clarification_interpreter_factory: Callable[[], CandidateAdviserClarificationInterpreter] | None = None) -> None:
        self._session = session
        self._agent = agent
        self._clarification_interpreter = clarification_interpreter
        self._agent_factory = agent_factory
        self._clarification_interpreter_factory = clarification_interpreter_factory

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
        if self.has_active_clarification_session(user_id):
            raise ValueError("Finish or defer the active clarification session before updating the assessment.")
        intake = self._intake(user_id)
        if not self._session.scalar(select(CandidateStructuredProfile.id).where(CandidateStructuredProfile.user_id == user_id)):
            raise ValueError("Candidate adviser requires a confirmed CV before assessment.")
        semantic_input = self._semantic_input(user_id, intake=intake)
        content = self._semantic_agent().assess(semantic_input=semantic_input)
        self._validate_new_open_questions(content)
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

    def get_assessment_read_only(self, user_id: str) -> CandidateAdviserAssessmentRead | None:
        record = self._session.scalar(select(CandidateAdviserAssessmentRecord).where(CandidateAdviserAssessmentRecord.user_id == user_id))
        if record is None:
            return None
        fingerprint = self.input_fingerprint(user_id, read_only=True)
        return self._read_assessment(record, fingerprint)

    def current_assessment(self, user_id: str) -> CandidateAdviserAssessmentRead | None:
        assessment = self.get_assessment(user_id)
        return assessment if assessment and assessment.status is CandidateAdviserAssessmentStatus.CONFIRMED else None

    def current_assessment_read_only(self, user_id: str) -> CandidateAdviserAssessmentRead | None:
        assessment = self.get_assessment_read_only(user_id)
        return assessment if assessment and assessment.status is CandidateAdviserAssessmentStatus.CONFIRMED else None

    def list_clarifications(self, user_id: str) -> list[CandidateAdviserClarificationRead]:
        assessment_record = self._assessment_record(user_id)
        if assessment_record is None:
            raise ValueError("A confirmed candidate adviser assessment is required before clarifications.")
        found = self.get_assessment(user_id)
        existing = self._session.scalars(select(CandidateAdviserClarificationRecord).where(
            CandidateAdviserClarificationRecord.user_id == user_id,
            CandidateAdviserClarificationRecord.origin_assessment_fingerprint == assessment_record.input_fingerprint,
        )).all()
        if assessment_record.status == CandidateAdviserAssessmentStatus.CONFIRMED and found is not None and found.status is CandidateAdviserAssessmentStatus.CONFIRMED:
            self._materialize_clarifications(user_id, found)
        elif not existing:
            return []
        elif not self.has_active_clarification_session(user_id):
            return []
        records = self._session.scalars(select(CandidateAdviserClarificationRecord).where(
            CandidateAdviserClarificationRecord.user_id == user_id,
            CandidateAdviserClarificationRecord.origin_assessment_fingerprint == assessment_record.input_fingerprint,
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
        options = self._suggested_answers(record)
        supplied = payload.model_fields_set
        structured_fields = {"selected_option_ids", "custom_answer_text", "special_selection"}
        has_structured = bool(supplied & structured_fields)
        has_legacy = "answer_text" in supplied
        if has_structured == has_legacy:
            raise ValueError("Provide exactly one clarification answer format.")
        if has_legacy:
            if options:
                raise ValueError("This clarification requires its persisted selectable-answer format.")
            if payload.answer_text is None or not payload.answer_text.strip():
                raise ValueError("A non-empty clarification answer is required.")
            response = None
            answer_text = payload.answer_text
            interpreter_input = CandidateAdviserClarificationInterpretationInput(
                question_text=record.question_text,
                selected_answers=[],
                additional_detail=answer_text,
            )
            interpretation = self._clarification_agent().interpret(
                interpretation_input=interpreter_input,
            )
        else:
            if not options:
                raise ValueError("This legacy clarification accepts free-text answers only.")
            if not structured_fields.issubset(supplied):
                raise ValueError("All structured clarification answer fields are required.")
            response = CandidateAdviserStructuredResponse(
                selected_option_ids=payload.selected_option_ids or [],
                custom_answer_text=payload.custom_answer_text or "",
                special_selection=payload.special_selection,
            )
            selected_answers = self._validate_structured_response(options, response)
            if response.special_selection == "not_sure":
                interpretation = ClarificationInterpretation(
                    answer_kind=ClarificationAnswerKind.INSUFFICIENT,
                    confirmed_context_summary="The candidate is not sure or does not have enough information to answer yet.",
                    proposed_evidence=[],
                )
            else:
                interpreter_input = CandidateAdviserClarificationInterpretationInput(
                    question_text=record.question_text,
                    selected_answers=selected_answers,
                    additional_detail=response.custom_answer_text,
                )
                interpretation = self._clarification_agent().interpret(
                    interpretation_input=interpreter_input,
                )
            answer_text = self._structured_answer_projection(options, response)
        self._validate_interpretation(interpretation)
        record.answer_text = answer_text
        record.structured_response_json = (
            json.dumps(response.model_dump(mode="json"), sort_keys=True)
            if response is not None else None
        )
        record.interpretation_json = json.dumps(interpretation.model_dump(mode="json"), sort_keys=True)
        record.status = CandidateAdviserClarificationStatus.REVIEW_READY
        self._session.commit()
        self._session.refresh(record)
        return self._read_clarification(record)

    def _semantic_agent(self) -> CandidateAdviserAgent:
        if self._agent is None:
            if self._agent_factory is None:
                raise ValueError("No semantic candidate adviser is configured.")
            self._agent = self._agent_factory()
        return self._agent

    def _clarification_agent(self) -> CandidateAdviserClarificationInterpreter:
        if self._clarification_interpreter is None:
            if self._clarification_interpreter_factory is None:
                raise ValueError("No semantic clarification interpreter is configured.")
            self._clarification_interpreter = self._clarification_interpreter_factory()
        return self._clarification_interpreter

    def confirm_clarification(self, user_id: str, clarification_id: str) -> CandidateAdviserClarificationRead:
        record = self._current_clarification(user_id, clarification_id, allow_confirmed=True)
        _, _, interpretation = self._validated_review_state(record)
        if record.status == CandidateAdviserClarificationStatus.CONFIRMED:
            return self._read_clarification(record)
        if record.status != CandidateAdviserClarificationStatus.REVIEW_READY or interpretation is None:
            raise ValueError("Clarification is not ready for confirmation.")
        from datetime import datetime, timezone
        # Confirmation and active-evidence reconciliation are one atomic
        # transition. A failed reconciliation must not leave a confirmed
        # clarification that was never made active.
        with self._session.begin_nested():
            record.status = CandidateAdviserClarificationStatus.CONFIRMED
            record.confirmed_at = datetime.now(timezone.utc)
            self._session.flush()
            if interpretation.answer_kind in {ClarificationAnswerKind.CAREER_FACT, ClarificationAnswerKind.MIXED}:
                enrichment = self._session.get(
                    CandidateAdviserEnrichmentRecord, (user_id, clarification_id)
                )
                if enrichment is None:
                    self._session.add(CandidateAdviserEnrichmentRecord(
                        user_id=user_id,
                        clarification_id=clarification_id,
                        source_assessment_fingerprint=record.origin_assessment_fingerprint,
                        state="pending",
                    ))
            # The resolver remains the only active-evidence authority.
            self._resolve_active_evidence(user_id)
        self._session.commit()
        self._session.refresh(record)
        return self._read_clarification(record)

    def input_fingerprint(
        self,
        user_id: str,
        *,
        semantic_input: CandidateAdviserSemanticInput | None = None,
        read_only: bool = False,
    ) -> str:
        payload = (semantic_input or self._semantic_input(user_id, read_only=read_only)).model_dump(mode="json")
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

    def _semantic_input(
        self,
        user_id: str,
        *,
        intake: CandidateAdviserIntake | None = None,
        read_only: bool = False,
    ) -> CandidateAdviserSemanticInput:
        structured = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
        data = CandidateCVData.model_validate(json.loads(structured.structured_json)) if structured else CandidateCVData()
        resolver = ActiveCandidateEvidenceResolver(self._session)
        active_evidence = (
            resolver.read_active(user_id, data)
            if read_only
            else resolver.resolve(user_id, data)
        )
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
    def _insights(content: CandidateAdviserAssessmentContent) -> Iterable[AdviserInsight | AdviserOpenQuestion]:
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
        # A current assessment can itself contain the same canonical question
        # text with different source references. Ask once; the first ordered
        # question remains the deterministic representative. This is exact
        # canonical-text equality only, not semantic paraphrase detection.
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
                suggested_answers_json=json.dumps(
                    [
                        {
                            "option_id": self._option_id(clarification_id, text),
                            "text": self._normalize_choice(text),
                        }
                        for text in getattr(question, "suggested_answers", [])
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ) if getattr(question, "suggested_answers", []) else None,
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
        assessment_record = self._assessment_record(user_id)
        if (
            assessment_record is None
            or assessment_record.status != CandidateAdviserAssessmentStatus.CONFIRMED
            or record.origin_assessment_fingerprint != assessment_record.input_fingerprint
            or not self.has_active_clarification_session(user_id)
        ):
            raise ValueError("Clarification is no longer current.")
        if allow_confirmed and record.status == CandidateAdviserClarificationStatus.CONFIRMED:
            return record
        return record

    def active_clarification_session(self, user_id: str) -> tuple[str, list[CandidateAdviserClarificationRecord]] | None:
        assessment_record = self._assessment_record(user_id)
        if assessment_record is None or assessment_record.status != CandidateAdviserAssessmentStatus.CONFIRMED:
            return None
        rows = list(self._session.scalars(select(CandidateAdviserClarificationRecord).where(
            CandidateAdviserClarificationRecord.user_id == user_id,
            CandidateAdviserClarificationRecord.origin_assessment_fingerprint == assessment_record.input_fingerprint,
        ).order_by(CandidateAdviserClarificationRecord.priority_index, CandidateAdviserClarificationRecord.clarification_id)))
        if not rows or not self._clarification_session_unresolved(user_id, rows):
            return None
        return assessment_record.input_fingerprint, rows

    def has_active_clarification_session(self, user_id: str) -> bool:
        return self.active_clarification_session(user_id) is not None

    def _clarification_session_unresolved(
        self, user_id: str, rows: list[CandidateAdviserClarificationRecord]
    ) -> bool:
        from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
        for row in rows:
            if row.status != CandidateAdviserClarificationStatus.CONFIRMED:
                return True
            if not row.interpretation_json:
                continue
            interpretation = ClarificationInterpretation.model_validate_json(row.interpretation_json)
            if interpretation.answer_kind not in {ClarificationAnswerKind.CAREER_FACT, ClarificationAnswerKind.MIXED}:
                continue
            enrichment = self._session.get(CandidateAdviserEnrichmentRecord, (user_id, row.clarification_id))
            state = enrichment.state if enrichment is not None else "pending"
            if state in {"deferred", "reviewed_no_update"}:
                continue
            proposals = self._session.scalars(select(CandidateAdviserProfileProposalRecord.state).where(
                CandidateAdviserProfileProposalRecord.user_id == user_id,
                CandidateAdviserProfileProposalRecord.source_clarification_id == row.clarification_id,
            )).all()
            if not proposals or any(value == "pending" for value in proposals):
                return True
        return False

    def _assessment_record(self, user_id: str) -> CandidateAdviserAssessmentRecord | None:
        return self._session.scalar(select(CandidateAdviserAssessmentRecord).where(
            CandidateAdviserAssessmentRecord.user_id == user_id
        ).execution_options(populate_existing=True))

    @staticmethod
    def _clarification_identity(origin_fingerprint: str, question: AdviserInsight | AdviserOpenQuestion) -> tuple[str, str, list[dict[str, str]]]:
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
    def _normalize_choice(value: str) -> str:
        return " ".join(value.split())

    @classmethod
    def _option_id(cls, clarification_id: str, text: str) -> str:
        normalized = cls._normalize_choice(text).casefold()
        return hashlib.sha256(f"{clarification_id}\0{normalized}".encode("utf-8")).hexdigest()

    @classmethod
    def _validate_new_open_questions(cls, content: CandidateAdviserAssessmentContent) -> None:
        for question in content.open_questions:
            normalized = [cls._normalize_choice(choice) for choice in question.suggested_answers]
            if not 3 <= len(normalized) <= 6 or any(not choice or len(choice) > 240 for choice in normalized):
                raise ValueError("Each newly generated open question must have 3–6 non-empty choices of at most 240 characters.")
            identities = [choice.casefold() for choice in normalized]
            if len(set(identities)) != len(identities):
                raise ValueError("New open-question choices must be unique after whitespace normalization.")

    @staticmethod
    def _validate_structured_response(
        options: list[CandidateAdviserSuggestedAnswer],
        response: CandidateAdviserStructuredResponse,
    ) -> list[str]:
        option_by_id = {option.option_id: option for option in options}
        selected = response.selected_option_ids
        if len(selected) != len(set(selected)):
            raise ValueError("Selected clarification options must be unique.")
        if len(selected) > len(options) or any(option_id not in option_by_id for option_id in selected):
            raise ValueError("A selected clarification option is unknown or no longer available.")
        has_detail = bool(response.custom_answer_text.strip())
        if response.special_selection == "not_sure":
            if selected or has_detail:
                raise ValueError("The not-sure response cannot be combined with selected options or custom detail.")
            return []
        if not selected and not has_detail:
            raise ValueError("Select an answer, add custom detail, or choose the not-sure response.")
        # Keep provider-facing labels intact, independent of answer_text's size.
        selected_set = set(selected)
        return [option.text for option in options if option.option_id in selected_set]

    @staticmethod
    def _structured_answer_projection(
        options: list[CandidateAdviserSuggestedAnswer],
        response: CandidateAdviserStructuredResponse,
    ) -> str:
        if response.special_selection == "not_sure":
            return "I'm not sure / I don't have enough information to answer this yet"
        selected_set = set(response.selected_option_ids)
        pieces = [option.text for option in options if option.option_id in selected_set]
        if response.custom_answer_text.strip():
            pieces.append(response.custom_answer_text)
        return "\n".join(pieces)

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

    def _read_clarification(self, record: CandidateAdviserClarificationRecord) -> CandidateAdviserClarificationRead:
        options, response, interpretation = self._validated_review_state(record)
        return CandidateAdviserClarificationRead(
            clarification_id=record.clarification_id,
            question_text=record.question_text,
            question_source_references=json.loads(record.question_source_references_json),
            suggested_answers=options,
            structured_response=response,
            priority_index=record.priority_index,
            status=CandidateAdviserClarificationStatus(record.status),
            answer_text=record.answer_text,
            interpretation=interpretation,
            created_at=record.created_at,
            updated_at=record.updated_at,
            confirmed_at=record.confirmed_at,
            session_active=self.has_active_clarification_session(record.user_id),
        )

    @classmethod
    def _validated_review_state(
        cls,
        record: CandidateAdviserClarificationRecord,
    ) -> tuple[
        list[CandidateAdviserSuggestedAnswer],
        CandidateAdviserStructuredResponse | None,
        ClarificationInterpretation | None,
    ]:
        """Decode one clarification's persisted answer state and enforce its invariants."""
        options = cls._suggested_answers(record)
        response = cls._structured_response(record)
        try:
            status = CandidateAdviserClarificationStatus(record.status)
        except ValueError as exc:
            raise ValueError("Persisted clarification status is invalid.") from exc

        interpretation: ClarificationInterpretation | None = None
        if record.interpretation_json is not None:
            try:
                interpretation = ClarificationInterpretation.model_validate_json(record.interpretation_json)
            except (TypeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                raise ValueError("Persisted clarification interpretation is invalid.") from exc

        if response is not None:
            if not options or status == CandidateAdviserClarificationStatus.UNANSWERED:
                raise ValueError("Persisted structured clarification review is invalid.")
            try:
                cls._validate_structured_response(options, response)
            except ValueError as exc:
                raise ValueError("Persisted structured clarification response is invalid.") from exc

        has_review = status in {
            CandidateAdviserClarificationStatus.REVIEW_READY,
            CandidateAdviserClarificationStatus.CONFIRMED,
        }
        if options and has_review and (response is None or interpretation is None):
            raise ValueError("Persisted structured clarification review is incomplete.")
        if not options and response is not None:
            raise ValueError("Persisted structured clarification response is invalid.")
        if not options and has_review and (
            not record.answer_text or not record.answer_text.strip() or interpretation is None
        ):
            raise ValueError("Persisted legacy clarification review is incomplete.")
        if status == CandidateAdviserClarificationStatus.UNANSWERED and interpretation is not None:
            raise ValueError("Persisted unanswered clarification contains review state.")
        return options, response, interpretation

    @staticmethod
    def _suggested_answers(
        record: CandidateAdviserClarificationRecord,
    ) -> list[CandidateAdviserSuggestedAnswer]:
        if record.suggested_answers_json is None:
            return []
        try:
            values = json.loads(record.suggested_answers_json)
            if not isinstance(values, list):
                raise ValueError
            parsed = [CandidateAdviserSuggestedAnswer.model_validate(value) for value in values]
            if not 3 <= len(parsed) <= 6:
                raise ValueError
            if len({item.option_id for item in parsed}) != len(parsed):
                raise ValueError
            normalized = [" ".join(item.text.split()).casefold() for item in parsed]
            expected_ids = [CandidateAdviserService._option_id(record.clarification_id, item.text) for item in parsed]
            if len(set(normalized)) != len(normalized) or any(not value for value in normalized) or [item.option_id for item in parsed] != expected_ids:
                raise ValueError
            return parsed
        except (TypeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ValueError("Persisted clarification answer options are invalid.") from exc

    @staticmethod
    def _structured_response(
        record: CandidateAdviserClarificationRecord,
    ) -> CandidateAdviserStructuredResponse | None:
        if record.structured_response_json is None:
            return None
        try:
            return CandidateAdviserStructuredResponse.model_validate_json(
                record.structured_response_json
            )
        except (TypeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ValueError("Persisted structured clarification response is invalid.") from exc

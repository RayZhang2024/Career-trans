import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.candidate_adviser import CandidateAdviserAgent
from app.agents.candidate_adviser_clarification import CandidateAdviserClarificationInterpreter
from app.agents.candidate_adviser_question import CandidateAdviserQuestionGenerator
from app.models.candidate_adviser import (CandidateAdviserAssessmentRecord, CandidateAdviserClarificationRecord, CandidateAdviserIntakeRecord, CandidateAdviserRefinementJourneyRecord, CandidateAdviserClarificationAreaRecord)
from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_adviser_profile_proposal import CandidateAdviserEnrichmentRecord, CandidateAdviserProfileProposalRecord
from app.schemas.candidate_adviser import (
    AdviserOpenQuestion,
    AdviserInsight,
    CandidateAdviserAssessmentContent,
    CandidateAdviserAssessmentRead,
    CandidateAdviserAssessmentContractVersion,
    CandidateAdviserAssessmentStatus,
    CandidateAdviserClarificationAnswer,
    CandidateAdviserClarificationInterpretationInput,
    CandidateAdviserClarificationRead,
    CandidateAdviserSuggestedAnswer,
    CandidateAdviserStructuredResponse,
    CandidateAdviserClarificationStatus,
    AdviserClarificationArea,
    CandidateAdviserQuestionGenerationInput,
    CandidateAdviserAreaSelectionRequest,
    CandidateAdviserQuestionGenerationRequest,
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
    def __init__(self, session: Session, *, agent: CandidateAdviserAgent | None = None, clarification_interpreter: CandidateAdviserClarificationInterpreter | None = None, question_generator: CandidateAdviserQuestionGenerator | None = None, agent_factory: Callable[[], CandidateAdviserAgent] | None = None, clarification_interpreter_factory: Callable[[], CandidateAdviserClarificationInterpreter] | None = None, question_generator_factory: Callable[[], CandidateAdviserQuestionGenerator] | None = None) -> None:
        self._session = session
        self._agent = agent
        self._clarification_interpreter = clarification_interpreter
        self._question_generator = question_generator
        self._agent_factory = agent_factory
        self._clarification_interpreter_factory = clarification_interpreter_factory
        self._question_generator_factory = question_generator_factory

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

    def assess(self, user_id: str, *, regenerate: bool = False) -> CandidateAdviserAssessmentRead:
        review_states = {"initial_assessment_review", "round1_assessment_review", "round2_assessment_review"}
        journey = self._current_refinement_journey(user_id)
        active_session = self.has_active_clarification_session(user_id)
        if journey is not None and journey.state == "questions_active" and not active_session:
            journey.rounds_completed = max(journey.rounds_completed, journey.round_number)
            journey.state = "assessment_update"
            self._session.commit()
        if active_session:
            raise ValueError("Finish or defer the active clarification session before updating the assessment.")
        intake = self._intake(user_id)
        if not self._session.scalar(select(CandidateStructuredProfile.id).where(CandidateStructuredProfile.user_id == user_id)):
            raise ValueError("Candidate adviser requires a confirmed CV before assessment.")
        assessment_record = self._assessment_record(user_id)
        current_origin_fingerprint = self._origin_context_fingerprint(user_id)
        if (
            journey is not None
            and journey.state != "assessment_update"
            and journey.state != "complete"
            and journey.origin_context_fingerprint != current_origin_fingerprint
        ):
            # A user-authored intake/Profile/CV change can make an in-flight
            # assessment stale before its current clarification loop ends.
            # Start a fresh durable journey while keeping prior rounds as
            # history; otherwise the stale area-selection state could never
            # be repaired through the normal Update action.
            journey.state = "superseded"
            self._session.flush()
            journey = None
        if journey is not None and journey.state == "complete":
            if journey.origin_context_fingerprint == current_origin_fingerprint:
                raise ValueError("A new refinement journey requires a material candidate-context change outside the completed journey.")
            journey.state = "superseded"
            self._session.flush()
            journey = None
        elif journey is not None and journey.state not in review_states | {"assessment_update"}:
            raise ValueError("The refinement journey is not ready for assessment generation.")
        if journey is not None and journey.state in review_states and assessment_record is not None and not regenerate:
            current = self._read_assessment(assessment_record, self.input_fingerprint(user_id))
            if current.status is CandidateAdviserAssessmentStatus.REVIEW_READY:
                return current
        if journey is None:
            journey_key = str(uuid4())
            round_number = 1
            rounds_completed = 0
            prior_areas: list[CandidateAdviserClarificationAreaRecord] = []
            state_after_generation = "initial_assessment_review"
        elif journey.state == "initial_assessment_review":
            journey_key = journey.journey_key
            round_number = 1
            rounds_completed = 0
            prior_areas = self._journey_areas(user_id, journey.journey_key)
            state_after_generation = "initial_assessment_review"
        elif journey.state == "round1_assessment_review":
            journey_key = journey.journey_key
            round_number = 2
            rounds_completed = 1
            prior_areas = self._journey_areas(user_id, journey.journey_key)
            state_after_generation = "round1_assessment_review"
        elif journey.state == "round2_assessment_review":
            journey_key = journey.journey_key
            round_number = 2
            rounds_completed = 2
            prior_areas = self._journey_areas(user_id, journey.journey_key)
            state_after_generation = "round2_assessment_review"
        else:
            journey_key = journey.journey_key
            rounds_completed = journey.rounds_completed
            prior_areas = self._journey_areas(user_id, journey.journey_key)
            round_number = 2 if rounds_completed > 0 else journey.round_number
            state_after_generation = "round1_assessment_review" if rounds_completed == 1 else "round2_assessment_review"
        if regenerate and journey is not None and journey.state in review_states:
            for draft_area in self._journey_areas(user_id, journey.journey_key):
                if draft_area.round_number == round_number and draft_area.selection_state == "proposed":
                    self._session.delete(draft_area)
        semantic_input = self._semantic_input(user_id, intake=intake)
        semantic_input.refinement_control = self._refinement_control(
            journey_key=journey_key,
            round_number=round_number,
            rounds_completed=rounds_completed,
            areas_allowed=rounds_completed < 2,
            prior_areas=prior_areas,
        )
        content = self._semantic_agent().assess(semantic_input=semantic_input)
        self._validate_new_area_assessment(content, areas_allowed=rounds_completed < 2)
        self._validate_sources(content, semantic_input)
        fingerprint = self.input_fingerprint(user_id, semantic_input=semantic_input)
        record = self._session.scalar(select(CandidateAdviserAssessmentRecord).where(CandidateAdviserAssessmentRecord.user_id == user_id))
        encoded = json.dumps(content.model_dump(mode="json"), sort_keys=True)
        if record is None:
            record = CandidateAdviserAssessmentRecord(user_id=user_id, input_fingerprint=fingerprint, contract_version="clarification_areas_v1", status=CandidateAdviserAssessmentStatus.REVIEW_READY, assessment_json=encoded)
            self._session.add(record)
        else:
            record.input_fingerprint = fingerprint
            record.contract_version = "clarification_areas_v1"
            record.status = CandidateAdviserAssessmentStatus.REVIEW_READY
            record.assessment_json = encoded
        if journey is None:
            journey = CandidateAdviserRefinementJourneyRecord(
                id=journey_key,
                user_id=user_id,
                journey_key=journey_key,
                round_number=1,
                rounds_completed=0,
                state=state_after_generation,
                origin_context_fingerprint=self._origin_context_fingerprint(user_id),
            )
            self._session.add(journey)
        else:
            journey.state = state_after_generation
        self._persist_proposed_areas(user_id, journey_key, round_number, fingerprint, content.clarification_areas)
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
        if (record.contract_version or "legacy_questions") == "clarification_areas_v1":
            journey = self._current_refinement_journey(user_id)
            if journey is not None:
                content = self._read_assessment(record, fingerprint).content
                if journey.state == "initial_assessment_review":
                    journey.state = "area_selection" if content.clarification_areas else "complete"
                elif journey.state == "round1_assessment_review":
                    journey.rounds_completed = 1
                    journey.state = "area_selection" if content.clarification_areas else "complete"
                    if journey.state == "area_selection":
                        journey.round_number = 2
                elif journey.state == "round2_assessment_review":
                    journey.rounds_completed = 2
                    journey.state = "complete"
                journey.origin_context_fingerprint = self._origin_context_fingerprint(user_id)
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
        journey = self._current_refinement_journey(user_id)
        if journey is not None and journey.state in {"questions_pending", "questions_active", "assessment_update", "round1_assessment_review", "round2_assessment_review"}:
            records = self._round_question_records(user_id, journey.journey_key, journey.round_number if journey.round_number <= 2 else 2)
            return [self._read_clarification(record) for record in records]
        if (assessment_record.contract_version or "legacy_questions") == "clarification_areas_v1":
            return []
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

    def _question_agent(self) -> CandidateAdviserQuestionGenerator:
        if self._question_generator is None:
            if self._question_generator_factory is None:
                raise ValueError("No grouped clarification question generator is configured.")
            self._question_generator = self._question_generator_factory()
        return self._question_generator

    def _current_refinement_journey(self, user_id: str) -> CandidateAdviserRefinementJourneyRecord | None:
        return self._session.scalar(
            select(CandidateAdviserRefinementJourneyRecord)
            .where(CandidateAdviserRefinementJourneyRecord.user_id == user_id)
            .where(CandidateAdviserRefinementJourneyRecord.state != "superseded")
            .order_by(CandidateAdviserRefinementJourneyRecord.updated_at.desc(), CandidateAdviserRefinementJourneyRecord.id.desc())
        )

    def _journey_areas(self, user_id: str, journey_key: str) -> list[CandidateAdviserClarificationAreaRecord]:
        return list(self._session.scalars(
            select(CandidateAdviserClarificationAreaRecord)
            .where(
                CandidateAdviserClarificationAreaRecord.user_id == user_id,
                CandidateAdviserClarificationAreaRecord.journey_key == journey_key,
            )
            .order_by(CandidateAdviserClarificationAreaRecord.round_number, CandidateAdviserClarificationAreaRecord.priority_index, CandidateAdviserClarificationAreaRecord.area_key)
        ))

    @staticmethod
    def _canonical_area_key(value: str) -> str:
        return unicodedata.normalize("NFKC", value).strip().casefold()

    def _refinement_control(
        self,
        *,
        journey_key: str,
        round_number: int,
        rounds_completed: int,
        areas_allowed: bool,
        prior_areas: list[CandidateAdviserClarificationAreaRecord],
    ):
        from app.schemas.candidate_adviser import RefinementControlInput, RefinementControlArea

        return RefinementControlInput(
            journey_id=journey_key,
            round_number=round_number,
            rounds_completed=rounds_completed,
            areas_allowed=areas_allowed,
            prior_areas=[
                RefinementControlArea(
                    area_key=area.area_key,
                    title=area.title,
                    selection_state="selected" if area.selection_state == "selected" else "skipped",
                )
                for area in prior_areas
                if area.selection_state in {"selected", "skipped"}
            ],
        )

    def _persist_proposed_areas(
        self,
        user_id: str,
        journey_key: str,
        round_number: int,
        fingerprint: str,
        areas: list[AdviserClarificationArea],
    ) -> None:
        prior = self._journey_areas(user_id, journey_key)
        prior_keys = {self._canonical_area_key(item.area_key) for item in prior}
        canonical_keys = [self._canonical_area_key(item.area_key) for item in areas]
        if len(set(canonical_keys)) != len(canonical_keys):
            raise ValueError("Clarification area keys must be unique after canonicalization.")
        if any(key in prior_keys for key in canonical_keys):
            raise ValueError("A clarification area key cannot be reused within one refinement journey.")
        for priority, area in enumerate(areas):
            self._session.add(CandidateAdviserClarificationAreaRecord(
                user_id=user_id,
                journey_key=journey_key,
                round_number=round_number,
                area_key=self._canonical_area_key(area.area_key),
                title=area.title,
                rationale=area.rationale,
                priority_index=priority,
                source_references_json=json.dumps([item.model_dump(mode="json") for item in area.source_references], sort_keys=True),
                selection_state="proposed",
                origin_assessment_fingerprint=fingerprint,
            ))

    def _origin_context_fingerprint(self, user_id: str) -> str:
        intake = self._session.scalar(select(CandidateAdviserIntakeRecord).where(CandidateAdviserIntakeRecord.user_id == user_id))
        structured = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
        profile = self._session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        payload = {
            "intake": json.loads(intake.intake_json) if intake is not None else None,
            "structured_cv": json.loads(structured.structured_json) if structured is not None else None,
            "profile": {
                field: getattr(profile, field)
                for field in ("headline", "summary", "current_role", "location", "career_goal", "job_search_criteria")
            } if profile is not None else None,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _validate_refinement_mutation_authority(
        self,
        user_id: str,
        payload: CandidateAdviserAreaSelectionRequest | CandidateAdviserQuestionGenerationRequest,
        *,
        allowed_states: set[str],
    ) -> tuple[CandidateAdviserRefinementJourneyRecord, list[CandidateAdviserClarificationAreaRecord]]:
        journey = self._current_refinement_journey(user_id)
        assessment_record = self._assessment_record(user_id)
        assessment = self.get_assessment(user_id)
        if (
            journey is None
            or journey.journey_key != payload.expected_refinement_journey_id
            or journey.round_number != payload.expected_round_number
            or journey.state not in allowed_states
            or assessment_record is None
            or assessment is None
            or assessment.status is not CandidateAdviserAssessmentStatus.CONFIRMED
            or (assessment_record.contract_version or "legacy_questions") != "clarification_areas_v1"
            or assessment.input_fingerprint != payload.expected_assessment_fingerprint
            or assessment.assessment_authority_token != payload.expected_assessment_authority_token
        ):
            raise ValueError("Refinement authority changed. Refresh Career Adviser before continuing.")
        areas = [area for area in self._journey_areas(user_id, journey.journey_key) if area.round_number == journey.round_number]
        if not areas or any(area.origin_assessment_fingerprint != payload.expected_assessment_fingerprint for area in areas):
            raise ValueError("The clarification areas belong to a different assessment. Refresh Career Adviser before continuing.")
        return journey, areas

    def select_refinement_areas(self, user_id: str, payload: CandidateAdviserAreaSelectionRequest) -> list[CandidateAdviserClarificationAreaRecord]:
        journey, areas = self._validate_refinement_mutation_authority(user_id, payload, allowed_states={"area_selection"})
        canonical_selected = [self._canonical_area_key(key) for key in payload.selected_area_keys]
        if len(set(canonical_selected)) != len(canonical_selected):
            raise ValueError("Selected clarification areas must be unique.")
        by_key = {area.area_key: area for area in areas}
        if any(key not in by_key for key in canonical_selected):
            raise ValueError("A selected clarification area is no longer available.")
        if not canonical_selected:
            for area in areas:
                area.selection_state = "skipped"
            journey.state = "complete"
        else:
            selected_set = set(canonical_selected)
            for area in areas:
                area.selection_state = "selected" if area.area_key in selected_set else "skipped"
            journey.state = "questions_pending"
        self._session.commit()
        return areas

    def generate_round_questions(self, user_id: str, payload: CandidateAdviserQuestionGenerationRequest) -> list[CandidateAdviserClarificationRead]:
        journey, areas = self._validate_refinement_mutation_authority(user_id, payload, allowed_states={"questions_pending", "questions_active"})
        existing = self._round_question_records(user_id, journey.journey_key, journey.round_number)
        if existing:
            return [self._read_clarification(row) for row in existing]
        if journey.state != "questions_pending":
            raise ValueError("Round questions are not available for generation.")
        areas = [area for area in areas if area.selection_state == "selected"]
        if not areas:
            raise ValueError("At least one committed area must be selected.")
        semantic_input = self._semantic_input(user_id)
        question_input = CandidateAdviserQuestionGenerationInput(
            journey_id=journey.journey_key,
            round_number=journey.round_number,
            selected_areas=[AdviserClarificationArea(
                area_key=area.area_key,
                title=area.title,
                rationale=area.rationale,
                source_references=json.loads(area.source_references_json),
            ) for area in areas],
            intake=semantic_input.intake,
            structured_cv=semantic_input.structured_cv,
            career_evidence=semantic_input.career_evidence,
            clarifications=semantic_input.clarifications,
        )
        output = self._question_agent().generate(generation_input=question_input)
        by_area = {self._canonical_area_key(group.area_key): group for group in output.area_groups}
        selected_keys = [area.area_key for area in areas]
        if len(by_area) != len(output.area_groups) or set(by_area) != set(selected_keys):
            raise ValueError("Generated questions must include every selected area exactly once and no other area.")
        catalog = candidate_adviser_reference_catalog(semantic_input)
        all_question_keys: set[str] = set()
        all_canonical_text: set[str] = set()
        new_records: list[CandidateAdviserClarificationRecord] = []
        for area in areas:
            group = by_area[area.area_key]
            if not 1 <= len(group.questions) <= 5:
                raise ValueError("Each selected area must receive between one and five questions.")
            for within_area, question in enumerate(group.questions):
                qkey = self._canonical_area_key(question.question_key)
                canonical_text = " ".join(question.text.split()).casefold()
                if qkey in all_question_keys or canonical_text in all_canonical_text:
                    raise ValueError("Generated question identities must be non-empty and unique.")
                all_question_keys.add(qkey)
                all_canonical_text.add(canonical_text)
                if not question.text.strip():
                    raise ValueError("Generated questions must be non-empty.")
                for reference in question.source_references:
                    if not candidate_adviser_reference_is_allowed(source_type=reference.source_type, reference=reference.reference, catalog=catalog):
                        raise ValueError("Generated question referenced a source that was not supplied.")
                options = [self._normalize_choice(value) for value in question.suggested_answers]
                if not 3 <= len(options) <= 6 or any(not value or len(value) > 240 for value in options) or len({value.casefold() for value in options}) != len(options):
                    raise ValueError("Generated question choices must be 3–6 distinct non-empty options.")
                # Stable area/slot identities make concurrent/retried provider
                # responses converge on one complete persisted question set.
                clarification_id = hashlib.sha256(f"{journey.journey_key}\0{journey.round_number}\0{area.area_key}\0{within_area}".encode("utf-8")).hexdigest()
                source_refs = [item.model_dump(mode="json") for item in question.source_references]
                new_records.append(CandidateAdviserClarificationRecord(
                    user_id=user_id,
                    clarification_id=clarification_id,
                    question_key=hashlib.sha256(canonical_text.encode("utf-8")).hexdigest(),
                    origin_assessment_fingerprint=payload.expected_assessment_fingerprint,
                    parent_area_id=area.id,
                    round_number=journey.round_number,
                    question_text=question.text,
                    question_source_references_json=json.dumps(source_refs, sort_keys=True),
                    suggested_answers_json=json.dumps([{"option_id": self._option_id(clarification_id, value), "text": value} for value in options], ensure_ascii=False, sort_keys=True),
                    priority_index=area.priority_index * 10 + within_area,
                    status=CandidateAdviserClarificationStatus.UNANSWERED,
                ))
        try:
            with self._session.begin_nested():
                self._session.add_all(new_records)
                self._session.flush()
                journey.state = "questions_active"
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            reconciled = self._round_question_records(user_id, journey.journey_key, journey.round_number)
            if reconciled:
                return [self._read_clarification(row) for row in reconciled]
            raise ValueError("Round questions could not be committed atomically; retry to reconcile persisted state.") from exc
        return [self._read_clarification(row) for row in self._round_question_records(user_id, journey.journey_key, journey.round_number)]

    def _round_question_records(self, user_id: str, journey_key: str, round_number: int) -> list[CandidateAdviserClarificationRecord]:
        area_ids = select(CandidateAdviserClarificationAreaRecord.id).where(
            CandidateAdviserClarificationAreaRecord.user_id == user_id,
            CandidateAdviserClarificationAreaRecord.journey_key == journey_key,
            CandidateAdviserClarificationAreaRecord.round_number == round_number,
            CandidateAdviserClarificationAreaRecord.selection_state == "selected",
        )
        return list(self._session.scalars(
            select(CandidateAdviserClarificationRecord).where(
                CandidateAdviserClarificationRecord.user_id == user_id,
                CandidateAdviserClarificationRecord.parent_area_id.in_(area_ids),
                CandidateAdviserClarificationRecord.round_number == round_number,
            ).order_by(CandidateAdviserClarificationRecord.priority_index, CandidateAdviserClarificationRecord.clarification_id)
        ))

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
            journey = self._current_refinement_journey(user_id)
            if journey is not None and record.parent_area_id is not None:
                journey.origin_context_fingerprint = self._origin_context_fingerprint(user_id)
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
        payload.pop("refinement_control", None)
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
        yield from content.clarification_areas
        yield content.career_strategy_summary
        yield content.job_search_strategy_summary

    @staticmethod
    def _read_assessment(record: CandidateAdviserAssessmentRecord, fingerprint: str) -> CandidateAdviserAssessmentRead:
        status = CandidateAdviserAssessmentStatus(record.status) if record.input_fingerprint == fingerprint else CandidateAdviserAssessmentStatus.STALE
        contract_version = record.contract_version or CandidateAdviserAssessmentContractVersion.LEGACY_QUESTIONS
        content = CandidateAdviserAssessmentContent.model_validate(json.loads(record.assessment_json))
        canonical_authority = json.dumps({
            "assessment_content": content.model_dump(mode="json"),
            "contract_version": contract_version.value if isinstance(contract_version, CandidateAdviserAssessmentContractVersion) else contract_version,
            "input_fingerprint": record.input_fingerprint,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        authority_token = hashlib.sha256(canonical_authority.encode("utf-8")).hexdigest()
        return CandidateAdviserAssessmentRead(
            input_fingerprint=record.input_fingerprint,
            assessment_authority_token=authority_token,
            contract_version=contract_version,
            status=status,
            content=content,
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

    @classmethod
    def _validate_new_area_assessment(cls, content: CandidateAdviserAssessmentContent, *, areas_allowed: bool) -> None:
        if content.open_questions:
            raise ValueError("New area-contract assessments must not generate immediate clarification questions.")
        if len(content.clarification_areas) > 6:
            raise ValueError("A clarification round may propose at most six areas.")
        if not areas_allowed and content.clarification_areas:
            raise ValueError("Clarification areas are disabled after Round 2.")
        keys = [cls._canonical_area_key(area.area_key) for area in content.clarification_areas]
        if any(not key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("Clarification area keys must be non-empty and unique after canonicalization.")
        if any(area.rationale != area.rationale.strip() or not area.rationale.strip() for area in content.clarification_areas):
            raise ValueError("Clarification areas require a concise rationale.")

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
        # Keep every answer in the current journey: two rounds can contain up
        # to 60 confirmed questions. Older journeys remain useful context, but
        # are compacted to the latest 12. Workflow-control rows are never
        # projected as citeable clarification evidence.
        records = self._confirmed_clarifications(user_id)
        journey = self._current_refinement_journey(user_id)
        current_area_ids = set(self._session.scalars(
            select(CandidateAdviserClarificationAreaRecord.id).where(
                CandidateAdviserClarificationAreaRecord.user_id == user_id,
                CandidateAdviserClarificationAreaRecord.journey_key == journey.journey_key,
            )
        )) if journey is not None else set()
        current_records = [record for record in records if record.parent_area_id in current_area_ids]
        historical_records = [record for record in records if record.parent_area_id not in current_area_ids][-12:]
        def confirmation_key(item: CandidateAdviserClarificationRecord) -> tuple[float, str]:
            confirmed_at = item.confirmed_at or item.updated_at
            if confirmed_at.tzinfo is None:
                confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
            return confirmed_at.timestamp(), item.clarification_id

        records = sorted([*historical_records, *current_records], key=confirmation_key)
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
        parent_area = self._session.scalar(select(CandidateAdviserClarificationAreaRecord).where(
            CandidateAdviserClarificationAreaRecord.id == record.parent_area_id,
            CandidateAdviserClarificationAreaRecord.user_id == record.user_id,
        )) if record.parent_area_id else None
        return CandidateAdviserClarificationRead(
            clarification_id=record.clarification_id,
            question_text=record.question_text,
            question_source_references=json.loads(record.question_source_references_json),
            suggested_answers=options,
            structured_response=response,
            priority_index=record.priority_index,
            parent_area_key=parent_area.area_key if parent_area else None,
            parent_area_title=parent_area.title if parent_area else None,
            round_number=record.round_number,
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


def refresh_refinement_origin_for_clarification(session: Session, user_id: str, clarification_id: str) -> None:
    """Keep an in-journey Adviser Profile apply from looking like external context."""
    clarification = session.scalar(select(CandidateAdviserClarificationRecord).where(
        CandidateAdviserClarificationRecord.user_id == user_id,
        CandidateAdviserClarificationRecord.clarification_id == clarification_id,
    ))
    if clarification is None or clarification.parent_area_id is None:
        return
    area = session.scalar(select(CandidateAdviserClarificationAreaRecord).where(
        CandidateAdviserClarificationAreaRecord.id == clarification.parent_area_id,
        CandidateAdviserClarificationAreaRecord.user_id == user_id,
    ))
    if area is None:
        return
    journey = session.scalar(select(CandidateAdviserRefinementJourneyRecord).where(
        CandidateAdviserRefinementJourneyRecord.user_id == user_id,
        CandidateAdviserRefinementJourneyRecord.journey_key == area.journey_key,
    ))
    if journey is not None:
        journey.origin_context_fingerprint = CandidateAdviserService(session)._origin_context_fingerprint(user_id)

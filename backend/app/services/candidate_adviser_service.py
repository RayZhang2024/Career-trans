import json
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.candidate_adviser import CandidateAdviserAgent
from app.models.candidate_adviser import (
    CandidateAdviserAssessmentRecord,
    CandidateIntakeProfile,
)
from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.providers.llm import SemanticOutputError
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessment,
    CandidateAdviserAssessmentRead,
    CandidateAdviserState,
    CandidateIntakeProfileData,
    CandidateIntakeRead,
    CandidateIntakeSourceType,
)
from app.services.candidate_adviser_projection import (
    candidate_adviser_input_fingerprint,
    candidate_adviser_source_context,
    compact_candidate_intake,
    intake_source_refs,
)
from app.services.cv_ingestion_service import PersistedCandidateContextLoader


class CandidateAdviserService:
    def __init__(
        self,
        session: Session,
        *,
        adviser: CandidateAdviserAgent | None = None,
        adviser_factory: Callable[[], CandidateAdviserAgent] | None = None,
    ) -> None:
        self._session = session
        self._adviser = adviser
        self._adviser_factory = adviser_factory

    def read_intake(self, user_id: str) -> CandidateIntakeRead:
        record = self._intake_record(user_id)
        if record is None:
            return CandidateIntakeRead(
                data=CandidateIntakeProfileData(),
                revision=0,
                confirmed=False,
            )
        return CandidateIntakeRead(
            data=CandidateIntakeProfileData.model_validate(json.loads(record.structured_json)),
            revision=record.revision,
            confirmed=record.confirmed,
        )

    def save_intake(
        self,
        user_id: str,
        data: CandidateIntakeProfileData,
    ) -> CandidateIntakeRead:
        record = self._intake_record(user_id)
        encoded = json.dumps(data.model_dump(mode="json"), ensure_ascii=False)
        if record is None:
            record = CandidateIntakeProfile(
                user_id=user_id,
                structured_json=encoded,
                revision=1,
                confirmed=False,
            )
            self._session.add(record)
        else:
            existing = CandidateIntakeProfileData.model_validate(
                json.loads(record.structured_json)
            )
            if existing == data:
                return self.read_intake(user_id)
            record.structured_json = encoded
            record.revision += 1
            record.confirmed = False
            record.confirmed_at = None
        self._session.commit()
        self._session.refresh(record)
        return self.read_intake(user_id)

    def confirm_intake(self, user_id: str) -> CandidateIntakeRead:
        record = self._intake_record(user_id)
        if record is None:
            raise LookupError("Candidate adviser intake not found.")
        intake = CandidateIntakeProfileData.model_validate(json.loads(record.structured_json))
        if not intake_source_refs(intake):
            raise ValueError("Candidate adviser intake must contain at least one meaningful answer.")
        record.confirmed = True
        record.confirmed_at = datetime.now(timezone.utc)
        self._session.commit()
        self._session.refresh(record)
        return self.read_intake(user_id)

    def assess(self, user_id: str) -> CandidateAdviserAssessmentRead:
        self._structured_profile(user_id)
        intake_record = self._confirmed_intake_record(user_id)
        intake = CandidateIntakeProfileData.model_validate(json.loads(intake_record.structured_json))
        context = PersistedCandidateContextLoader(self._session).load(
            user_id,
            include_adviser=False,
        )
        source_context = candidate_adviser_source_context(context)
        semantic_intake = compact_candidate_intake(intake)
        allowed_evidence_ids = [item.evidence_id for item in source_context.evidence]
        allowed_intake_refs = sorted(intake_source_refs(semantic_intake))
        assessment = self._semantic_adviser().assess(
            source_context,
            semantic_intake,
            allowed_evidence_ids=allowed_evidence_ids,
            allowed_intake_refs=allowed_intake_refs,
        )
        try:
            self._validate_refs(
                assessment,
                allowed_evidence_ids=set(allowed_evidence_ids),
                allowed_intake_refs=set(allowed_intake_refs),
            )
        except ValueError as exc:
            raise SemanticOutputError(str(exc)) from exc
        fingerprint = candidate_adviser_input_fingerprint(
            candidate_context=context,
            intake=intake,
        )
        record = self._assessment_record(user_id)
        encoded = json.dumps(assessment.model_dump(mode="json"), ensure_ascii=False)
        if record is None:
            record = CandidateAdviserAssessmentRecord(
                user_id=user_id,
                structured_json=encoded,
                input_fingerprint=fingerprint,
                state=CandidateAdviserState.REVIEW_READY.value,
            )
            self._session.add(record)
        else:
            record.structured_json = encoded
            record.input_fingerprint = fingerprint
            record.state = CandidateAdviserState.REVIEW_READY.value
        self._session.commit()
        self._session.refresh(record)
        return self.read_assessment(user_id)

    def read_assessment(self, user_id: str) -> CandidateAdviserAssessmentRead:
        record = self._assessment_record(user_id)
        if record is None:
            raise LookupError("Candidate adviser assessment not found.")
        assessment = CandidateAdviserAssessment.model_validate(json.loads(record.structured_json))
        current = self._current_fingerprint(user_id)
        state = (
            CandidateAdviserState.STALE
            if current is None or current != record.input_fingerprint
            else CandidateAdviserState(record.state)
        )
        return CandidateAdviserAssessmentRead(
            state=state,
            assessment=assessment,
            input_fingerprint=record.input_fingerprint,
        )

    def edit_assessment(
        self,
        user_id: str,
        corrected: CandidateAdviserAssessment,
    ) -> CandidateAdviserAssessmentRead:
        record = self._assessment_record(user_id)
        if record is None:
            raise LookupError("Candidate adviser assessment not found.")
        current = self.read_assessment(user_id)
        if current.state != CandidateAdviserState.REVIEW_READY:
            raise ValueError("Only a current review-ready adviser assessment can be edited.")
        intake = self._confirmed_intake(user_id)
        context = PersistedCandidateContextLoader(self._session).load(
            user_id,
            include_adviser=False,
        )
        source_context = candidate_adviser_source_context(context)
        semantic_intake = compact_candidate_intake(intake)
        self._validate_refs(
            corrected,
            allowed_evidence_ids={item.evidence_id for item in source_context.evidence},
            allowed_intake_refs=intake_source_refs(semantic_intake),
        )
        record.structured_json = json.dumps(corrected.model_dump(mode="json"), ensure_ascii=False)
        self._session.commit()
        self._session.refresh(record)
        return self.read_assessment(user_id)

    def confirm_assessment(self, user_id: str) -> CandidateAdviserAssessmentRead:
        record = self._assessment_record(user_id)
        if record is None:
            raise LookupError("Candidate adviser assessment not found.")
        current = self.read_assessment(user_id)
        if current.state == CandidateAdviserState.CONFIRMED:
            return current
        if current.state != CandidateAdviserState.REVIEW_READY:
            raise ValueError("Only a current review-ready adviser assessment can be confirmed.")
        record.state = CandidateAdviserState.CONFIRMED.value
        self._session.commit()
        self._session.refresh(record)
        return self.read_assessment(user_id)

    def _semantic_adviser(self) -> CandidateAdviserAgent:
        if self._adviser is None:
            if self._adviser_factory is None:
                raise ValueError("No semantic candidate adviser is configured.")
            self._adviser = self._adviser_factory()
        return self._adviser

    def _current_fingerprint(self, user_id: str) -> str | None:
        structured = self._session.scalar(
            select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id)
        )
        intake_record = self._intake_record(user_id)
        if structured is None or intake_record is None or not intake_record.confirmed:
            return None
        intake = CandidateIntakeProfileData.model_validate(json.loads(intake_record.structured_json))
        context = PersistedCandidateContextLoader(self._session).load(
            user_id,
            include_adviser=False,
        )
        return candidate_adviser_input_fingerprint(
            candidate_context=context,
            intake=intake,
        )

    def _structured_profile(self, user_id: str) -> CandidateStructuredProfile:
        structured = self._session.scalar(
            select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id)
        )
        if structured is None:
            raise ValueError("Confirm a CV before running candidate adviser assessment.")
        return structured

    def _confirmed_intake_record(self, user_id: str) -> CandidateIntakeProfile:
        record = self._intake_record(user_id)
        if record is None or not record.confirmed:
            raise ValueError("Confirm candidate adviser intake before assessment.")
        return record

    def _confirmed_intake(self, user_id: str) -> CandidateIntakeProfileData:
        record = self._confirmed_intake_record(user_id)
        return CandidateIntakeProfileData.model_validate(json.loads(record.structured_json))

    def _intake_record(self, user_id: str) -> CandidateIntakeProfile | None:
        return self._session.scalar(
            select(CandidateIntakeProfile).where(CandidateIntakeProfile.user_id == user_id)
        )

    def _assessment_record(self, user_id: str) -> CandidateAdviserAssessmentRecord | None:
        return self._session.scalar(
            select(CandidateAdviserAssessmentRecord).where(
                CandidateAdviserAssessmentRecord.user_id == user_id
            )
        )

    @staticmethod
    def _validate_refs(
        assessment: CandidateAdviserAssessment,
        *,
        allowed_evidence_ids: set[str],
        allowed_intake_refs: set[str],
    ) -> None:
        for finding in [*assessment.strengths, *assessment.transferable_capabilities]:
            if not finding.supporting_refs:
                raise ValueError("Candidate adviser returned an unsupported positive finding.")
        for hypothesis in assessment.role_hypotheses:
            if not hypothesis.supporting_refs:
                raise ValueError("Candidate adviser returned an unsupported role hypothesis.")

        refs = [
            ref
            for finding in [
                *assessment.strengths,
                *assessment.transferable_capabilities,
                *assessment.development_gaps,
            ]
            for ref in finding.supporting_refs
        ]
        refs.extend(
            ref
            for hypothesis in assessment.role_hypotheses
            for ref in hypothesis.supporting_refs
        )
        for ref in refs:
            if (
                ref.source_type == CandidateIntakeSourceType.CAREER_EVIDENCE
                and ref.source_ref not in allowed_evidence_ids
            ):
                raise ValueError("Candidate adviser referenced unknown career evidence.")
            if (
                ref.source_type == CandidateIntakeSourceType.CANDIDATE_INTAKE
                and ref.source_ref not in allowed_intake_refs
            ):
                raise ValueError("Candidate adviser referenced unsupported intake data.")

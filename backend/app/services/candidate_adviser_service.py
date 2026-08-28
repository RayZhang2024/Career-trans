import hashlib
import json
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.candidate_adviser import CandidateAdviserAgent
from app.models.candidate_adviser import CandidateAdviserAssessmentRecord, CandidateAdviserIntakeRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.schemas.candidate_adviser import (
    AdviserInsight,
    CandidateAdviserAssessmentContent,
    CandidateAdviserAssessmentRead,
    CandidateAdviserAssessmentStatus,
    CandidateAdviserIntake,
    CandidateAdviserIntakeRead,
    CandidateAdviserSemanticInput,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.services.candidate_adviser_compaction import compact_candidate_adviser_input
from app.services.career_evidence_fingerprint import career_evidence_fingerprint


class CandidateAdviserService:
    def __init__(self, session: Session, *, agent: CandidateAdviserAgent | None = None) -> None:
        self._session = session
        self._agent = agent

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
        self._validate_sources(content, intake, {item.evidence_id for item in semantic_input.career_evidence})
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

    def input_fingerprint(self, user_id: str, *, semantic_input: CandidateAdviserSemanticInput | None = None) -> str:
        payload = (semantic_input or self._semantic_input(user_id)).model_dump(mode="json")
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _intake(self, user_id: str) -> CandidateAdviserIntake:
        found = self.get_intake(user_id)
        if found is None:
            raise ValueError("Candidate adviser intake has not been provided.")
        return CandidateAdviserIntake.model_validate(found.model_dump(exclude={"updated_at"}))

    def _semantic_input(self, user_id: str, *, intake: CandidateAdviserIntake | None = None) -> CandidateAdviserSemanticInput:
        structured = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
        data = CandidateCVData.model_validate(json.loads(structured.structured_json)) if structured else CandidateCVData()
        records = list(
            self._session.scalars(
                select(CandidateEvidenceRecord).where(
                    CandidateEvidenceRecord.user_id == user_id
                )
            )
        )
        records_by_fingerprint = {record.fingerprint: record for record in records}
        ordered_records: list[CandidateEvidenceRecord] = []
        for item in data.evidence:
            record = records_by_fingerprint.pop(career_evidence_fingerprint(item), None)
            if record is not None:
                ordered_records.append(record)
        ordered_records.extend(
            sorted(
                records_by_fingerprint.values(),
                key=lambda record: (record.created_at, record.id),
            )
        )
        evidence = [
            {
                "evidence_id": record.id,
                "title": record.title,
                "text": record.text,
                "skills": json.loads(record.skills_json),
            }
            for record in ordered_records
        ]
        return compact_candidate_adviser_input(
            intake=intake or self._intake(user_id),
            structured_cv=data,
            career_evidence=evidence,
        )

    @staticmethod
    def _validate_sources(content: CandidateAdviserAssessmentContent, intake: CandidateAdviserIntake, evidence_ids: set[str]) -> None:
        allowed_paths = CandidateAdviserService._intake_paths(intake)
        for insight in CandidateAdviserService._insights(content):
            for reference in insight.source_references:
                if reference.source_type == "career_evidence" and reference.reference not in evidence_ids:
                    raise ValueError("Candidate adviser output referenced evidence that was not supplied.")
                if reference.source_type == "intake" and reference.reference not in allowed_paths:
                    raise ValueError("Candidate adviser output referenced an intake field that was not supplied.")

    @staticmethod
    def _intake_paths(intake: CandidateAdviserIntake) -> set[str]:
        paths = {
            name
            for name in ("career_direction", "work_preferences", "constraints", "self_assessment", "motivations", "tradeoffs")
            if getattr(intake, name)
        }
        for name in ("work_authorisation", "security_clearances", "locations"):
            if getattr(intake.eligibility, name):
                paths.add(f"eligibility.{name}")
        return paths

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

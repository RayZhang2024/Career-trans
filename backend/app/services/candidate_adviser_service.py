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
)


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
        changed = record is None or record.intake_json != encoded
        if record is None:
            record = CandidateAdviserIntakeRecord(user_id=user_id, intake_json=encoded)
            self._session.add(record)
        else:
            record.intake_json = encoded
        assessment = self._session.scalar(select(CandidateAdviserAssessmentRecord).where(CandidateAdviserAssessmentRecord.user_id == user_id))
        if assessment is not None and changed:
            assessment.status = CandidateAdviserAssessmentStatus.STALE
        self._session.commit()
        self._session.refresh(record)
        return CandidateAdviserIntakeRead(**intake.model_dump(mode="json"), updated_at=record.updated_at)

    def assess(self, user_id: str) -> CandidateAdviserAssessmentRead:
        if self._agent is None:
            raise ValueError("No semantic candidate adviser is configured.")
        intake = self._intake(user_id)
        evidence = self._evidence(user_id)
        if not self._session.scalar(select(CandidateStructuredProfile.id).where(CandidateStructuredProfile.user_id == user_id)):
            raise ValueError("Candidate adviser requires a confirmed CV before assessment.")
        content = self._agent.assess(intake=intake, evidence=evidence)
        self._validate_sources(content, intake, {str(item["evidence_id"]) for item in evidence})
        fingerprint = self.input_fingerprint(user_id, intake=intake)
        record = self._session.scalar(select(CandidateAdviserAssessmentRecord).where(CandidateAdviserAssessmentRecord.user_id == user_id))
        encoded = json.dumps(content.model_dump(mode="json"), sort_keys=True)
        if record is None:
            record = CandidateAdviserAssessmentRecord(user_id=user_id, input_fingerprint=fingerprint, status=CandidateAdviserAssessmentStatus.CURRENT, assessment_json=encoded)
            self._session.add(record)
        else:
            record.input_fingerprint = fingerprint
            record.status = CandidateAdviserAssessmentStatus.CURRENT
            record.assessment_json = encoded
        self._session.commit()
        self._session.refresh(record)
        return self._read_assessment(record, fingerprint)

    def get_assessment(self, user_id: str) -> CandidateAdviserAssessmentRead | None:
        record = self._session.scalar(select(CandidateAdviserAssessmentRecord).where(CandidateAdviserAssessmentRecord.user_id == user_id))
        if record is None:
            return None
        fingerprint = self.input_fingerprint(user_id)
        derived_status = CandidateAdviserAssessmentStatus.CURRENT if record.input_fingerprint == fingerprint else CandidateAdviserAssessmentStatus.STALE
        if record.status != derived_status:
            record.status = derived_status
            self._session.commit()
            self._session.refresh(record)
        return self._read_assessment(record, fingerprint)

    def current_assessment(self, user_id: str) -> CandidateAdviserAssessmentRead | None:
        assessment = self.get_assessment(user_id)
        return assessment if assessment and assessment.status is CandidateAdviserAssessmentStatus.CURRENT else None

    def input_fingerprint(self, user_id: str, *, intake: CandidateAdviserIntake | None = None) -> str:
        resolved_intake = intake or self._intake(user_id)
        structured = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
        records = list(self._session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id).order_by(CandidateEvidenceRecord.id)))
        payload = {
            "intake": resolved_intake.model_dump(mode="json"),
            "structured_profile": json.loads(structured.structured_json) if structured else None,
            "evidence": [
                {"id": record.id, "fingerprint": record.fingerprint}
                for record in records
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _intake(self, user_id: str) -> CandidateAdviserIntake:
        found = self.get_intake(user_id)
        if found is None:
            raise ValueError("Candidate adviser intake has not been provided.")
        return CandidateAdviserIntake.model_validate(found.model_dump(exclude={"updated_at"}))

    def _evidence(self, user_id: str) -> list[dict[str, object]]:
        records = list(self._session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id).order_by(CandidateEvidenceRecord.created_at, CandidateEvidenceRecord.id)))
        return [
            {
                "evidence_id": record.id,
                "title": record.title,
                "text": record.text[:1200],
                "skills": json.loads(record.skills_json),
            }
            for record in records[:24]
        ]

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
        status = CandidateAdviserAssessmentStatus.CURRENT if record.input_fingerprint == fingerprint else CandidateAdviserAssessmentStatus.STALE
        return CandidateAdviserAssessmentRead(
            input_fingerprint=record.input_fingerprint,
            status=status,
            content=CandidateAdviserAssessmentContent.model_validate(json.loads(record.assessment_json)),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_adviser import CandidateAdviserAssessmentRecord
from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.schemas.candidate import (
    CandidateContext,
    CandidateContextSummary,
    CandidateEvidenceMaterializationStatus,
    CandidateEligibility,
)
from app.schemas.candidate_adviser import CandidateAdviserAssessmentStatus
from app.schemas.candidate_read_snapshot import (
    CandidateAdviserReadStatus,
    CandidateReadiness,
    CanonicalCandidateReadSnapshot,
)
from app.schemas.candidate_profile import CandidateProfileRead
from app.schemas.cv_ingestion import CandidateCVData
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_adviser_service import CandidateAdviserService


class CandidateEvidenceMaterializationIncomplete(RuntimeError):
    """Raised when a workflow would otherwise consume a truncated evidence set."""


class CanonicalCandidateReadService:
    """Read current candidate authorities without reconciliation or persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def read(self, user_id: str) -> CanonicalCandidateReadSnapshot:
        # Suppress autoflush as well as explicit flush/commit: a read snapshot
        # must not persist pending ORM state as a side effect of its queries.
        with self._session.no_autoflush:
            profile_row = self._session.scalar(
                select(CandidateProfile).where(CandidateProfile.user_id == user_id)
            )
            structured_row = self._session.scalar(
                select(CandidateStructuredProfile).where(
                    CandidateStructuredProfile.user_id == user_id
                )
            )
            data = (
                CandidateCVData.model_validate_json(structured_row.structured_json)
                if structured_row is not None
                else None
            )
            evidence_read = ActiveCandidateEvidenceResolver(self._session).inspect_active(
                user_id, data or CandidateCVData()
            )

            adviser = CandidateAdviserService(self._session)
            intake = adviser.get_intake(user_id)
            assessment_exists = self._session.scalar(
                select(CandidateAdviserAssessmentRecord.id).where(
                    CandidateAdviserAssessmentRecord.user_id == user_id
                )
            ) is not None
            assessment_status = CandidateAdviserReadStatus.NOT_AVAILABLE
            assessment_content = None
            if assessment_exists:
                if intake is None:
                    assessment_status = CandidateAdviserReadStatus.UNAVAILABLE
                elif not evidence_read.complete:
                    # A partial evidence set cannot establish trusted Adviser
                    # currentness, even if its stored fingerprint happens to
                    # match that partial projection.
                    assessment_status = CandidateAdviserReadStatus.UNAVAILABLE
                else:
                    assessment = adviser.get_assessment_read_only(user_id)
                    if assessment is None:
                        assessment_status = CandidateAdviserReadStatus.UNAVAILABLE
                    elif assessment.status is CandidateAdviserAssessmentStatus.STALE:
                        assessment_status = CandidateAdviserReadStatus.STALE
                    elif assessment.status is CandidateAdviserAssessmentStatus.REVIEW_READY:
                        assessment_status = CandidateAdviserReadStatus.REVIEW_READY
                    elif assessment.status is CandidateAdviserAssessmentStatus.CONFIRMED:
                        assessment_status = CandidateAdviserReadStatus.CONFIRMED
                        assessment_content = assessment.content

            latest_draft = self._session.scalar(
                select(CandidateCVIngestionDraft)
                .where(CandidateCVIngestionDraft.user_id == user_id)
                .order_by(
                    CandidateCVIngestionDraft.created_at.desc(),
                    CandidateCVIngestionDraft.id.desc(),
                )
                .limit(1)
            )

        if structured_row is None and evidence_read.expected_count == 0:
            materialization_status = CandidateEvidenceMaterializationStatus.NOT_APPLICABLE
        elif evidence_read.complete:
            materialization_status = CandidateEvidenceMaterializationStatus.COMPLETE
        else:
            materialization_status = CandidateEvidenceMaterializationStatus.INCOMPLETE
        readiness = CandidateReadiness(
            structured_profile_available=structured_row is not None,
            ready_for_candidate_context=(
                structured_row is not None and evidence_read.complete
            ),
            evidence_materialization_status=materialization_status,
            expected_evidence_count=evidence_read.expected_count,
            materialized_evidence_count=len(evidence_read.evidence),
            missing_evidence_count=len(evidence_read.missing_fingerprints),
            latest_cv_draft_state=latest_draft.state if latest_draft else None,
        )
        return CanonicalCandidateReadSnapshot(
            profile=(CandidateProfileRead.model_validate(profile_row) if profile_row else None),
            structured_profile=data,
            active_evidence=list(evidence_read.evidence),
            adviser_intake=intake,
            eligibility=(intake.eligibility if intake else CandidateEligibility()),
            adviser_assessment=assessment_content,
            adviser_assessment_status=assessment_status,
            readiness=readiness,
        )

    @staticmethod
    def candidate_context(
        snapshot: CanonicalCandidateReadSnapshot,
        *,
        require_structured_profile: bool = False,
        require_complete_evidence: bool = False,
    ) -> CandidateContext | None:
        if require_structured_profile and not snapshot.readiness.structured_profile_available:
            return None
        if (
            require_complete_evidence
            and snapshot.readiness.evidence_materialization_status
            is CandidateEvidenceMaterializationStatus.INCOMPLETE
        ):
            raise CandidateEvidenceMaterializationIncomplete(
                "Current candidate evidence is not fully materialised."
            )

        profile = snapshot.profile
        data = snapshot.structured_profile or CandidateCVData()
        profile_text = " ".join(
            value
            for value in (
                [profile.headline, profile.current_role, profile.summary, profile.location]
                if profile
                else []
            )
            if value
        )
        employment_text = "\n".join(
            f"{item.title} at {item.employer}. {item.description}"
            for item in data.employment
        )
        education_text = "\n".join(
            f"{item.qualification} at {item.institution}. {item.description}"
            for item in data.education
        )
        assessment = snapshot.adviser_assessment
        intake = snapshot.adviser_intake
        career_strategy_parts = [
            profile.career_goal if profile and profile.career_goal else "",
            intake.career_direction if intake else "",
            assessment.career_strategy_summary.text if assessment else "",
            *((item.text for item in assessment.role_hypotheses) if assessment else ()),
        ]
        criteria_parts = [
            profile.job_search_criteria if profile and profile.job_search_criteria else "",
            *(intake.work_preferences if intake else []),
            *(intake.constraints if intake else []),
            *(intake.tradeoffs if intake else []),
            assessment.job_search_strategy_summary.text if assessment else "",
        ]
        return CandidateContext(
            profile_text="\n".join(
                value for value in [profile_text, employment_text, education_text] if value
            ),
            skills_text=", ".join(item.name for item in data.skills),
            career_strategy_text="\n".join(value for value in career_strategy_parts if value),
            job_search_criteria_text="\n".join(value for value in criteria_parts if value),
            eligibility=snapshot.eligibility,
            evidence=snapshot.active_evidence,
        )

    @staticmethod
    def summary(snapshot: CanonicalCandidateReadSnapshot) -> CandidateContextSummary:
        data = snapshot.structured_profile or CandidateCVData()
        profile = snapshot.profile
        readiness = snapshot.readiness
        return CandidateContextSummary(
            ready=readiness.ready_for_candidate_context,
            employment_count=len(data.employment),
            education_count=len(data.education),
            skill_count=len(data.skills),
            evidence_count=readiness.materialized_evidence_count,
            structured_profile_available=readiness.structured_profile_available,
            evidence_materialization_status=readiness.evidence_materialization_status,
            expected_evidence_count=readiness.expected_evidence_count,
            missing_evidence_count=readiness.missing_evidence_count,
            career_strategy_configured=bool(
                profile and profile.career_goal and profile.career_goal.strip()
            ),
            job_search_criteria_configured=bool(
                profile
                and profile.job_search_criteria
                and profile.job_search_criteria.strip()
            ),
        )

"""Provider-free, user-scoped reads for the canonical job workspace."""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.discovered_job import DiscoveredJob
from app.models.discovered_job_provenance import DiscoveredJobProvenance
from app.models.user_job_discovery import UserJobEvaluation
from app.schemas.job_workspace import (
    JobWorkspaceRead,
    WorkspaceCurrentFitRead,
    WorkspaceCurrentFitReason,
    WorkspaceCurrentFitStatus,
    WorkspaceEvaluationApplicability,
    WorkspaceEvaluationRead,
    WorkspaceEvaluationResponse,
    WorkspaceJobRead,
    WorkspaceProvenanceRead,
    WorkspaceProvenanceResponse,
)
from app.services.canonical_candidate_read_service import (
    CandidateEvidenceMaterializationIncomplete,
    CanonicalCandidateReadService,
)
from app.services.llm_runtime import ResolvedRuntimeSnapshot
from app.services.posting_legitimacy_service import PostingLegitimacyService
from app.services.public_job_actionability import is_public_job_actionable
from app.services.semantic_runtime_attribution import read_attribution
from app.services.user_job_discovery_service import reusable_current_evaluation, UserJobDiscoveryService
from app.schemas.job_ranking import RankedJobOpportunity


class UserJobWorkspaceReadService:
    """Read one canonical job without requiring a provider or eager runtime setup."""

    def __init__(
        self,
        session: Session,
        *,
        candidate_reader: CanonicalCandidateReadService | None = None,
        runtime_snapshot_resolver: Callable[[str], ResolvedRuntimeSnapshot] | None = None,
    ) -> None:
        self._session = session
        self._candidate_reader = candidate_reader or CanonicalCandidateReadService(session)
        self._runtime_snapshot_resolver = runtime_snapshot_resolver

    def read(self, user_id: str, discovered_job_id: str, *, provenance_limit: int = 20, evaluation_limit: int = 20) -> JobWorkspaceRead:
        job = self._session.get(DiscoveredJob, discovered_job_id)
        if job is None:
            raise LookupError("Discovered job not found.")
        provenance_rows = self._session.scalars(
            select(DiscoveredJobProvenance).where(DiscoveredJobProvenance.job_id == job.id)
            .order_by(DiscoveredJobProvenance.imported_at.desc(), DiscoveredJobProvenance.id.asc()).limit(provenance_limit + 1)
        ).all()
        evaluation_rows = self._session.scalars(
            select(UserJobEvaluation).where(UserJobEvaluation.user_id == user_id, UserJobEvaluation.discovered_job_id == job.id)
            .order_by(UserJobEvaluation.created_at.desc(), UserJobEvaluation.id.asc()).limit(evaluation_limit + 1)
        ).all()

        current_evaluation = None
        current_reason: WorkspaceCurrentFitReason | None = None
        current_unavailable = False
        if not is_public_job_actionable(job):
            current_reason = WorkspaceCurrentFitReason.JOB_NOT_ACTIONABLE
        elif self._runtime_snapshot_resolver is None:
            current_reason = WorkspaceCurrentFitReason.RUNTIME_CONFIGURATION_UNAVAILABLE
            current_unavailable = True
        else:
            try:
                snapshot = self._candidate_reader.read(user_id)
                context = self._candidate_reader.candidate_context(snapshot, require_structured_profile=True, require_complete_evidence=True)
            except CandidateEvidenceMaterializationIncomplete:
                context = None
                current_reason = WorkspaceCurrentFitReason.CANDIDATE_EVIDENCE_INCOMPLETE
                current_unavailable = True
            except Exception:
                context = None
                current_reason = WorkspaceCurrentFitReason.CANDIDATE_NOT_READY
                current_unavailable = True
            if current_reason is None:
                if context is None:
                    current_reason = WorkspaceCurrentFitReason.CANDIDATE_NOT_READY
                    current_unavailable = True
                else:
                    try:
                        runtime = self._runtime_snapshot_resolver(user_id)
                        current_evaluation = reusable_current_evaluation(
                            self._session, user_id, job, candidate_reader=self._candidate_reader,
                            runtime_snapshot=runtime, candidate_context=context,
                        )
                    except Exception:
                        current_reason = WorkspaceCurrentFitReason.RUNTIME_CONFIGURATION_UNAVAILABLE
                        current_unavailable = True
                    if current_reason is None and current_evaluation is None:
                        current_reason = WorkspaceCurrentFitReason.NO_CURRENT_EVALUATION

        current_id = current_evaluation.id if current_evaluation is not None else None
        visible_rows = evaluation_rows[:evaluation_limit]
        evaluations = [self._evaluation_read(row, WorkspaceEvaluationApplicability.UNKNOWN if current_unavailable else (WorkspaceEvaluationApplicability.CURRENT if row.id == current_id else WorkspaceEvaluationApplicability.HISTORICAL), job=job, current=row.id == current_id) for row in visible_rows]
        current_fit = WorkspaceCurrentFitRead(
            status=WorkspaceCurrentFitStatus.UNAVAILABLE if current_unavailable else (WorkspaceCurrentFitStatus.CURRENT if current_evaluation is not None else WorkspaceCurrentFitStatus.NONE),
            reason=None if current_evaluation is not None else current_reason,
            evaluation=self._evaluation_read(current_evaluation, WorkspaceEvaluationApplicability.CURRENT, job=job, current=True) if current_evaluation is not None else None,
        )
        return JobWorkspaceRead(
            job=WorkspaceJobRead(
                id=job.id, title=job.title, company=job.company, location=job.location, url=job.url, description=job.description,
                posted_at=job.posted_at, work_arrangement=job.work_arrangement, employment_type=job.employment_type,
                detail_authority=job.detail_authority, verification_status=job.verification_status, verification_reason=job.verification_reason,
                state=job.state, actionable=is_public_job_actionable(job), first_seen_at=job.first_seen_at,
                last_seen_at=job.last_seen_at, last_changed_at=job.last_changed_at,
            ),
            provenance=WorkspaceProvenanceResponse(
                items=[WorkspaceProvenanceRead.model_validate(row, from_attributes=True) for row in provenance_rows[:provenance_limit]],
                limit=provenance_limit, truncated=len(provenance_rows) > provenance_limit,
            ),
            current_fit=current_fit,
            evaluations=WorkspaceEvaluationResponse(items=evaluations, limit=evaluation_limit, truncated=len(evaluation_rows) > evaluation_limit),
        )

    @staticmethod
    def _evaluation_read(row: UserJobEvaluation, applicability: WorkspaceEvaluationApplicability, *, job: DiscoveredJob, current: bool) -> WorkspaceEvaluationRead:
        opportunity = RankedJobOpportunity.model_validate_json(row.evaluation_json)
        if current:
            opportunity = opportunity.model_copy(update={"legitimacy": PostingLegitimacyService().assess(UserJobDiscoveryService._listing(job))})
        return WorkspaceEvaluationRead(
            id=row.id, created_at=row.created_at, applicability=applicability, opportunity=opportunity,
            runtime_attribution=read_attribution(row.runtime_attribution_json),
        )

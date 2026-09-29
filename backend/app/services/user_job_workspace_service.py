"""Provider-free, user-scoped reads for the canonical job workspace."""

from collections.abc import Callable
from datetime import datetime, timezone
import json

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.models.application_preparation import ApplicationPreparation
from app.models.application_tracking import ApplicationTrackingRecord
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
    WorkspaceApplicationRead,
    WorkspaceApplicationResponse,
    WorkspaceApplicationResultSummary,
    WorkspaceApplicationTargetRead,
    WorkspaceApplicationTrackingSummary,
    WorkspaceJobRead,
    WorkspaceProvenanceRead,
    WorkspaceProvenanceResponse,
)
from app.services.canonical_candidate_read_service import (
    CandidateEvidenceMaterializationIncomplete,
    CanonicalCandidateReadService,
)
from app.services.llm_runtime import ResolvedRuntimeSnapshot
from app.services.llm_runtime import RuntimePreferenceError
from app.services.posting_legitimacy_service import PostingLegitimacyService
from app.services.public_job_actionability import is_public_job_actionable
from app.services.semantic_runtime_attribution import read_attribution
from app.services.user_job_discovery_service import reusable_current_evaluation, UserJobDiscoveryService
from app.services.user_job_decision_service import UserJobDecisionService
from app.schemas.job_ranking import RankedJobOpportunity
from app.services.application_preparation_service import _decode_persisted_result


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

    def read(
        self,
        user_id: str,
        discovered_job_id: str,
        *,
        provenance_limit: int = 20,
        evaluation_limit: int = 20,
        application_limit: int = 20,
    ) -> JobWorkspaceRead:
        job = self._session.get(DiscoveredJob, discovered_job_id)
        if job is None:
            raise LookupError("Discovered job not found.")
        provenance_count = self._session.scalar(
            select(func.count()).select_from(DiscoveredJobProvenance).where(DiscoveredJobProvenance.job_id == job.id)
        ) or 0
        provenance_rows = self._session.scalars(
            select(DiscoveredJobProvenance).where(DiscoveredJobProvenance.job_id == job.id)
            .order_by(DiscoveredJobProvenance.imported_at.desc(), DiscoveredJobProvenance.id.asc()).limit(provenance_limit + 1)
        ).all()
        evaluation_rows = self._session.scalars(
            select(UserJobEvaluation).where(UserJobEvaluation.user_id == user_id, UserJobEvaluation.discovered_job_id == job.id)
            .order_by(UserJobEvaluation.created_at.desc(), UserJobEvaluation.id.asc()).limit(evaluation_limit + 1)
        ).all()
        target_json_valid = func.json_valid(ApplicationPreparation.target_snapshot_json) == 1
        canonical_id = case(
            (target_json_valid, func.json_extract(ApplicationPreparation.target_snapshot_json, "$.canonical_discovered_job_id")),
            else_=None,
        )
        source_kind = case(
            (target_json_valid, func.json_extract(ApplicationPreparation.target_snapshot_json, "$.source_kind")),
            else_=None,
        )
        target_title = case(
            (target_json_valid, func.json_extract(ApplicationPreparation.target_snapshot_json, "$.title")),
            else_=None,
        )
        target_hash = case(
            (target_json_valid, func.json_extract(ApplicationPreparation.target_snapshot_json, "$.job_content_hash")),
            else_=None,
        )
        application_rows = self._session.scalars(
            select(ApplicationPreparation)
            .where(
                ApplicationPreparation.user_id == user_id,
                canonical_id == job.id,
                source_kind.is_not(None),
                target_title.is_not(None),
                target_hash.is_not(None),
            )
            .order_by(ApplicationPreparation.created_at.desc(), ApplicationPreparation.id.asc())
            .limit(application_limit + 1)
        ).all()
        visible_application_rows = application_rows[:application_limit]
        tracking_by_preparation = self._tracking_projection(user_id, visible_application_rows)

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
                    except RuntimePreferenceError:
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
                count=provenance_count,
                limit=provenance_limit, truncated=len(provenance_rows) > provenance_limit,
            ),
            current_fit=current_fit,
            evaluations=WorkspaceEvaluationResponse(items=evaluations, limit=evaluation_limit, truncated=len(evaluation_rows) > evaluation_limit),
            decision=UserJobDecisionService(self._session).read(user_id, job.id),
            applications=WorkspaceApplicationResponse(
                items=[item for row in visible_application_rows if (item := self._application_read(row, job, tracking_by_preparation.get(row.id))) is not None],
                limit=application_limit,
                truncated=len(application_rows) > application_limit,
            ),
        )

    def _tracking_projection(
        self, user_id: str, preparations: list[ApplicationPreparation]
    ) -> dict[str, ApplicationTrackingRecord]:
        if not preparations:
            return {}
        preparation_ids = [row.id for row in preparations]
        rows = self._session.execute(
            select(ApplicationTrackingRecord)
            .join(ApplicationPreparation, ApplicationPreparation.id == ApplicationTrackingRecord.preparation_id)
            .where(
                ApplicationPreparation.user_id == user_id,
                ApplicationTrackingRecord.preparation_id.in_(preparation_ids),
            )
        ).scalars().all()
        return {row.preparation_id: row for row in rows}

    @staticmethod
    def _application_read(
        row: ApplicationPreparation,
        job: DiscoveredJob,
        tracking: ApplicationTrackingRecord | None,
    ) -> WorkspaceApplicationRead | None:
        try:
            target = json.loads(row.target_snapshot_json)
            target_read = WorkspaceApplicationTargetRead(
                source_kind=target["source_kind"],
                canonical_discovered_job_id=target.get("canonical_discovered_job_id"),
                title=target["title"], company=target.get("company"), location=target.get("location"),
                public_url=target.get("public_url"), work_arrangement=target.get("work_arrangement"),
                employment_type=target.get("employment_type"), job_content_hash=target["job_content_hash"],
            )
        except (TypeError, KeyError, json.JSONDecodeError, ValidationError):
            # A malformed target cannot be safely associated with a workspace.
            return None
        result_summary = None
        try:
            result, _, _ = _decode_persisted_result(row.preparation_result_json)
            result_summary = WorkspaceApplicationResultSummary(
                layout_status=result.layout_status.value,
                target_pages=result.target_pages,
                actual_pdf_pages=result.actual_pdf_pages,
                has_cover_letter=result.cover_letter is not None,
                answer_count=len(result.answers),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            # A malformed legacy result must not make the rest of the workspace
            # unreadable, and no summary is invented for it.
            result_summary = None
        tracking_summary = None
        if tracking is not None:
            tracking_summary = WorkspaceApplicationTrackingSummary(
                id=tracking.id,
                preparation_id=tracking.preparation_id,
                current_status=tracking.current_status,
                revision=tracking.revision,
                created_at=_utc(tracking.created_at),
                updated_at=_utc(tracking.updated_at),
            )
        return WorkspaceApplicationRead(
            preparation_id=row.id,
            created_at=_utc(row.created_at),
            target=target_read,
            snapshot_status="current_job_content" if target.get("job_content_hash") == job.content_hash else "historical_job_content",
            result_summary=result_summary,
            tracking=tracking_summary,
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


def _utc(value: datetime) -> datetime:
    """SQLite may return timezone=True values as naive; restore UTC authority."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

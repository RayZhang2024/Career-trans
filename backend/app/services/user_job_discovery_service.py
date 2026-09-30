"""User-owned discovery runs over canonical, shared public job records."""

import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.discovered_job import DiscoveredJob
from app.models.user_job_discovery import DiscoveryRun, DiscoveryRunJob, UserJobEvaluation
from app.models.user_job_decision import UserJobDecision
from app.schemas.candidate import CandidateContext
from app.schemas.discovery import DiscoveredJobState, JobListing, JobVerificationStatus
from app.schemas.job_ranking import JobRankingRequest, JobRankingResponse, RankedJobOpportunity
from app.schemas.recommendation import Recommendation
from app.schemas.user_job_discovery import (
    DiscoveryRunCreateRequest, DiscoveryRunJobOutcome, DiscoveryRunJobRead,
    DiscoveryRunRead, DiscoveryRunStatus, UserOpportunityRead, UserOpportunityResponse,
    UserOpportunitySummary, UserOpportunitySummaryResponse, DiscoveryRunSummaryRead,
    DiscoveryRunSummaryResponse, DiscoveryRunJobSummaryRead, DiscoveryRunDetailRead,
    DiscoveryRunJobDetailRead,
)
from app.schemas.user_job_decision import UserJobDecisionRead, UserJobDecisionValue
from app.services.candidate_profile_compaction import candidate_career_profile, candidate_matching_profile, candidate_search_profile
from app.services.canonical_candidate_read_service import (
    CandidateEvidenceMaterializationIncomplete,
    CanonicalCandidateReadService,
)
from app.services.job_presemantic_selection_service import JobPresemanticSelectionService
from app.services.job_ranking_service import JobRankingService
from app.services.posting_legitimacy_service import PostingLegitimacyService
from app.services.public_job_actionability import is_public_job_actionable
from app.services.llm_runtime import JOB_EVALUATION_OPERATIONS, ResolvedRuntimeSnapshot, resolve_runtime_snapshot
from app.services.semantic_runtime_attribution import available_attribution, canonical_attribution_json, read_attribution
from app.services.user_job_decision_service import utc_timestamp


_CONTRACT_VERSION = "user-discovery-run-v1"


class DiscoveryRunExecutionFailure(RuntimeError):
    """Evaluation failed after its durable run row was committed."""

    def __init__(self, run_id: str, status: DiscoveryRunStatus = DiscoveryRunStatus.FAILED) -> None:
        super().__init__("Discovery evaluation failed after its run was created.")
        self.run_id = run_id
        self.status = status


def evaluation_contract_fingerprint_for_runtime(runtime_snapshot: ResolvedRuntimeSnapshot) -> str:
    return _fingerprint({
        "contract": _CONTRACT_VERSION,
        "revision": _application_revision(),
        "runtime": runtime_snapshot.fingerprint_projection(JOB_EVALUATION_OPERATIONS),
    })


def reusable_current_evaluation(
    session: Session,
    user_id: str,
    job: DiscoveredJob,
    *,
    candidate_reader: CanonicalCandidateReadService,
    runtime_snapshot: ResolvedRuntimeSnapshot,
    candidate_context: CandidateContext | None = None,
) -> UserJobEvaluation | None:
    """Shared authority for whether a stored evaluation is current for a job."""
    context = candidate_context
    if context is None:
        try:
            snapshot = candidate_reader.read(user_id)
            context = candidate_reader.candidate_context(
                snapshot,
                require_structured_profile=True,
                require_complete_evidence=True,
            )
        except CandidateEvidenceMaterializationIncomplete:
            return None
    if context is None or not is_public_job_actionable(job):
        return None
    candidate = UserJobDiscoveryService.candidate_evaluation_fingerprint(context)
    contract = evaluation_contract_fingerprint_for_runtime(runtime_snapshot)
    evaluation = session.scalar(select(UserJobEvaluation).where(
        UserJobEvaluation.user_id == user_id,
        UserJobEvaluation.discovered_job_id == job.id,
        UserJobEvaluation.job_content_hash == job.content_hash,
        UserJobEvaluation.candidate_evaluation_fingerprint == candidate,
        UserJobEvaluation.evaluation_contract_fingerprint == contract,
    ))
    if evaluation is None:
        return None
    try:
        result = RankedJobOpportunity.model_validate_json(evaluation.evaluation_json)
    except ValueError:
        return None
    return evaluation if result.job_profile is not None and result.requirement_matches else None


class UserJobDiscoveryHistoryReadService:
    """Settings-independent projections for persisted discovery history."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_run_summaries(self, user_id: str, *, limit: int) -> DiscoveryRunSummaryResponse:
        runs = self._session.scalars(
            select(DiscoveryRun).where(DiscoveryRun.user_id == user_id)
            .order_by(DiscoveryRun.started_at.desc(), DiscoveryRun.id.asc()).limit(limit + 1)
        ).all()
        return DiscoveryRunSummaryResponse(
            items=[self._run_summary(run) for run in runs[:limit]],
            limit=limit,
            truncated=len(runs) > limit,
        )

    def get_run_detail(self, user_id: str, run_id: str) -> DiscoveryRunDetailRead:
        run = self._owned_run(user_id, run_id)
        rows = self._session.scalars(
            select(DiscoveryRunJob).where(DiscoveryRunJob.discovery_run_id == run.id)
            .order_by(DiscoveryRunJob.created_at, DiscoveryRunJob.id)
        ).all()
        return DiscoveryRunDetailRead(
            **self._run_summary(run).model_dump(),
            jobs=[self._run_row_summary(row) for row in rows],
        )

    def get_historical_run_job_detail(self, user_id: str, run_id: str, discovered_job_id: str) -> DiscoveryRunJobDetailRead:
        run = self._owned_run(user_id, run_id)
        row = self._session.scalar(select(DiscoveryRunJob).where(
            DiscoveryRunJob.discovery_run_id == run.id,
            DiscoveryRunJob.discovered_job_id == discovered_job_id,
        ))
        if row is None:
            raise LookupError("Discovery run job not found.")
        evaluation = self._session.get(UserJobEvaluation, row.evaluation_id) if row.evaluation_id else None
        return DiscoveryRunJobDetailRead(
            discovered_job_id=row.discovered_job_id,
            evaluation_id=row.evaluation_id,
            outcome=row.outcome,
            failure_stage=row.failure_stage,
            failure_kind=row.failure_kind,
            opportunity=RankedJobOpportunity.model_validate_json(evaluation.evaluation_json) if evaluation else None,
            runtime_attribution=read_attribution(evaluation.runtime_attribution_json) if evaluation else None,
        )

    def _owned_run(self, user_id: str, run_id: str) -> DiscoveryRun:
        run = self._session.scalar(select(DiscoveryRun).where(DiscoveryRun.id == run_id, DiscoveryRun.user_id == user_id))
        if run is None:
            raise LookupError("Discovery run not found.")
        return run

    @staticmethod
    def _run_summary(run: DiscoveryRun) -> DiscoveryRunSummaryRead:
        return DiscoveryRunSummaryRead(
            id=run.id, status=run.status, run_input=json.loads(run.search_input_json),
            funnel=json.loads(run.funnel_json), failure_summary=json.loads(run.failure_summary_json),
            started_at=run.started_at, completed_at=run.completed_at,
        )

    @staticmethod
    def _run_row_summary(row: DiscoveryRunJob) -> DiscoveryRunJobSummaryRead:
        return DiscoveryRunJobSummaryRead(
            discovered_job_id=row.discovered_job_id, evaluation_id=row.evaluation_id,
            outcome=row.outcome, failure_stage=row.failure_stage, failure_kind=row.failure_kind,
            opportunity=None,
        )


class UserJobDiscoveryService:
    """Persist run history and only reuse complete, still-current user evaluations."""

    def __init__(
        self,
        session: Session,
        *,
        ranking_service: JobRankingService | None = None,
        settings: Settings | None = None,
        runtime_snapshot: ResolvedRuntimeSnapshot | None = None,
        candidate_reader: CanonicalCandidateReadService | None = None,
    ) -> None:
        self._session = session
        self._ranking_service = ranking_service
        self._settings = settings or get_settings()
        self._runtime_snapshot = runtime_snapshot or resolve_runtime_snapshot(self._settings)
        self._candidate_reader = candidate_reader or CanonicalCandidateReadService(session)

    def start(
        self,
        user_id: str,
        request: DiscoveryRunCreateRequest,
        *,
        link_run: Callable[[str], None] | None = None,
    ) -> DiscoveryRunRead:
        if self._ranking_service is None:
            raise RuntimeError("Ranking service is required to create a discovery run.")
        try:
            snapshot = self._candidate_reader.read(user_id)
            context = self._candidate_reader.candidate_context(
                snapshot,
                require_structured_profile=True,
                require_complete_evidence=True,
            )
        except CandidateEvidenceMaterializationIncomplete as exc:
            raise ValueError("Current candidate evidence is not fully materialised.") from exc
        if context is None:
            raise ValueError("Candidate profile is not ready.")
        candidate_fingerprint = self.candidate_evaluation_fingerprint(context)
        contract_fingerprint = self.evaluation_contract_fingerprint()
        unique_ids = list(dict.fromkeys(request.discovered_job_ids))
        records = self._session.scalars(select(DiscoveredJob).where(DiscoveredJob.id.in_(unique_ids))).all()
        if len(records) != len(unique_ids):
            raise ValueError("One or more submitted jobs are unavailable.")
        run = DiscoveryRun(
            user_id=user_id,
            search_input_json=_canonical_json(self._run_input_snapshot(request)),
            search_input_fingerprint=self.search_input_fingerprint(request.query),
            candidate_evaluation_fingerprint=candidate_fingerprint,
            evaluation_contract_fingerprint=contract_fingerprint,
        )
        self._session.add(run)
        self._session.flush()
        if link_run is not None:
            # The one-off execution link and run row commit atomically before
            # any ranking/provider work can begin.
            link_run(run.id)
        self._session.commit()

        by_id = {record.id: record for record in records}
        # Missing IDs are intentionally not a public-job oracle; retain a safe run outcome.
        run_rows: dict[str, DiscoveryRunJob] = {}
        # Current eligibility is deliberately resolved before reuse. A historic
        # successful evaluation cannot make a currently incompatible role part
        # of a user run.
        actionable = [(job_id, by_id[job_id]) for job_id in unique_ids if self._is_actionable(by_id[job_id])]
        all_selection = JobPresemanticSelectionService().select_for_hunt(
            [self._listing(record) for _, record in actionable], query=request.query, limit=len(actionable)
        )
        eligible_keys = {self._listing_key(item) for item in all_selection.eligible}
        eligible: list[tuple[str, DiscoveredJob]] = []
        for job_id in unique_ids:
            record = by_id[job_id]
            if not self._is_actionable(record):
                run_rows[job_id] = self._add_relation(run, job_id, DiscoveryRunJobOutcome.NOT_ACTIONABLE)
            elif self._listing_key(self._listing(record)) not in eligible_keys:
                run_rows[job_id] = self._add_relation(run, job_id, DiscoveryRunJobOutcome.PRESEMANTIC_FILTERED)
            else:
                eligible.append((job_id, record))
        fresh: list[tuple[str, JobListing]] = []
        reused = 0
        for job_id, record in eligible:
            evaluation = self._reusable_evaluation(user_id, record, candidate_fingerprint, contract_fingerprint)
            if evaluation is not None:
                stored = RankedJobOpportunity.model_validate_json(evaluation.evaluation_json)
                if stored.relevance.relevant and stored.relevance.score >= request.min_relevance_score:
                    run_rows[job_id] = self._add_relation(run, job_id, DiscoveryRunJobOutcome.REUSED_EVALUATION, evaluation=evaluation)
                    reused += 1
                else:
                    run_rows[job_id] = self._add_relation(run, job_id, DiscoveryRunJobOutcome.SEMANTIC_REJECTED, evaluation=evaluation)
            else:
                fresh.append((job_id, self._listing(record)))
        self._session.commit()

        selection = JobPresemanticSelectionService().select_for_hunt(
            [listing for _, listing in fresh], query=request.query, limit=request.max_semantic_candidates
        )
        fresh_by_identity = {self._listing_key(listing): job_id for job_id, listing in fresh}
        selected_keys = {self._listing_key(item) for item in selection.selected}
        for job_id, listing in fresh:
            key = self._listing_key(listing)
            if key in selected_keys:
                # This provisional relation is atomically upgraded after the
                # bounded provider work; it makes run participation explicit.
                run_rows[job_id] = self._add_relation(run, job_id, DiscoveryRunJobOutcome.ANALYSIS_FAILED)
                continue
            outcome = (DiscoveryRunJobOutcome.OUTSIDE_SEMANTIC_BUDGET if key in eligible_keys else DiscoveryRunJobOutcome.PRESEMANTIC_FILTERED)
            run_rows[job_id] = self._add_relation(run, job_id, outcome)
        self._session.commit()

        response = JobRankingResponse(
            discovered_count=0, gated_out_count=0, relevance_screened_count=0, finalist_count=0, analysed_count=0
        )
        if selection.selected:
            try:
                response = self._ranking_service.rank(JobRankingRequest(
                    jobs=selection.selected, candidate_context=context,
                    max_semantic_candidates=len(selection.selected),
                    max_full_analyses=min(request.max_full_analyses, len(selection.selected)),
                    min_relevance_score=request.min_relevance_score,
                ))
            except Exception:
                self._terminalize_unexpected_failure(run, run_rows)
                self._session.commit()
                raise DiscoveryRunExecutionFailure(run.id, DiscoveryRunStatus(run.status)) from None
        self._persist_ranking(run, run_rows, fresh_by_identity, response, user_id, candidate_fingerprint, contract_fingerprint, request.min_relevance_score)
        self._finish_run(run, request, selection, response, reused)
        self._session.commit()
        return self.get_run(user_id, run.id)

    def list_runs(self, user_id: str) -> list[DiscoveryRunRead]:
        runs = self._session.scalars(select(DiscoveryRun).where(DiscoveryRun.user_id == user_id).order_by(DiscoveryRun.started_at.desc())).all()
        return [self._read_run(run) for run in runs]

    def get_run(self, user_id: str, run_id: str) -> DiscoveryRunRead:
        run = self._session.scalar(select(DiscoveryRun).where(DiscoveryRun.id == run_id, DiscoveryRun.user_id == user_id))
        if run is None:
            raise LookupError("Discovery run not found.")
        return self._read_run(run)

    def current_opportunities(self, user_id: str) -> UserOpportunityResponse:
        try:
            snapshot = self._candidate_reader.read(user_id)
            context = self._candidate_reader.candidate_context(
                snapshot,
                require_structured_profile=True,
                require_complete_evidence=True,
            )
        except CandidateEvidenceMaterializationIncomplete:
            return UserOpportunityResponse()
        if context is None:
            return UserOpportunityResponse()
        candidate = self.candidate_evaluation_fingerprint(context)
        contract = self.evaluation_contract_fingerprint()
        evaluations = self._session.execute(
            select(UserJobEvaluation, DiscoveredJob).join(DiscoveredJob, DiscoveredJob.id == UserJobEvaluation.discovered_job_id)
            .where(UserJobEvaluation.user_id == user_id, UserJobEvaluation.candidate_evaluation_fingerprint == candidate,
                   UserJobEvaluation.evaluation_contract_fingerprint == contract)
        ).all()
        opportunities: list[UserOpportunityRead] = []
        for evaluation, job in evaluations:
            if self._is_actionable(job) and evaluation.job_content_hash == job.content_hash:
                opportunities.append(UserOpportunityRead(evaluation_id=evaluation.id, discovered_job_id=job.id, opportunity=RankedJobOpportunity.model_validate_json(evaluation.evaluation_json)))
        priority = {Recommendation.APPLY: 0, Recommendation.CONSIDER: 1, Recommendation.SKIP: 2}
        opportunities.sort(key=lambda item: (priority[item.opportunity.recommendation_assessment.recommendation], -item.opportunity.fit_assessment.fit_score, -item.opportunity.career_assessment.career_alignment_score, -item.opportunity.relevance.score, item.discovered_job_id))
        return UserOpportunityResponse(opportunities=opportunities)

    def current_opportunity_summaries(self, user_id: str, *, limit: int) -> UserOpportunitySummaryResponse:
        items = self._current_opportunity_items_read_only(user_id)
        decision_rows = self._session.scalars(select(UserJobDecision).where(UserJobDecision.user_id == user_id, UserJobDecision.discovered_job_id.in_([item.discovered_job_id for item in items]))).all() if items else []
        decisions = {row.discovered_job_id: row for row in decision_rows}
        return UserOpportunitySummaryResponse(items=[self._summary(item, decisions.get(item.discovered_job_id)) for item in items[:limit]], limit=limit, truncated=len(items) > limit)

    def current_opportunity_detail(self, user_id: str, evaluation_id: str) -> RankedJobOpportunity:
        for item in self._current_opportunity_items_read_only(user_id, include_dismissed=True):
            if item.evaluation_id == evaluation_id:
                job = self._session.get(DiscoveredJob, item.discovered_job_id)
                # Current reads project current deterministic recency without
                # rewriting the historical evaluation snapshot.
                return item.opportunity.model_copy(update={"legitimacy": PostingLegitimacyService().assess(self._listing(job))})
        raise LookupError("Current opportunity not found.")

    def list_run_summaries(self, user_id: str, *, limit: int) -> DiscoveryRunSummaryResponse:
        runs = self._session.scalars(select(DiscoveryRun).where(DiscoveryRun.user_id == user_id).order_by(DiscoveryRun.started_at.desc(), DiscoveryRun.id.asc()).limit(limit + 1)).all()
        return DiscoveryRunSummaryResponse(items=[self._run_summary(run) for run in runs[:limit]], limit=limit, truncated=len(runs) > limit)

    def get_run_detail(self, user_id: str, run_id: str) -> DiscoveryRunDetailRead:
        run = self._owned_run(user_id, run_id)
        rows = self._session.scalars(select(DiscoveryRunJob).where(DiscoveryRunJob.discovery_run_id == run.id).order_by(DiscoveryRunJob.created_at, DiscoveryRunJob.id)).all()
        return DiscoveryRunDetailRead(**self._run_summary(run).model_dump(), jobs=[self._run_row_summary(row) for row in rows])

    def get_historical_run_job_detail(self, user_id: str, run_id: str, discovered_job_id: str) -> DiscoveryRunJobDetailRead:
        run = self._owned_run(user_id, run_id)
        row = self._session.scalar(select(DiscoveryRunJob).where(DiscoveryRunJob.discovery_run_id == run.id, DiscoveryRunJob.discovered_job_id == discovered_job_id))
        if row is None:
            raise LookupError("Discovery run job not found.")
        evaluation = self._session.get(UserJobEvaluation, row.evaluation_id) if row.evaluation_id else None
        return DiscoveryRunJobDetailRead(discovered_job_id=row.discovered_job_id, evaluation_id=row.evaluation_id, outcome=row.outcome, failure_stage=row.failure_stage, failure_kind=row.failure_kind, opportunity=RankedJobOpportunity.model_validate_json(evaluation.evaluation_json) if evaluation else None, runtime_attribution=read_attribution(evaluation.runtime_attribution_json) if evaluation else None)

    def current_evaluation_for_job(
        self, user_id: str, job: DiscoveredJob, *, candidate_context: CandidateContext | None = None
    ) -> RankedJobOpportunity | None:
        """The shared #154 authority for a reusable complete evaluation."""
        evaluation = reusable_current_evaluation(
            self._session, user_id, job, candidate_reader=self._candidate_reader,
            runtime_snapshot=self._runtime_snapshot, candidate_context=candidate_context,
        )
        if evaluation is None:
            return None
        result = RankedJobOpportunity.model_validate_json(evaluation.evaluation_json)
        return result if result.job_profile is not None and result.requirement_matches else None

    def is_currently_actionable(self, job: DiscoveredJob) -> bool:
        """Expose the shared public-job actionability rule without copying it."""
        return self._is_actionable(job)

    def _current_opportunity_items_read_only(self, user_id: str, *, include_dismissed: bool = False) -> list[UserOpportunityRead]:
        try:
            snapshot = self._candidate_reader.read(user_id)
            context = self._candidate_reader.candidate_context(
                snapshot,
                require_structured_profile=True,
                require_complete_evidence=True,
            )
        except CandidateEvidenceMaterializationIncomplete:
            return []
        if context is None:
            return []
        candidate, contract = self.candidate_evaluation_fingerprint(context), self.evaluation_contract_fingerprint()
        rows = self._session.execute(select(UserJobEvaluation, DiscoveredJob).join(DiscoveredJob, DiscoveredJob.id == UserJobEvaluation.discovered_job_id).where(UserJobEvaluation.user_id == user_id, UserJobEvaluation.candidate_evaluation_fingerprint == candidate, UserJobEvaluation.evaluation_contract_fingerprint == contract)).all()
        result = [UserOpportunityRead(evaluation_id=evaluation.id, discovered_job_id=job.id, opportunity=RankedJobOpportunity.model_validate_json(evaluation.evaluation_json)) for evaluation, job in rows if self._is_actionable(job) and evaluation.job_content_hash == job.content_hash]
        if not include_dismissed and result:
            dismissed = set(self._session.scalars(select(UserJobDecision.discovered_job_id).where(UserJobDecision.user_id == user_id, UserJobDecision.decision == UserJobDecisionValue.DISMISSED.value, UserJobDecision.discovered_job_id.in_([item.discovered_job_id for item in result]))).all())
            result = [item for item in result if item.discovered_job_id not in dismissed]
        priority = {Recommendation.APPLY: 0, Recommendation.CONSIDER: 1, Recommendation.SKIP: 2}
        result.sort(key=lambda item: (priority[item.opportunity.recommendation_assessment.recommendation], -item.opportunity.fit_assessment.fit_score, -item.opportunity.career_assessment.career_alignment_score, -item.opportunity.relevance.score, item.discovered_job_id))
        return result

    def _summary(self, item: UserOpportunityRead, decision_row: UserJobDecision | None = None) -> UserOpportunitySummary:
        job = self._session.get(DiscoveredJob, item.discovered_job_id)
        opportunity = item.opportunity
        row = decision_row
        decision = UserJobDecisionRead(discovered_job_id=item.discovered_job_id, decision=UserJobDecisionValue.UNDECIDED) if row is None else UserJobDecisionRead(discovered_job_id=row.discovered_job_id, decision=UserJobDecisionValue(row.decision), revision=row.revision, created_at=utc_timestamp(row.created_at), updated_at=utc_timestamp(row.updated_at))
        return UserOpportunitySummary(evaluation_id=item.evaluation_id, discovered_job_id=item.discovered_job_id, recommendation=opportunity.recommendation_assessment.recommendation, title=job.title, company=job.company, location=job.location, work_arrangement=job.work_arrangement, fit_score=opportunity.fit_assessment.fit_score, career_alignment_score=opportunity.career_assessment.career_alignment_score, career_alignment_confidence=opportunity.career_assessment.confidence, relevance_score=opportunity.relevance.score, archetype=opportunity.archetype.archetype, url=job.url, posting_recency=PostingLegitimacyService().assess(self._listing(job)), decision=decision)

    @staticmethod
    def _run_summary(run: DiscoveryRun) -> DiscoveryRunSummaryRead:
        return DiscoveryRunSummaryRead(id=run.id, status=run.status, run_input=json.loads(run.search_input_json), funnel=json.loads(run.funnel_json), failure_summary=json.loads(run.failure_summary_json), started_at=run.started_at, completed_at=run.completed_at)

    def _run_row_summary(self, row: DiscoveryRunJob) -> DiscoveryRunJobSummaryRead:
        # Run summaries are strictly historical outcome rows. Full historical
        # snapshots are available only through the exact run-job detail route.
        return DiscoveryRunJobSummaryRead(discovered_job_id=row.discovered_job_id, evaluation_id=row.evaluation_id, outcome=row.outcome, failure_stage=row.failure_stage, failure_kind=row.failure_kind, opportunity=None)

    def _owned_run(self, user_id: str, run_id: str) -> DiscoveryRun:
        run = self._session.scalar(select(DiscoveryRun).where(DiscoveryRun.id == run_id, DiscoveryRun.user_id == user_id))
        if run is None:
            raise LookupError("Discovery run not found.")
        return run

    @staticmethod
    def search_input_fingerprint(query) -> str:
        return _fingerprint(UserJobDiscoveryService._normalised_search_input(query))

    @staticmethod
    def _normalised_search_input(query) -> dict[str, object]:
        value = query.model_dump(mode="json")
        for key in (
            "keywords", "locations", "companies", "excluded_companies",
            "excluded_title_terms", "employment_types",
        ):
            value[key] = sorted({" ".join(item.split()).casefold() for item in value.get(key, []) if item.strip()})
        return value

    @staticmethod
    def _run_input_snapshot(request: DiscoveryRunCreateRequest) -> dict[str, object]:
        """Historical execution input, separate from query-only identity."""
        return {
            "query": UserJobDiscoveryService._normalised_search_input(request.query),
            "max_semantic_candidates": request.max_semantic_candidates,
            "max_full_analyses": request.max_full_analyses,
            "min_relevance_score": request.min_relevance_score,
        }

    @staticmethod
    def candidate_evaluation_fingerprint(context: CandidateContext) -> str:
        # This is a deterministic projection of the actual ranking-stage contracts;
        # it intentionally excludes storage timestamps, source names and provenance.
        matching = candidate_matching_profile_for_fingerprint(context)
        return _fingerprint({
            "search": candidate_search_profile(context).model_dump(mode="json"),
            "career": candidate_career_profile(context).model_dump(mode="json"),
            "matching": matching,
        })

    def evaluation_contract_fingerprint(self) -> str:
        return evaluation_contract_fingerprint_for_runtime(self._runtime_snapshot)

    def _reusable_evaluation(self, user_id: str, job: DiscoveredJob, candidate: str, contract: str) -> UserJobEvaluation | None:
        return self._session.scalar(select(UserJobEvaluation).where(
            UserJobEvaluation.user_id == user_id, UserJobEvaluation.discovered_job_id == job.id,
            UserJobEvaluation.job_content_hash == job.content_hash,
            UserJobEvaluation.candidate_evaluation_fingerprint == candidate,
            UserJobEvaluation.evaluation_contract_fingerprint == contract,
        ))

    def _persist_ranking(self, run, rows, job_ids, response, user_id, candidate, contract, min_relevance_score) -> None:
        result_keys: set[str] = set()
        for opportunity in response.results:
            key = self._listing_key(opportunity.job); result_keys.add(key)
            job_id = job_ids[key]; job = self._session.get(DiscoveredJob, job_id)
            evaluation = UserJobEvaluation(user_id=user_id, discovered_job_id=job_id, job_content_hash=job.content_hash,
                candidate_evaluation_fingerprint=candidate, evaluation_contract_fingerprint=contract,
                job_snapshot_json=_canonical_json(self._job_snapshot(job)), evaluation_json=opportunity.model_dump_json(),
                runtime_attribution_json=canonical_attribution_json(available_attribution(self._runtime_snapshot, JOB_EVALUATION_OPERATIONS)))
            try:
                with self._session.begin_nested():
                    self._session.add(evaluation); self._session.flush()
            except IntegrityError:
                evaluation = self._reusable_evaluation(user_id, job, candidate, contract)
            rows[job_id].evaluation_id = evaluation.id
            rows[job_id].outcome = DiscoveryRunJobOutcome.NEWLY_EVALUATED.value
        failed_keys = set()
        for job in response.gated_out_jobs:
            key = self._listing_key(job)
            if key in job_ids:
                rows[job_ids[key]].outcome = DiscoveryRunJobOutcome.PRESEMANTIC_FILTERED.value
        for failure in response.failures:
            key = self._listing_key(failure.job); failed_keys.add(key)
            if key in job_ids:
                row = rows[job_ids[key]]
                row.outcome = DiscoveryRunJobOutcome.ANALYSIS_FAILED.value
                row.failure_stage = failure.stage; row.failure_kind = _safe_failure_kind(failure.error)
        for diagnostic in response.semantic_screening:
            key = self._listing_key(diagnostic.job)
            if key not in job_ids or key in result_keys or key in failed_keys:
                continue
            row = rows[job_ids[key]]
            if diagnostic.relevance is not None and (not diagnostic.relevance.relevant or diagnostic.relevance.score < min_relevance_score):
                row.outcome = DiscoveryRunJobOutcome.SEMANTIC_REJECTED.value
            elif diagnostic.archetype is not None:
                row.outcome = DiscoveryRunJobOutcome.OUTSIDE_DEEP_ANALYSIS_BUDGET.value
            elif diagnostic.failure_stage:
                row.outcome = DiscoveryRunJobOutcome.ANALYSIS_FAILED.value; row.failure_stage = diagnostic.failure_stage; row.failure_kind = _safe_failure_kind(diagnostic.error)

    def _finish_run(self, run, request, selection, response, reused) -> None:
        relations = self._session.scalars(select(DiscoveryRunJob).where(DiscoveryRunJob.discovery_run_id == run.id)).all()
        failures = Counter(row.failure_stage for row in relations if row.failure_stage)
        # A terminal analysis_failed is itself failure even when a provider
        # forgot to supply a stage; it must never leave a completed run.
        if any(row.outcome == DiscoveryRunJobOutcome.ANALYSIS_FAILED.value for row in relations):
            failures["analysis_failed"] += 1
        successful = sum(row.outcome in {DiscoveryRunJobOutcome.NEWLY_EVALUATED.value, DiscoveryRunJobOutcome.REUSED_EVALUATION.value} for row in relations)
        run.status = (DiscoveryRunStatus.PARTIAL_FAILED.value if failures and successful else DiscoveryRunStatus.FAILED.value if failures else DiscoveryRunStatus.COMPLETED.value)
        run.funnel_json = _canonical_json({"submitted": len(request.discovered_job_ids), "fresh_selected": len(selection.selected), "reused": reused, "relevance_screened": response.relevance_screened_count, "full_analysis_attempts": response.finalist_count, "analysed": response.analysed_count})
        run.failure_summary_json = _canonical_json(dict(failures)); run.completed_at = datetime.now(timezone.utc)

    def _read_run(self, run: DiscoveryRun) -> DiscoveryRunRead:
        rows = self._session.scalars(select(DiscoveryRunJob).where(DiscoveryRunJob.discovery_run_id == run.id).order_by(DiscoveryRunJob.created_at, DiscoveryRunJob.id)).all()
        return DiscoveryRunRead(id=run.id, status=DiscoveryRunStatus(run.status), search_input_fingerprint=run.search_input_fingerprint,
            candidate_evaluation_fingerprint=run.candidate_evaluation_fingerprint, evaluation_contract_fingerprint=run.evaluation_contract_fingerprint, run_input=json.loads(run.search_input_json),
            funnel=json.loads(run.funnel_json), failure_summary=json.loads(run.failure_summary_json), started_at=run.started_at, completed_at=run.completed_at,
            jobs=[DiscoveryRunJobRead(discovered_job_id=row.discovered_job_id, evaluation_id=row.evaluation_id, outcome=row.outcome, failure_stage=row.failure_stage, failure_kind=row.failure_kind, opportunity=RankedJobOpportunity.model_validate_json(self._session.get(UserJobEvaluation, row.evaluation_id).evaluation_json) if row.evaluation_id else None) for row in rows])

    def _terminalize_unexpected_failure(self, run: DiscoveryRun, rows: dict[str, DiscoveryRunJob]) -> None:
        for row in rows.values():
            if row.outcome == DiscoveryRunJobOutcome.ANALYSIS_FAILED.value:
                row.failure_stage = "orchestration"
                row.failure_kind = "ranking_failed"
        successful = any(
            row.outcome in {DiscoveryRunJobOutcome.NEWLY_EVALUATED.value, DiscoveryRunJobOutcome.REUSED_EVALUATION.value}
            for row in rows.values()
        )
        run.status = (DiscoveryRunStatus.PARTIAL_FAILED.value if successful else DiscoveryRunStatus.FAILED.value)
        run.failure_summary_json = _canonical_json({"orchestration": 1})
        run.completed_at = datetime.now(timezone.utc)

    def _add_relation(self, run, job_id, outcome, evaluation=None):
        row = DiscoveryRunJob(discovery_run_id=run.id, discovered_job_id=job_id, evaluation_id=evaluation.id if evaluation else None, outcome=outcome.value if hasattr(outcome, "value") else outcome)
        self._session.add(row); return row

    @staticmethod
    def _is_actionable(job: DiscoveredJob) -> bool:
        return is_public_job_actionable(job)

    @staticmethod
    def _listing(job: DiscoveredJob) -> JobListing:
        return JobListing(source=job.source, source_token=job.source_token, external_id=job.external_id, title=job.title, company=job.company, location=job.location, url=job.url, description=job.description, posted_at=job.posted_at, work_arrangement=job.work_arrangement, employment_type=job.employment_type, detail_authority=job.detail_authority, verification_status=job.verification_status, verification_reason=job.verification_reason)

    @staticmethod
    def _listing_key(job: JobListing) -> str:
        return "|".join((job.source, job.source_token or "", job.external_id or "", job.url))

    @staticmethod
    def _job_snapshot(job: DiscoveredJob) -> dict[str, object]:
        return {"id": job.id, "title": job.title, "company": job.company, "location": job.location, "url": job.url, "content_hash": job.content_hash}


def candidate_matching_profile_for_fingerprint(context: CandidateContext) -> dict[str, object]:
    """Unbounded active evidence identity; provider compaction must not hide a material change."""
    return {"profile_summary": candidate_matching_profile(context, job_profile=_empty_profile()).profile_summary,
            "skills": sorted(candidate_matching_profile(context, job_profile=_empty_profile()).skills, key=str.casefold),
            "evidence": sorted(({"evidence_id": item.evidence_id, "evidence_type": item.evidence_type, "title": item.title, "text": item.text, "skills": item.skills} for item in context.evidence), key=lambda value: value["evidence_id"])}


def _empty_profile():
    from app.schemas.job import JobProfile
    return JobProfile(title="fingerprint", requirements=[])


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_failure_kind(value: str | None) -> str | None:
    if not value:
        return None
    # Existing ranking failures are already safe text; persist only the bounded category.
    return value.split(":", 1)[0].strip().casefold().replace(" ", "_")[:128]


_CACHED_APPLICATION_REVISION: str | None = None


def _application_revision() -> str:
    """Prefer deployment identity, otherwise resolve local Git once per process."""
    global _CACHED_APPLICATION_REVISION
    explicit = os.environ.get("CAREER_TRANS_DEPLOYMENT_REVISION", "").strip()
    if explicit:
        return explicit
    if _CACHED_APPLICATION_REVISION is None:
        try:
            _CACHED_APPLICATION_REVISION = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=os.path.dirname(__file__) + "/../../..", text=True, stderr=subprocess.DEVNULL, timeout=2
            ).strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            _CACHED_APPLICATION_REVISION = "unknown"
    return _CACHED_APPLICATION_REVISION

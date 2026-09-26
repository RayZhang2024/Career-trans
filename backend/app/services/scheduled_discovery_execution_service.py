"""Durable claim-then-execute orchestration for saved discovery schedules."""

import json
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.discovered_job import DiscoveredJob
from app.models.discovery_schedule import DiscoverySchedule, ScheduledDiscoveryExecution
from app.schemas.agentic_discovery import AgenticDiscoveryRequest
from app.schemas.discovery import JobSearchQuery
from app.schemas.discovery_schedule import AcquisitionConfig, EvaluationConfig, ExecutionStatus, TriggerKind
from app.schemas.structured_ats_discovery import StructuredAtsDiscoveryRequest
from app.schemas.user_job_discovery import DiscoveryRunCreateRequest
from app.services.canonical_candidate_read_service import (
    CandidateEvidenceMaterializationIncomplete,
    CanonicalCandidateReadService,
)
from app.services.discovery_schedule_service import DiscoveryScheduleService, most_recent_due, next_occurrence
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.llm_runtime import ResolvedRuntimeSnapshot

STALE_EXECUTION_AGE = timedelta(hours=2)


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


@dataclass(frozen=True)
class _ChannelOutcome:
    """Safe structural outcome; it deliberately contains no provider body/text."""
    succeeded: bool
    failed: bool
    canonical_ids: set[str] = field(default_factory=set)
    counters: dict[str, int] = field(default_factory=dict)


class ScheduledDiscoveryExecutionService:
    """Claims commit before acquisition, so provider work is never in a DB transaction."""

    def __init__(
        self,
        session: Session,
        *,
        structured_ats: object,
        agentic_web_factory: Callable[..., object],
        user_runs: object | None = None,
        user_runs_factory: Callable[[ResolvedRuntimeSnapshot], object] | None = None,
        runtime_snapshot_resolver: Callable[[str], ResolvedRuntimeSnapshot] | None = None,
        candidate_reader: CanonicalCandidateReadService | None = None,
    ) -> None:
        self._session = session
        self._structured_ats = structured_ats
        self._agentic_web_factory = agentic_web_factory
        self._user_runs = user_runs
        self._user_runs_factory = user_runs_factory
        self._runtime_snapshot_resolver = runtime_snapshot_resolver
        self._candidate_reader = candidate_reader or CanonicalCandidateReadService(session)

    def process_due(self, now: datetime, limit: int) -> list[ScheduledDiscoveryExecution]:
        schedules = self._session.scalars(
            select(DiscoverySchedule)
            .where(DiscoverySchedule.enabled.is_(True), DiscoverySchedule.next_run_at <= now)
            .order_by(DiscoverySchedule.next_run_at, DiscoverySchedule.id)
            .limit(limit)
        ).all()
        results: list[ScheduledDiscoveryExecution] = []
        for schedule in schedules:
            claimed = self.claim(schedule.id, TriggerKind.SCHEDULED, now)
            if claimed is not None:
                results.append(self._execute_safely(claimed.id, now))
        return results

    def run_now(self, user_id: str, schedule_id: str, now: datetime) -> ScheduledDiscoveryExecution:
        DiscoveryScheduleService(self._session).get(user_id, schedule_id)
        claimed = self.claim(schedule_id, TriggerKind.MANUAL, now)
        if claimed is None:
            raise RuntimeError("A discovery schedule execution is already running.")
        return self._execute_safely(claimed.id, now)

    def _execute_safely(self, execution_id: str, now: datetime) -> ScheduledDiscoveryExecution:
        """Both triggers terminalize catchable orchestration failures after claim."""
        try:
            return self.execute_claimed(execution_id, now)
        except Exception:
            # The claim was committed already; clear any failed unit of work before
            # terminalizing that durable execution record.
            self._session.rollback()
            return self._finish_by_id(execution_id, ExecutionStatus.FAILED, now, {}, {"execution": 1})

    def claim(self, schedule_id: str, trigger: TriggerKind, now: datetime) -> ScheduledDiscoveryExecution | None:
        """Atomically create an execution, acquire lease, and consume a scheduled slot."""
        schedule = self._session.get(DiscoverySchedule, schedule_id)
        if schedule is None:
            return None
        self._recover_stale(schedule, now)
        self._session.refresh(schedule)
        if schedule.active_execution_id:
            return None
        if trigger is TriggerKind.SCHEDULED and (not schedule.enabled or schedule.next_run_at is None or _utc(schedule.next_run_at) > _utc(now)):
            return None
        spec = DiscoveryScheduleService.spec(schedule)
        scheduled_for = most_recent_due(spec, now) if trigger is TriggerKind.SCHEDULED else None
        if trigger is TriggerKind.SCHEDULED and scheduled_for is None:
            return None
        execution = ScheduledDiscoveryExecution(
            schedule_id=schedule.id,
            user_id=schedule.user_id,
            trigger_kind=trigger.value,
            scheduled_for=scheduled_for,
            config_snapshot_json=json.dumps(DiscoveryScheduleService.snapshot(schedule), sort_keys=True),
        )
        try:
            self._session.add(execution)
            self._session.flush()
            values: dict[str, object] = {"active_execution_id": execution.id}
            if trigger is TriggerKind.SCHEDULED:
                values["next_run_at"] = next_occurrence(spec, now)
            claim = update(DiscoverySchedule).where(
                DiscoverySchedule.id == schedule.id,
                DiscoverySchedule.active_execution_id.is_(None),
            )
            if trigger is TriggerKind.SCHEDULED:
                claim = claim.where(DiscoverySchedule.enabled.is_(True), DiscoverySchedule.next_run_at <= _utc(now).replace(tzinfo=None))
            if self._session.execute(claim.values(**values).execution_options(synchronize_session=False)).rowcount != 1:
                self._session.rollback()
                return None
            self._session.commit()
            self._session.refresh(schedule)
            return execution
        except IntegrityError:
            self._session.rollback()
            return None

    def execute_claimed(self, execution_id: str, now: datetime) -> ScheduledDiscoveryExecution:
        execution = self._session.get(ScheduledDiscoveryExecution, execution_id)
        if execution is None or execution.status != ExecutionStatus.RUNNING.value:
            raise LookupError("Claimed schedule execution is unavailable.")
        snapshot = json.loads(execution.config_snapshot_json)
        try:
            candidate_snapshot = self._candidate_reader.read(execution.user_id)
            context = self._candidate_reader.candidate_context(
                candidate_snapshot,
                require_structured_profile=True,
                require_complete_evidence=True,
            )
        except CandidateEvidenceMaterializationIncomplete:
            return self._finish(execution, ExecutionStatus.SKIPPED, now, {}, {"candidate_evidence_incomplete": 1})
        if context is None:
            return self._finish(execution, ExecutionStatus.SKIPPED, now, {}, {"candidate_not_ready": 1})

        query = JobSearchQuery.model_validate(snapshot["query"])
        acquisition = AcquisitionConfig.model_validate(snapshot["acquisition"])
        outcomes: list[_ChannelOutcome] = []
        # A schedule owner snapshot is resolved before its first semantic stage,
        # then shared with both agentic acquisition and user evaluation.
        runtime_snapshot = (
            self._resolve_runtime(execution.user_id)
            if acquisition.agentic_web.enabled and self._runtime_snapshot_resolver is not None
            else None
        )

        if acquisition.structured_ats.enabled:
            try:
                config = acquisition.structured_ats
                response = self._structured_ats.discover(
                    StructuredAtsDiscoveryRequest(
                        keywords=query.keywords,
                        locations=query.locations,
                        remote_ok=query.remote_ok,
                        excluded_companies=query.excluded_companies,
                        excluded_title_terms=query.excluded_title_terms,
                        employment_types=query.employment_types,
                        companies=config.companies,
                        providers=config.providers,
                        max_sources=config.max_sources,
                        max_results=config.max_results,
                    )
                )
                ids = self._canonical_ids(response.listings)
                attempted = len(response.source_diagnostics)
                succeeded = sum(diagnostic.succeeded for diagnostic in response.source_diagnostics)
                failed = sum(not diagnostic.succeeded for diagnostic in response.source_diagnostics)
                outcomes.append(_ChannelOutcome(
                    succeeded=(attempted == 0 or succeeded > 0),
                    failed=failed > 0,
                    canonical_ids=ids,
                    counters={"sources_attempted": attempted, "sources_succeeded": succeeded, "sources_failed": failed},
                ))
            except Exception:
                outcomes.append(_ChannelOutcome(succeeded=False, failed=True, counters={"sources_attempted": 0, "sources_succeeded": 0, "sources_failed": 1}))

        if acquisition.agentic_web.enabled:
            try:
                config = acquisition.agentic_web
                response = self._agentic_service(runtime_snapshot).discover(
                    AgenticDiscoveryRequest(
                        candidate_context=context,
                        query=query,
                        country=config.country,
                        max_search_queries=config.max_search_queries,
                        max_search_results_per_query=config.max_search_results_per_query,
                        max_pages_to_open=config.max_pages_to_open,
                        max_discovered_jobs=config.max_discovered_jobs,
                    )
                )
                diagnostics = response.diagnostics
                errors = bool(
                    diagnostics.search_errors
                    or diagnostics.page_errors
                    or diagnostics.page_fetch_failures
                    or diagnostics.extraction_failures
                )
                outcomes.append(_ChannelOutcome(
                    succeeded=bool(response.listings) or not errors,
                    failed=errors,
                    canonical_ids=self._canonical_ids(response.listings),
                    counters={
                        "search_queries_executed": diagnostics.search_queries_executed,
                        "pages_opened": diagnostics.pages_opened,
                        "page_fetch_failures": diagnostics.page_fetch_failures,
                        "extraction_successes": diagnostics.extraction_successes,
                        "extraction_failures": diagnostics.extraction_failures,
                    },
                ))
            except Exception:
                outcomes.append(_ChannelOutcome(succeeded=False, failed=True, counters={"search_queries_executed": 0, "pages_opened": 0, "page_fetch_failures": 0, "extraction_successes": 0, "extraction_failures": 1}))

        canonical_ids = set().union(*(outcome.canonical_ids for outcome in outcomes)) if outcomes else set()
        failures = {}
        summaries: dict[str, int] = {}
        for name, outcome in zip([name for name, enabled in (("structured_ats", acquisition.structured_ats.enabled), ("agentic_web", acquisition.agentic_web.enabled)) if enabled], outcomes, strict=True):
            if outcome.failed:
                failures[name] = 1
            for key, value in outcome.counters.items():
                summaries[f"{name}_{key}"] = value
        useful_channel = any(outcome.succeeded for outcome in outcomes)

        if not canonical_ids:
            status = ExecutionStatus.FAILED if failures and not useful_channel else (ExecutionStatus.PARTIAL_FAILED if failures else ExecutionStatus.COMPLETED)
            summaries["canonical_jobs"] = 0
            return self._finish(execution, status, now, summaries, failures)

        evaluation = EvaluationConfig.model_validate(snapshot["evaluation"])
        try:
            if runtime_snapshot is None and self._runtime_snapshot_resolver is not None:
                runtime_snapshot = self._resolve_runtime(execution.user_id)
            user_runs = self._user_run_service(runtime_snapshot)
            run = user_runs.start(
                execution.user_id,
                DiscoveryRunCreateRequest(
                    query=query,
                    discovered_job_ids=sorted(canonical_ids),
                    max_semantic_candidates=evaluation.max_semantic_candidates,
                    max_full_analyses=evaluation.max_full_analyses,
                    min_relevance_score=evaluation.min_relevance_score,
                ),
            )
            execution.discovery_run_id = run.id
            if run.status.value == "failed":
                status = ExecutionStatus.FAILED
            elif failures or run.status.value == "partial_failed":
                status = ExecutionStatus.PARTIAL_FAILED
            else:
                status = ExecutionStatus.COMPLETED
            summaries.update({"canonical_jobs": len(canonical_ids), "reused": run.funnel.get("reused", 0), "relevance_screened": run.funnel.get("relevance_screened", 0), "analysed": run.funnel.get("analysed", 0)})
            return self._finish(execution, status, now, summaries, failures)
        except Exception:
            return self._finish(execution, ExecutionStatus.FAILED, now, {"canonical_jobs": len(canonical_ids)}, {"evaluation": 1})

    def _resolve_runtime(self, user_id: str) -> ResolvedRuntimeSnapshot:
        assert self._runtime_snapshot_resolver is not None
        return self._runtime_snapshot_resolver(user_id)

    def _agentic_service(self, runtime_snapshot: ResolvedRuntimeSnapshot | None) -> object:
        parameters = inspect.signature(self._agentic_web_factory).parameters
        return self._agentic_web_factory(runtime_snapshot) if parameters else self._agentic_web_factory()

    def _user_run_service(self, runtime_snapshot: ResolvedRuntimeSnapshot | None) -> object:
        if runtime_snapshot is not None and self._user_runs_factory is not None:
            return self._user_runs_factory(runtime_snapshot)
        if self._user_runs is None:
            raise RuntimeError("A user discovery service factory is required for evaluation.")
        return self._user_runs

    def _recover_stale(self, schedule: DiscoverySchedule, now: datetime) -> None:
        if not schedule.active_execution_id:
            return
        active = self._session.get(ScheduledDiscoveryExecution, schedule.active_execution_id)
        if active and active.status == ExecutionStatus.RUNNING.value and _utc(active.started_at) < _utc(now) - STALE_EXECUTION_AGE:
            active.status = ExecutionStatus.FAILED.value
            active.completed_at = now
            active.failure_summary_json = json.dumps({"stale_execution": 1})
            schedule.active_execution_id = None
            self._session.commit()

    def _finish_by_id(self, execution_id: str, status: ExecutionStatus, now: datetime, summary: dict[str, int], failures: dict[str, int]) -> ScheduledDiscoveryExecution:
        execution = self._session.get(ScheduledDiscoveryExecution, execution_id)
        if execution is None:
            raise LookupError("Schedule execution is unavailable.")
        return self._finish(execution, status, now, summary, failures)

    def _finish(self, execution: ScheduledDiscoveryExecution, status: ExecutionStatus, now: datetime, summary: dict[str, int], failures: dict[str, int]) -> ScheduledDiscoveryExecution:
        execution.status = status.value
        execution.completed_at = now
        execution.acquisition_summary_json = json.dumps(summary, sort_keys=True)
        execution.failure_summary_json = json.dumps(failures, sort_keys=True)
        schedule = self._session.get(DiscoverySchedule, execution.schedule_id)
        # Never update schedule timing/config here. A later edit or claim wins.
        if schedule is not None and schedule.active_execution_id == execution.id:
            schedule.active_execution_id = None
            schedule.last_execution_at = now
        self._session.commit()
        return execution

    def _canonical_ids(self, listings: list[object]) -> set[str]:
        keys = [SqlAlchemyDiscoveredJobStateStore.identity_key(listing) for listing in listings]
        if not keys:
            return set()
        return set(self._session.scalars(select(DiscoveredJob.id).where(DiscoveredJob.identity_key.in_(keys))).all())

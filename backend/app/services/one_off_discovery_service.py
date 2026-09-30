"""Durable one-off discovery claims and reconciliation."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.one_off_discovery_execution import OneOffDiscoveryExecution
from app.models.user_job_discovery_settings import UserJobDiscoverySettings
from app.schemas.discovery import JobSearchQuery
from app.schemas.one_off_discovery import (
    OneOffExecutionRead, OneOffLaunchRequest, OneOffPolicy, OneOffPreflightRead,
    OneOffReadiness, OneOffStatus,
)
from app.services.canonical_candidate_read_service import (
    CandidateEvidenceMaterializationIncomplete, CanonicalCandidateReadService,
)
from app.services.agentic_web_execution_core import AgenticWebExecutionCore
from app.services.job_discovery_settings_service import (
    JobDiscoveryProviderNotReady, JobDiscoverySettingsService, ResolvedWebSearchProvider,
)


ONE_OFF_STALE_AGE = timedelta(hours=2)


class OneOffLaunchConflict(RuntimeError):
    """The launch contract or idempotency identity changed."""


class OneOffLaunchUnavailable(RuntimeError):
    """A pre-claim launch condition is not met."""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class OneOffDiscoveryService:
    """Manual web-only flow; all external work starts after the claim commits."""

    def __init__(
        self,
        session: Session,
        *,
        settings_service: JobDiscoverySettingsService,
        runtime_snapshot_resolver,
        agentic_factory,
        user_runs_factory,
        candidate_reader: CanonicalCandidateReadService | None = None,
        clock=lambda: datetime.now(timezone.utc),
        agentic_core: AgenticWebExecutionCore | None = None,
    ) -> None:
        self._session = session
        self._settings = settings_service
        self._runtime_snapshot_resolver = runtime_snapshot_resolver
        self._agentic_factory = agentic_factory
        self._user_runs_factory = user_runs_factory
        self._candidate_reader = candidate_reader or CanonicalCandidateReadService(session)
        self._clock = clock
        self._agentic_core = agentic_core or AgenticWebExecutionCore(session)

    def preflight(self, user_id: str) -> OneOffPreflightRead:
        policy = OneOffPolicy()
        settings = self._settings.read(user_id)
        provider = settings.effective_provider.value
        readiness = OneOffReadiness.CONFIGURED_FOR_LAUNCH
        available = True
        reason = None
        try:
            # Resolves credentials and probes Local Codex manual readiness only; no search is performed.
            self._settings.resolve_provider(user_id, scheduled_due_runner=False)
        except JobDiscoveryProviderNotReady as exc:
            available = False
            reason = str(exc)
            if provider == "local_codex":
                readiness = OneOffReadiness.MANUAL_RUNTIME_NOT_READY
            elif provider == "unsupported":
                readiness = OneOffReadiness.UNSUPPORTED
            else:
                readiness = OneOffReadiness.NOT_CONFIGURED
        try:
            snapshot = self._candidate_reader.read(user_id)
            context = self._candidate_reader.candidate_context(
                snapshot, require_structured_profile=True, require_complete_evidence=True,
            )
            if context is None:
                available = False
                readiness = OneOffReadiness.CANDIDATE_NOT_READY
                reason = "Confirm your candidate profile and complete required evidence before finding jobs."
        except CandidateEvidenceMaterializationIncomplete:
            available = False
            readiness = OneOffReadiness.CANDIDATE_NOT_READY
            reason = "Candidate evidence is incomplete. Refresh your profile evidence before finding jobs."
        fingerprint = _digest({
            "provider_settings_revision": settings.revision,
            "effective_provider": provider,
            "policy": policy.model_dump(mode="json"),
        })
        return OneOffPreflightRead(
            effective_provider=provider, readiness=readiness, available=available,
            reason=reason, policy=policy, provider_settings_revision=settings.revision,
            launch_fingerprint=fingerprint,
        )

    def launch(self, user_id: str, request: OneOffLaunchRequest) -> OneOffExecutionRead:
        query = request.query
        for field in ("keywords", "locations", "excluded_companies", "excluded_title_terms", "employment_types"):
            values = getattr(query, field)
            canonical = [line.strip() for value in values for line in re.split(r"\r?\n", value) if line.strip()]
            if values != canonical:
                raise OneOffLaunchUnavailable("SearchIntent must be committed before finding jobs. Review its editable fields and try again.")
        if not any(value.strip() for value in query.keywords):
            raise OneOffLaunchUnavailable("Add at least one search theme before finding jobs.")
        identity = {"query": query.model_dump(mode="json")}
        request_fingerprint = _digest(identity)
        existing = self._by_request(user_id, request.client_request_id)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise OneOffLaunchConflict("This request ID is already associated with a different SearchIntent.")
            self._recover_if_stale(existing)
            return self.read(existing)

        preflight = self.preflight(user_id)
        if request.expected_launch_fingerprint != preflight.launch_fingerprint:
            raise OneOffLaunchConflict("Job Discovery settings or the one-off policy changed. Refresh preflight and explicitly launch again.")
        if not preflight.available:
            raise OneOffLaunchUnavailable(preflight.reason or "One-off discovery is not currently available.")
        try:
            candidate_snapshot = self._candidate_reader.read(user_id)
            context = self._candidate_reader.candidate_context(
                candidate_snapshot, require_structured_profile=True, require_complete_evidence=True,
            )
        except CandidateEvidenceMaterializationIncomplete as exc:
            raise OneOffLaunchUnavailable("Candidate evidence is incomplete. Refresh your profile evidence before finding jobs.") from exc
        if context is None:
            raise OneOffLaunchUnavailable("Confirm your candidate profile before finding jobs.")

        # Hold the user's provider-authority revision stable until the claim commits.
        self._session.execute(select(UserJobDiscoverySettings).where(
            UserJobDiscoverySettings.user_id == user_id,
        ).with_for_update()).first()
        try:
            provider_resolution = self._settings.resolve_provider(user_id, scheduled_due_runner=False)
        except JobDiscoveryProviderNotReady as exc:
            raise OneOffLaunchUnavailable(str(exc)) from exc
        current_settings = self._settings.read(user_id)
        current_fingerprint = _digest({
            "provider_settings_revision": current_settings.revision,
            "effective_provider": current_settings.effective_provider.value,
            "policy": preflight.policy.model_dump(mode="json"),
        })
        if current_fingerprint != request.expected_launch_fingerprint or current_settings.effective_provider.value != provider_resolution.metadata.get("provider"):
            raise OneOffLaunchConflict("Job Discovery settings changed. Refresh preflight and explicitly launch again.")
        safe_provider = self._safe_provider(provider_resolution.metadata)
        execution = OneOffDiscoveryExecution(
            user_id=user_id, client_request_id=str(request.client_request_id),
            request_fingerprint=request_fingerprint,
            query_snapshot_json=_json(query.model_dump(mode="json")),
            policy_snapshot_json=_json(preflight.policy.model_dump(mode="json")),
            provider_metadata_json=_json(safe_provider), status=OneOffStatus.RUNNING.value,
        )
        self._session.add(execution)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            existing = self._by_request(user_id, request.client_request_id)
            if existing is None:
                raise
            if existing.request_fingerprint != request_fingerprint:
                raise OneOffLaunchConflict("This request ID is already associated with a different SearchIntent.")
            self._recover_if_stale(existing)
            return self.read(existing)
        self._session.refresh(execution)

        try:
            now = self._clock()
            runtime = self._runtime_snapshot_resolver(user_id)
            discovery = self._agentic_factory(user_id, runtime, provider_resolution)
            acquired = self._agentic_core.acquire(
                agentic_service=discovery,
                provider_metadata=getattr(discovery, "provider_metadata", None),
                candidate_context=context,
                query=query,
                country=preflight.policy.country,
                max_search_queries=preflight.policy.max_search_queries,
                max_search_results_per_query=preflight.policy.max_search_results_per_query,
                max_pages_to_open=preflight.policy.max_pages_to_open,
                max_discovered_jobs=preflight.policy.max_discovered_jobs,
            )
            ids = acquired.canonical_ids
            summary = {
                **acquired.counters,
                "canonical_jobs": len(ids),
            }
            failures = {"agentic_web": 1} if acquired.failed else {}
            if ids and not acquired.stop_evaluation:
                evaluated = self._agentic_core.evaluate(
                    user_runs=self._user_runs_factory(runtime),
                    user_id=user_id,
                    query=query,
                    canonical_ids=ids,
                    max_semantic_candidates=preflight.policy.max_semantic_candidates,
                    max_full_analyses=preflight.policy.max_full_analyses,
                    min_relevance_score=preflight.policy.min_relevance_score,
                )
                execution.discovery_run_id = evaluated.discovery_run_id
                summary.update(evaluated.funnel)
                if evaluated.status == "failed":
                    failures["evaluation"] = 1
                status = OneOffStatus(self._agentic_core.final_status(
                    acquisition_failed=acquired.failed,
                    evaluation_status=evaluated.status,
                    useful_acquisition=True,
                ))
            else:
                # A clean search with no accepted vacancies is a completed execution.
                status = OneOffStatus(self._agentic_core.final_status(
                    acquisition_failed=acquired.failed,
                    evaluation_status=None,
                    useful_acquisition=bool(ids),
                ))
            return self._finish(execution, status, summary, failures, now)
        except Exception:
            self._session.rollback()
            row = self._session.get(OneOffDiscoveryExecution, execution.id)
            if row is None:
                raise
            return self._finish(row, OneOffStatus.FAILED, {}, {"execution": 1}, self._clock())

    def reconcile(self, user_id: str, execution_id: str) -> OneOffExecutionRead:
        row = self._session.scalar(select(OneOffDiscoveryExecution).where(
            OneOffDiscoveryExecution.id == execution_id, OneOffDiscoveryExecution.user_id == user_id,
        ))
        if row is None:
            raise LookupError("One-off discovery execution not found.")
        self._recover_if_stale(row)
        return self.read(row)

    def list_history(self, user_id: str, limit: int = 20) -> list[OneOffExecutionRead]:
        rows = self._session.scalars(select(OneOffDiscoveryExecution).where(
            OneOffDiscoveryExecution.user_id == user_id,
        ).order_by(OneOffDiscoveryExecution.started_at.desc(), OneOffDiscoveryExecution.id.desc()).limit(limit)).all()
        for row in rows:
            self._recover_if_stale(row)
        return [self.read(row) for row in rows]

    def _by_request(self, user_id: str, request_id: UUID) -> OneOffDiscoveryExecution | None:
        return self._session.scalar(select(OneOffDiscoveryExecution).where(
            OneOffDiscoveryExecution.user_id == user_id,
            OneOffDiscoveryExecution.client_request_id == str(request_id),
        ))

    def _recover_if_stale(self, row: OneOffDiscoveryExecution) -> None:
        if row.status != OneOffStatus.RUNNING.value:
            return
        started = row.started_at if row.started_at.tzinfo else row.started_at.replace(tzinfo=timezone.utc)
        if started < self._clock() - ONE_OFF_STALE_AGE:
            row.status = OneOffStatus.FAILED.value
            row.completed_at = self._clock()
            row.failure_summary_json = _json({"stale_execution": 1})
            self._session.commit()

    def _finish(self, row, status, summary, failures, completed_at):
        row.status = status.value
        row.completed_at = completed_at
        row.acquisition_summary_json = _json(summary)
        row.failure_summary_json = _json(failures)
        self._session.commit()
        self._session.refresh(row)
        return self.read(row)

    @staticmethod
    def _safe_provider(metadata: dict[str, str | None]) -> dict[str, str]:
        allowed = {
            "provider": {"tavily", "openai", "brave", "local_codex", "disabled", "unsupported"},
            "credential_source": {"user", "deployment", "none"},
            "search_depth": {"basic"},
        }
        return {key: value for key, values in allowed.items() if (value := metadata.get(key)) in values}

    @staticmethod
    def read(row: OneOffDiscoveryExecution) -> OneOffExecutionRead:
        return OneOffExecutionRead(
            id=row.id, client_request_id=UUID(row.client_request_id),
            query=JobSearchQuery.model_validate_json(row.query_snapshot_json),
            policy=OneOffPolicy.model_validate_json(row.policy_snapshot_json),
            provider=json.loads(row.provider_metadata_json), status=OneOffStatus(row.status),
            started_at=row.started_at, completed_at=row.completed_at,
            acquisition_summary=json.loads(row.acquisition_summary_json),
            failure_summary=json.loads(row.failure_summary_json), discovery_run_id=row.discovery_run_id,
        )

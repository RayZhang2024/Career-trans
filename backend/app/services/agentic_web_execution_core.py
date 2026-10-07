"""Shared deterministic execution contract for agentic web discovery.

Saved schedules and transient one-off launches have different claim/lease rules,
but they use this component for the common web acquisition, canonical identity
resolution and bounded user evaluation steps.
"""

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.discovered_job import DiscoveredJob
from app.schemas.agentic_discovery import AgenticDiscoveryRequest
from app.schemas.candidate import CandidateContext
from app.schemas.discovery import JobSearchQuery
from app.schemas.user_job_discovery import DiscoveryRunCreateRequest, DiscoveryRunStatus
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.user_job_discovery_service import DiscoveryRunExecutionFailure


@dataclass(frozen=True)
class AgenticAcquisition:
    canonical_ids: set[str] = field(default_factory=set)
    counters: dict[str, int] = field(default_factory=dict)
    succeeded: bool = False
    failed: bool = False
    stop_evaluation: bool = False


@dataclass(frozen=True)
class EvaluationOutcome:
    discovery_run_id: str | None = None
    status: str | None = None
    funnel: dict[str, int] = field(default_factory=dict)
    failed: bool = False


class AgenticWebExecutionCore:
    """Own common agentic-web semantics without owning either claim lifecycle."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def acquire(
        self,
        *,
        agentic_service: object,
        provider_metadata: object,
        candidate_context: CandidateContext,
        query: JobSearchQuery,
        country: str,
        max_search_queries: int,
        max_search_results_per_query: int,
        max_pages_to_open: int,
        max_discovered_jobs: int,
    ) -> AgenticAcquisition:
        """Run one bounded web-only request and resolve its canonical persisted IDs."""
        try:
            response = agentic_service.discover(AgenticDiscoveryRequest(
                candidate_context=candidate_context,
                query=query,
                country=country,
                max_search_queries=max_search_queries,
                max_search_results_per_query=max_search_results_per_query,
                max_pages_to_open=max_pages_to_open,
                max_discovered_jobs=max_discovered_jobs,
            ))
        except Exception:
            return AgenticAcquisition(
                counters={"search_queries_executed": 0, "pages_opened": 0,
                          "page_fetch_failures": 0, "extraction_successes": 0,
                          "extraction_failures": 1},
                failed=True,
            )

        diagnostics = response.diagnostics
        errors = bool(
            diagnostics.search_errors or diagnostics.page_errors
            or diagnostics.page_fetch_failures or diagnostics.extraction_failures
        )
        ids = self.canonical_ids(response.listings[:max_discovered_jobs])
        counters = {
            "search_strategies_generated": diagnostics.search_strategies_generated,
            "search_queries_executed": diagnostics.search_queries_executed,
            "search_results_raw": diagnostics.search_results_raw,
            "search_results_unique": diagnostics.search_results_unique,
            "duplicate_search_results_removed": diagnostics.duplicate_search_results_removed,
            "deterministic_filtered_count": diagnostics.deterministic_filtered_count,
            "pages_selected": diagnostics.pages_selected,
            "pages_opened": diagnostics.pages_opened,
            "page_fetch_failures": diagnostics.page_fetch_failures,
            "extraction_successes": diagnostics.extraction_successes,
            "extraction_failures": diagnostics.extraction_failures,
            "detail_extraction_failures": diagnostics.detail_extraction_failures,
            "normalized_jobs": diagnostics.normalized_jobs,
            "deduplicated_jobs": diagnostics.deduplicated_jobs,
            "duplicate_jobs_removed": diagnostics.duplicate_jobs_removed,
            "unique_employers": diagnostics.unique_employers,
        }
        local_search_failed = (
            isinstance(provider_metadata, dict)
            and provider_metadata.get("provider") == "local_codex"
            and diagnostics.local_codex_search_failed
        )
        return AgenticAcquisition(
            canonical_ids=ids,
            counters=counters,
            succeeded=bool(response.listings[:max_discovered_jobs]) or not errors,
            failed=errors or local_search_failed,
            stop_evaluation=local_search_failed,
        )

    def canonical_ids(self, listings: list[object]) -> set[str]:
        """Resolve exactly the canonical listing identities persisted at acquisition."""
        keys = list(dict.fromkeys(
            SqlAlchemyDiscoveredJobStateStore.identity_key(listing)
            for listing in listings
        ))
        if not keys:
            return set()
        return set(self._session.scalars(
            select(DiscoveredJob.id).where(DiscoveredJob.identity_key.in_(keys))
        ).all())

    def evaluate(
        self,
        *,
        user_runs: object,
        user_id: str,
        query: JobSearchQuery,
        canonical_ids: set[str],
        max_semantic_candidates: int,
        max_full_analyses: int,
        min_relevance_score: float,
        link_run: Callable[[str], None] | None = None,
    ) -> EvaluationOutcome:
        if not canonical_ids:
            return EvaluationOutcome()
        request = DiscoveryRunCreateRequest(
            query=query,
            discovered_job_ids=sorted(canonical_ids),
            max_semantic_candidates=max_semantic_candidates,
            max_full_analyses=max_full_analyses,
            min_relevance_score=min_relevance_score,
        )
        try:
            run = user_runs.start(user_id, request, **({"link_run": link_run} if link_run is not None else {}))
        except DiscoveryRunExecutionFailure as exc:
            return EvaluationOutcome(
                discovery_run_id=exc.run_id,
                status=exc.status.value,
                failed=True,
            )
        return EvaluationOutcome(
            discovery_run_id=run.id,
            status=run.status.value,
            funnel={key: value for key, value in run.funnel.items() if isinstance(value, int)},
            failed=run.status.value == DiscoveryRunStatus.FAILED.value,
        )

    @staticmethod
    def final_status(
        *,
        acquisition_failed: bool,
        evaluation_status: str | None,
        useful_acquisition: bool,
    ) -> str:
        """Apply the common structural completed/partial/failed contract."""
        if evaluation_status == DiscoveryRunStatus.FAILED.value:
            return DiscoveryRunStatus.FAILED.value
        if evaluation_status == DiscoveryRunStatus.PARTIAL_FAILED.value:
            return DiscoveryRunStatus.PARTIAL_FAILED.value
        if acquisition_failed:
            return DiscoveryRunStatus.PARTIAL_FAILED.value if useful_acquisition else DiscoveryRunStatus.FAILED.value
        return DiscoveryRunStatus.COMPLETED.value

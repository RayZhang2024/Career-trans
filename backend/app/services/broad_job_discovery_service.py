"""Job-first, non-authoritative structured discovery from broad providers."""

from sqlalchemy.orm import Session

from app.providers.jobs.adzuna import AdzunaJobSource, AdzunaJobSourceError
from app.schemas.broad_job_discovery import (
    BroadJobDiscoveryResponse,
    BroadJobSearchQuery,
    BroadJobSourceDiagnostic,
)
from app.schemas.discovery import DiscoveredJobState
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts
from app.services.discovered_job_state_store import DiscoveredJobStateStore
from app.services.job_deduplication_service import JobDeduplicationService
from app.services.job_screening_service import JobScreeningService


class BroadJobDiscoveryService:
    """Acquire, screen, deduplicate, bound, and persist broad source results only."""

    def __init__(
        self,
        *,
        session: Session,
        source: AdzunaJobSource,
        state_store: DiscoveredJobStateStore,
        deduplicator: JobDeduplicationService | None = None,
        screening: JobScreeningService | None = None,
    ) -> None:
        self._session = session
        self._source = source
        self._state_store = state_store
        self._deduplicator = deduplicator or JobDeduplicationService()
        self._screening = screening or JobScreeningService()

    def discover(self, query: BroadJobSearchQuery) -> BroadJobDiscoveryResponse:
        try:
            result = self._source.search(query)
        except AdzunaJobSourceError as exc:
            return BroadJobDiscoveryResponse(
                provider_counts={},
                source_diagnostics=[
                    BroadJobSourceDiagnostic(
                        provider=self._source.name,
                        succeeded=False,
                        raw_count=0,
                        normalized_count=0,
                        rejected_count=0,
                        deduplicated_count=0,
                        bounded_out_count=0,
                        imported_count=0,
                        updated_count=0,
                        unchanged_count=0,
                        failure=str(exc),
                    )
                ],
                raw_count=0,
                normalized_count=0,
                rejected_count=0,
                deduplicated_count=0,
                bounded_count=0,
                bounded_out_count=0,
            )

        normalized = result.listings
        screened = self._screening.screen(normalized, query)
        deduplicated, duplicate_count = self._deduplicator.deduplicate(screened)
        listings = deduplicated[: query.max_results]
        states = self._state_store.persist(listings)
        self._session.commit()
        diagnostic = BroadJobSourceDiagnostic(
            provider=self._source.name,
            succeeded=True,
            raw_count=result.raw_count,
            normalized_count=len(normalized),
            rejected_count=len(normalized) - len(screened),
            deduplicated_count=duplicate_count,
            bounded_out_count=len(deduplicated) - len(listings),
            imported_count=sum(state is DiscoveredJobState.NEW for state in states.values()),
            updated_count=sum(state is DiscoveredJobState.UPDATED for state in states.values()),
            unchanged_count=sum(state is DiscoveredJobState.UNCHANGED for state in states.values()),
        )
        return BroadJobDiscoveryResponse(
            listings=listings,
            provider_counts={self._source.name: result.raw_count},
            source_diagnostics=[diagnostic],
            raw_count=result.raw_count,
            normalized_count=len(normalized),
            rejected_count=diagnostic.rejected_count,
            deduplicated_count=duplicate_count,
            bounded_count=len(listings),
            bounded_out_count=diagnostic.bounded_out_count,
            job_states=states,
            lifecycle_counts=DiscoveryLifecycleCounts(
                new=diagnostic.imported_count,
                updated=diagnostic.updated_count,
                unchanged=diagnostic.unchanged_count,
                inactive=0,
            ),
        )

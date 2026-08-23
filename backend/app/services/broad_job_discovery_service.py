from app.schemas.discovery import (
    BroadJobDiscoveryResponse,
    DiscoveryLifecycleCounts,
    DiscoveredJobState,
    JobSearchQuery,
)
from app.services.discovered_job_state_store import DiscoveredJobStateStore
from app.services.job_discovery_service import JobDiscoveryService


class BroadJobDiscoveryService:
    """Compose broad structured acquisition with the existing discovery lifecycle flow."""

    def __init__(
        self,
        *,
        discovery_service: JobDiscoveryService,
        state_store: DiscoveredJobStateStore,
    ) -> None:
        self._discovery_service = discovery_service
        self._state_store = state_store

    def search(self, query: JobSearchQuery) -> BroadJobDiscoveryResponse:
        collection = self._discovery_service.collect(query)
        job_states = self._state_store.synchronize(
            collection.deduplicated_listings,
            collection.authoritative_source_keys,
        )
        return BroadJobDiscoveryResponse(
            discovery=collection.response,
            job_states=job_states,
            lifecycle_counts=DiscoveryLifecycleCounts(
                new=sum(state is DiscoveredJobState.NEW for state in job_states.values()),
                updated=sum(state is DiscoveredJobState.UPDATED for state in job_states.values()),
                unchanged=sum(state is DiscoveredJobState.UNCHANGED for state in job_states.values()),
                inactive=sum(state is DiscoveredJobState.INACTIVE for state in job_states.values()),
            ),
        )

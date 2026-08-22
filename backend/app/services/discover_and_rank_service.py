from app.schemas.discovery_pipeline import (
    DiscoverAndRankRequest,
    DiscoverAndRankResponse,
)
from app.schemas.job_ranking import JobRankingRequest
from app.services.ats_resolver_service import AtsResolverService
from app.services.discovered_job_state_store import DiscoveredJobStateStore
from app.services.job_discovery_service import JobDiscoveryService
from app.services.job_ranking_service import JobRankingService
from app.services.job_source_factory import create_job_source


class DiscoverAndRankService:
    """Efficiently compose resolution, structured discovery, persistence, and ranking."""

    def __init__(
        self,
        *,
        resolver: AtsResolverService,
        ranking_service: JobRankingService,
        state_store: DiscoveredJobStateStore,
    ) -> None:
        self._resolver = resolver
        self._ranking_service = ranking_service
        self._state_store = state_store

    def discover_and_rank(self, request: DiscoverAndRankRequest) -> DiscoverAndRankResponse:
        resolution = self._resolver.resolve(request.companies)
        # Only sources proven by the resolver are fetched. Configured fallback boards
        # are deliberately excluded so a successful structured source is not fetched twice.
        sources = [
            create_job_source(item.resolved)
            for item in resolution.results
            if item.resolved is not None
        ]
        collection = JobDiscoveryService(sources).collect(request.query)
        job_states = self._state_store.synchronize(
            collection.raw_listings,
            collection.successful_source_keys,
        )
        ranking = self._ranking_service.rank(
            JobRankingRequest(
                jobs=collection.response.listings,
                candidate_context=request.candidate_context,
                max_semantic_candidates=request.max_semantic_candidates,
                max_full_analyses=request.max_full_analyses,
                min_relevance_score=request.min_relevance_score,
            )
        ) if collection.response.listings else self._empty_ranking()
        return DiscoverAndRankResponse(
            resolutions=resolution.results,
            discovery=collection.response,
            job_states=job_states,
            ranking=ranking,
        )

    @staticmethod
    def _empty_ranking():
        from app.schemas.job_ranking import JobRankingResponse

        return JobRankingResponse(
            discovered_count=0,
            gated_out_count=0,
            relevance_screened_count=0,
            finalist_count=0,
            analysed_count=0,
        )

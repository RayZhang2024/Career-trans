import logging

from app.providers.jobs.base import JobSource
from app.schemas.discovery import JobDiscoveryResponse, JobSearchQuery
from app.services.job_deduplication_service import JobDeduplicationService
from app.services.job_screening_service import JobScreeningService

logger = logging.getLogger(__name__)


class JobDiscoveryService:
    """Collect, deduplicate, and cheaply screen public job listings."""

    def __init__(
        self,
        providers: list[JobSource],
        deduplication_service: JobDeduplicationService | None = None,
        screening_service: JobScreeningService | None = None,
    ) -> None:
        self._providers = providers
        self._deduplication_service = deduplication_service or JobDeduplicationService()
        self._screening_service = screening_service or JobScreeningService()

    def discover(self, query: JobSearchQuery) -> JobDiscoveryResponse:
        raw_listings = []
        provider_counts: dict[str, int] = {}
        provider_errors: dict[str, str] = {}

        for provider in self._providers:
            try:
                listings = provider.search(query)
            except Exception as exc:  # Providers are isolated from one another in V1.
                logger.warning("Job discovery provider failed: %s", provider.name)
                provider_errors[provider.name] = f"{type(exc).__name__}: provider request failed"
                continue
            provider_counts[provider.name] = len(listings)
            raw_listings.extend(listings)

        deduplicated, deduplicated_count = self._deduplication_service.deduplicate(raw_listings)
        screened = self._screening_service.screen(deduplicated, query)
        listings = screened[: query.max_results]
        return JobDiscoveryResponse(
            listings=listings,
            provider_counts=provider_counts,
            raw_count=len(raw_listings),
            deduplicated_count=deduplicated_count,
            screened_out_count=len(deduplicated) - len(screened),
            provider_errors=provider_errors,
        )

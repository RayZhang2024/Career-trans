import logging
from dataclasses import dataclass

from app.providers.jobs.base import JobSource
from app.schemas.discovery import JobDiscoveryResponse, JobListing, JobSearchQuery
from app.services.job_deduplication_service import JobDeduplicationService
from app.services.job_screening_service import JobScreeningService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveryCollection:
    """Source results retained before query screening for persistence reconciliation."""

    raw_listings: list[JobListing]
    response: JobDiscoveryResponse
    successful_source_keys: set[str]


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
        return self.collect(query).response

    def collect(self, query: JobSearchQuery) -> DiscoveryCollection:
        raw_listings = []
        provider_counts: dict[str, int] = {}
        provider_errors: dict[str, str] = {}

        for provider in self._providers:
            source_keys = list(getattr(provider, "source_keys", [provider.name]))
            provider_key = source_keys[0] if len(source_keys) == 1 else provider.name
            try:
                listings = provider.search(query)
            except Exception as exc:  # Providers are isolated from one another in V1.
                logger.warning("Job discovery provider failed: %s", provider.name)
                provider_errors[provider_key] = f"{type(exc).__name__}: provider request failed"
                continue
            provider_counts[provider.name] = provider_counts.get(provider.name, 0) + len(listings)
            raw_listings.extend(listings)

        deduplicated, deduplicated_count = self._deduplication_service.deduplicate(raw_listings)
        screened = self._screening_service.screen(deduplicated, query)
        listings = screened[: query.max_results]
        response = JobDiscoveryResponse(
            listings=listings,
            provider_counts=provider_counts,
            raw_count=len(raw_listings),
            deduplicated_count=deduplicated_count,
            screened_out_count=len(deduplicated) - len(screened),
            provider_errors=provider_errors,
        )
        successful_source_keys = {
            key
            for provider in self._providers
            if (
                (source_keys := list(getattr(provider, "source_keys", [provider.name])))
                and (source_keys[0] if len(source_keys) == 1 else provider.name) not in provider_errors
            )
            for key in source_keys
        }
        return DiscoveryCollection(
            raw_listings=raw_listings,
            response=response,
            successful_source_keys=successful_source_keys,
        )

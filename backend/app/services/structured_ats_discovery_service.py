"""Bounded collection from persisted, resolved public ATS sources."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_career_source import CompanyCareerSource
from app.schemas.discovery import (
    DiscoveredJobLifecycleItem,
    DiscoveredJobState,
    JobListing,
    JobProvenance,
    JobSearchQuery,
)
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts
from app.schemas.job_sources import CompanySourceStatus, ResolvedJobSource
from app.schemas.structured_ats_discovery import (
    StructuredAtsDiscoveryRequest,
    StructuredAtsDiscoveryResponse,
    StructuredAtsSourceDiagnostic,
)
from app.services.structured_ats_failure_diagnostics import (
    StructuredAtsSourceConfigurationError,
    classify_structured_ats_failure,
)
from app.services.company_source_discovery_service import canonical_company_key
from app.services.discovered_job_state_store import (
    DiscoveredJobStateStore,
    SqlAlchemyDiscoveredJobStateStore,
)
from app.services.job_deduplication_service import JobDeduplicationService
from app.services.job_screening_service import JobScreeningService
from app.services.job_source_factory import create_job_source


class StructuredAtsDiscoveryService:
    """Collect known public ATS boards without semantic, web-search, or ranking work."""

    def __init__(
        self,
        *,
        session: Session,
        state_store: DiscoveredJobStateStore,
        deduplicator: JobDeduplicationService | None = None,
        screening: JobScreeningService | None = None,
    ) -> None:
        self._session = session
        self._state_store = state_store
        self._deduplicator = deduplicator or JobDeduplicationService()
        self._screening = screening or JobScreeningService()

    def discover(self, request: StructuredAtsDiscoveryRequest) -> StructuredAtsDiscoveryResponse:
        query = JobSearchQuery(
            # A seed is required by the existing query contract but is deliberately
            # not used as a positive title filter by JobScreeningService.
            keywords=request.keywords or ["structured ATS"],
            locations=request.locations,
            remote_ok=request.remote_ok,
            companies=request.companies,
            excluded_companies=request.excluded_companies,
            excluded_title_terms=request.excluded_title_terms,
            employment_types=request.employment_types,
            max_results=request.max_results,
        )
        records = self._records(request)
        diagnostics: list[StructuredAtsSourceDiagnostic] = []
        accepted_by_source: dict[str, list[JobListing]] = {}
        raw: list[JobListing] = []

        for record in records:
            source_key = self._source_key(record)
            try:
                listings = self._collect_source(record, query)
            except Exception as exc:
                diagnostics.append(
                    StructuredAtsSourceDiagnostic(
                        company=record.company_name,
                        provider=record.provider or "unknown",
                        source_token=record.source_token or "",
                        succeeded=False,
                        discovered_count=0,
                        imported_count=0,
                        unchanged_count=0,
                        updated_count=0,
                        deduplicated_count=0,
                        bounded_out_count=0,
                        rejected_count=0,
                        failure_kind=classify_structured_ats_failure(exc),
                    )
                )
                continue

            record.last_successful_fetch_at = self._now()
            raw.extend(listings)
            accepted = self._screening.screen(listings, query)
            accepted_by_source[source_key] = accepted
            diagnostics.append(
                StructuredAtsSourceDiagnostic(
                    company=record.company_name,
                    provider=record.provider or "unknown",
                    source_token=record.source_token or "",
                    succeeded=True,
                    discovered_count=len(listings),
                    imported_count=0,
                    unchanged_count=0,
                    updated_count=0,
                    deduplicated_count=0,
                    bounded_out_count=0,
                    rejected_count=len(listings) - len(accepted),
                )
            )

        accepted = [listing for source in accepted_by_source.values() for listing in source]
        deduplicated, duplicate_count = self._deduplicator.deduplicate(accepted)
        listings = self._select_candidates(
            deduplicated,
            keywords=request.keywords,
            limit=request.max_results,
        )
        deduplicated_ids = {id(listing) for listing in deduplicated}
        persisted_ids = {id(listing) for listing in listings}
        states = self._state_store.persist(listings)
        self._session.commit()
        for diagnostic in diagnostics:
            if not diagnostic.succeeded:
                continue
            source_key = f"{diagnostic.provider}:{diagnostic.source_token}"
            source_accepted = accepted_by_source[source_key]
            source_deduplicated = [item for item in source_accepted if id(item) in deduplicated_ids]
            persisted = [item for item in source_deduplicated if id(item) in persisted_ids]
            diagnostic.deduplicated_count = (
                len(source_accepted) - len(source_deduplicated)
            )
            diagnostic.bounded_out_count = len(source_deduplicated) - len(persisted)
            source_states = [
                states.get(SqlAlchemyDiscoveredJobStateStore.identity_key(item))
                for item in persisted
            ]
            diagnostic.imported_count = sum(
                state is DiscoveredJobState.NEW for state in source_states
            )
            diagnostic.unchanged_count = sum(
                state is DiscoveredJobState.UNCHANGED for state in source_states
            )
            diagnostic.updated_count = sum(
                state is DiscoveredJobState.UPDATED for state in source_states
            )
        return StructuredAtsDiscoveryResponse(
            listings=listings,
            source_diagnostics=diagnostics,
            raw_count=len(raw),
            deduplicated_count=duplicate_count,
            rejected_count=len(raw) - len(accepted),
            job_states=states,
            lifecycle_jobs=self._lifecycle_jobs(listings, states),
            lifecycle_counts=DiscoveryLifecycleCounts(
                new=sum(state is DiscoveredJobState.NEW for state in states.values()),
                updated=sum(state is DiscoveredJobState.UPDATED for state in states.values()),
                unchanged=sum(state is DiscoveredJobState.UNCHANGED for state in states.values()),
                inactive=sum(state is DiscoveredJobState.INACTIVE for state in states.values()),
            ),
        )

    @staticmethod
    def _lifecycle_jobs(
        listings: list[JobListing],
        states: dict[str, DiscoveredJobState],
    ) -> list[DiscoveredJobLifecycleItem]:
        return [
            DiscoveredJobLifecycleItem(job=listing, state=states[SqlAlchemyDiscoveredJobStateStore.identity_key(listing)])
            for listing in listings
            if SqlAlchemyDiscoveredJobStateStore.identity_key(listing) in states
        ]

    def _records(self, request: StructuredAtsDiscoveryRequest) -> list[CompanyCareerSource]:
        records = list(
            self._session.scalars(
                select(CompanyCareerSource)
                .where(CompanyCareerSource.status == CompanySourceStatus.RESOLVED)
                .order_by(CompanyCareerSource.canonical_company_key)
            )
        )
        provider_filter = {value.casefold().strip() for value in request.providers if value.strip()}
        company_filter = {canonical_company_key(value) for value in request.companies if value.strip()}
        return [
            record
            for record in records
            if record.provider
            and record.source_token
            and record.careers_url
            and (not provider_filter or record.provider.casefold() in provider_filter)
            and (not company_filter or record.canonical_company_key in company_filter)
        ][: request.max_sources]

    @staticmethod
    def _source_key(record: CompanyCareerSource) -> str:
        return f"{record.provider}:{record.source_token}"

    @classmethod
    def _select_candidates(
        cls,
        listings: list[JobListing],
        *,
        keywords: list[str],
        limit: int,
    ) -> list[JobListing]:
        """Preserve one source front, then prioritize remaining eligible candidates."""
        fronts: dict[tuple[str, str], list[tuple[int, int, JobListing]]] = {}
        for index, listing in enumerate(listings):
            company_key = (listing.company or listing.source).casefold()
            source_key = cls._listing_source_key(listing)
            affinity = cls._search_theme_affinity(listing, keywords)
            fronts.setdefault((company_key, source_key), []).append((affinity, index, listing))

        # Search themes prioritize eligible candidates only. They do not remove a
        # listing, and original input order is the deterministic tie-breaker.
        for candidates in fronts.values():
            candidates.sort(key=lambda item: (-item[0], item[1]))

        representatives = [candidates.pop(0) for candidates in fronts.values()]
        representatives.sort(key=lambda item: (-item[0], item[1]))
        selected = representatives[:limit]
        if len(selected) == limit:
            return [listing for _, _, listing in selected]

        remaining = [candidate for candidates in fronts.values() for candidate in candidates]
        remaining.sort(key=lambda item: (-item[0], item[1]))
        selected.extend(remaining[: limit - len(selected)])
        return [listing for _, _, listing in selected]

    @staticmethod
    def _listing_source_key(listing: JobListing) -> str:
        return ":".join(value.casefold() for value in (listing.source, listing.source_token or ""))

    @staticmethod
    def _search_theme_affinity(listing: JobListing, keywords: list[str]) -> int:
        terms = {
            term
            for keyword in keywords
            for term in re.findall(r"[a-z0-9]+", keyword.casefold())
        }
        if not terms:
            return 0
        title_terms = set(re.findall(r"[a-z0-9]+", listing.title.casefold()))
        description_terms = set(re.findall(r"[a-z0-9]+", (listing.description or "").casefold()))
        return 2 * len(terms & title_terms) + len(terms & description_terms)

    @staticmethod
    def _now():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)

    @staticmethod
    def _collect_source(record: CompanyCareerSource, query: JobSearchQuery) -> list[JobListing]:
        try:
            source = create_job_source(
                ResolvedJobSource(
                    company=record.company_name,
                    provider=record.provider or "",
                    source_token=record.source_token or "",
                    careers_url=record.careers_url or "",
                )
            )
        except ValueError as exc:
            # `create_job_source` uses ValueError only for unsupported factory configuration.
            raise StructuredAtsSourceConfigurationError from exc
        return [
            listing.model_copy(
                update={
                    "company": listing.company or record.company_name,
                    "provenance": JobProvenance(
                        runtime="career-trans",
                        source_ref=record.careers_url,
                        discovered_via="structured_ats",
                    ),
                }
            )
            for listing in source.search(query)
        ]

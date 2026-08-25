import json
import re
from collections import Counter
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit

from app.agents.agentic_discovery import PageVacancyExtractor, SearchStrategyGenerator
from app.providers.page_fetch import PageFetcher
from app.providers.web_search import WebSearchProvider
from app.schemas.agentic_discovery import (
    AgenticDiscoveryDiagnostics,
    AgenticDiscoveryRequest,
    AgenticDiscoveryResponse,
    ExtractedVacancy,
    PageContent,
    SearchResult,
    SearchStrategy,
)
from app.schemas.discovery import DiscoveredJobState, JobListing
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts
from app.services.candidate_profile_compaction import candidate_career_profile, candidate_search_profile
from app.services.discovered_job_state_store import DiscoveredJobStateStore
from app.services.job_deduplication_service import JobDeduplicationService
from app.services.job_screening_service import JobScreeningService


class AgenticJobDiscoveryService:
    """Bounded web-search frontier with deterministic validation and non-authoritative lifecycle."""

    def __init__(
        self,
        *,
        strategy_generator: SearchStrategyGenerator,
        search_provider: WebSearchProvider,
        page_fetcher: PageFetcher,
        vacancy_extractor: PageVacancyExtractor,
        state_store: DiscoveredJobStateStore,
    ) -> None:
        self._strategy_generator = strategy_generator
        self._search_provider = search_provider
        self._page_fetcher = page_fetcher
        self._vacancy_extractor = vacancy_extractor
        self._state_store = state_store
        self._deduplicator = JobDeduplicationService()
        self._screening = JobScreeningService()

    def discover(self, request: AgenticDiscoveryRequest) -> AgenticDiscoveryResponse:
        diagnostics = AgenticDiscoveryDiagnostics()
        strategies = self._strategies(request, diagnostics)
        raw_results = self._search(strategies, request, diagnostics)
        candidates = self._select_pages(raw_results, request, diagnostics)
        listings = self._extract(candidates, request, diagnostics)
        screened = [
            listing
            for listing in listings
            if self._screening.matches_hard_constraints(listing, request.query)
        ]
        deduplicated, removed = self._deduplicator.deduplicate(screened)
        final_listings = deduplicated[: request.max_discovered_jobs]
        # Web evidence is non-authoritative: only accepted public vacancies are persisted,
        # and successful source keys are deliberately empty so omission cannot inactivate jobs.
        job_states = self._state_store.synchronize(final_listings, set())
        diagnostics.normalized_jobs = len(listings)
        diagnostics.deduplicated_jobs = len(deduplicated)
        diagnostics.duplicate_jobs_removed = removed
        diagnostics.unique_employers = len({job.company.casefold() for job in final_listings if job.company})
        diagnostics.source_counts = dict(Counter(job.source for job in final_listings))
        return AgenticDiscoveryResponse(
            strategies=strategies,
            listings=final_listings,
            job_states={key: state.value for key, state in job_states.items()},
            lifecycle_counts=DiscoveryLifecycleCounts(
                new=sum(state is DiscoveredJobState.NEW for state in job_states.values()),
                updated=sum(state is DiscoveredJobState.UPDATED for state in job_states.values()),
                unchanged=sum(state is DiscoveredJobState.UNCHANGED for state in job_states.values()),
                inactive=sum(state is DiscoveredJobState.INACTIVE for state in job_states.values()),
            ),
            diagnostics=diagnostics,
        )

    def _strategies(self, request: AgenticDiscoveryRequest, diagnostics: AgenticDiscoveryDiagnostics) -> list[SearchStrategy]:
        try:
            generated = self._strategy_generator.generate(
                candidate_search_profile(request.candidate_context),
                candidate_career_profile(request.candidate_context),
                request.max_search_queries,
            )
        except Exception as exc:
            diagnostics.search_errors["strategy_generation"] = self._error(exc)
            return []
        unique: list[SearchStrategy] = []
        seen: set[str] = set()
        for strategy in sorted(generated, key=lambda item: (-item.priority, item.query.casefold())):
            key = " ".join(strategy.query.casefold().split())
            if key and key not in seen:
                seen.add(key)
                unique.append(strategy)
            if len(unique) == request.max_search_queries:
                break
        diagnostics.search_strategies_generated = len(unique)
        return unique

    def _search(self, strategies: list[SearchStrategy], request: AgenticDiscoveryRequest, diagnostics: AgenticDiscoveryDiagnostics) -> list[SearchResult]:
        results: list[SearchResult] = []
        for strategy in strategies:
            diagnostics.search_queries_executed += 1
            try:
                results.extend(self._search_provider.search(strategy.query, request.max_search_results_per_query))
            except Exception as exc:
                diagnostics.search_errors[strategy.query] = self._error(exc)
        diagnostics.search_results_raw = len(results)
        return results

    def _select_pages(self, results: list[SearchResult], request: AgenticDiscoveryRequest, diagnostics: AgenticDiscoveryDiagnostics) -> list[SearchResult]:
        unique: list[SearchResult] = []
        seen: set[str] = set()
        for result in results:
            canonical = self._canonical_url(result.url)
            if canonical in seen:
                continue
            seen.add(canonical)
            unique.append(result.model_copy(update={"url": canonical}))
        diagnostics.search_results_unique = len(unique)
        filtered = [result for result in unique if self._is_promising_result(result, request)]
        diagnostics.deterministic_filtered_count = len(unique) - len(filtered)
        ordered = sorted(filtered, key=lambda result: (-self._result_score(result, request), result.rank, result.url))
        selected = ordered[: request.max_pages_to_open]
        diagnostics.pages_selected = len(selected)
        diagnostics.domain_counts = dict(Counter(result.domain for result in selected))
        return selected

    def _extract(self, candidates: list[SearchResult], request: AgenticDiscoveryRequest, diagnostics: AgenticDiscoveryDiagnostics) -> list[JobListing]:
        listings: list[JobListing] = []
        for candidate in candidates:
            try:
                page = self._page_fetcher.fetch(candidate.url)
                diagnostics.pages_opened += 1
            except Exception as exc:
                diagnostics.page_fetch_failures += 1
                diagnostics.page_errors[candidate.url] = self._error(exc)
                continue
            extracted = self._metadata_vacancy(page)
            if extracted is None:
                try:
                    extracted = self._vacancy_extractor.extract(page)
                except Exception as exc:
                    diagnostics.extraction_failures += 1
                    diagnostics.page_errors[candidate.url] = self._error(exc)
                    continue
            listing = self._normalize(extracted, page)
            if listing is None:
                diagnostics.extraction_failures += 1
                continue
            diagnostics.extraction_successes += 1
            listings.append(listing)
        return listings

    @staticmethod
    def _metadata_vacancy(page: PageContent) -> ExtractedVacancy | None:
        for match in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page.html, flags=re.IGNORECASE | re.DOTALL):
            try:
                payload = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("@type") != "JobPosting":
                    continue
                organization = entry.get("hiringOrganization")
                location = entry.get("jobLocation")
                address = location.get("address") if isinstance(location, dict) else None
                return ExtractedVacancy(
                    title=entry.get("title") if isinstance(entry.get("title"), str) else None,
                    company=organization.get("name") if isinstance(organization, dict) and isinstance(organization.get("name"), str) else None,
                    location=address.get("addressLocality") if isinstance(address, dict) and isinstance(address.get("addressLocality"), str) else None,
                    description=entry.get("description") if isinstance(entry.get("description"), str) else None,
                    employment_type=entry.get("employmentType") if isinstance(entry.get("employmentType"), str) else None,
                )
        return None

    @staticmethod
    def _normalize(extracted: ExtractedVacancy | None, page: PageContent) -> JobListing | None:
        if extracted is None or not extracted.title or not extracted.title.strip():
            return None
        url = AgenticJobDiscoveryService._canonical_url(page.final_url)
        domain = (urlsplit(url).hostname or "").casefold()
        return JobListing(
            source="agentic_web",
            source_token=domain or None,
            external_id=sha256(url.encode("utf-8")).hexdigest()[:32],
            title=extracted.title.strip(),
            company=extracted.company.strip() if extracted.company and extracted.company.strip() else None,
            location=extracted.location.strip() if extracted.location and extracted.location.strip() else None,
            url=url,
            description=extracted.description.strip() if extracted.description and extracted.description.strip() else None,
            posted_at=extracted.posted_at,
            employment_type=extracted.employment_type,
            work_arrangement=extracted.work_arrangement,
        )

    @staticmethod
    def _is_promising_result(result: SearchResult, request: AgenticDiscoveryRequest) -> bool:
        """Reject only objective non-vacancy pages and explicit exclusions.

        Search-result metadata is incomplete evidence.  It cannot prove a
        location mismatch, and title/snippet relevance is reserved for the
        bounded semantic screening stage after a vacancy is discovered.
        """
        text = f"{result.title} {result.snippet} {result.url}".casefold()
        if any(term in text for term in ("salary guide", "course", "training", "news", "blog", "article")):
            return False
        if urlsplit(result.url).path in ("", "/"):
            return False
        if any(term.casefold() in text for term in request.query.excluded_title_terms):
            return False
        return True

    @staticmethod
    def _result_score(result: SearchResult, request: AgenticDiscoveryRequest) -> int:
        text = f"{result.domain} {urlsplit(result.url).path}".casefold()
        score = 0
        if any(name in text for name in ("greenhouse", "lever", "ashby", "smartrecruiters", "recruitee")):
            score += 4
        if any(term in text for term in ("/jobs", "/job/", "/careers", "/positions", "/vacancies")):
            score += 2
        return score

    @staticmethod
    def _canonical_url(url: str) -> str:
        parts = urlsplit(url.strip())
        return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/") or "/", "", ""))

    @staticmethod
    def _error(exc: Exception) -> str:
        return f"{type(exc).__name__}: operation failed"

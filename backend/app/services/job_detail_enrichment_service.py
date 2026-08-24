"""Bounded direct-page enrichment for already-persisted external vacancies."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agentic_discovery import PageVacancyExtractor
from app.models.discovered_job import DiscoveredJob
from app.providers.page_fetch import PageFetcher
from app.schemas.agentic_discovery import ExtractedVacancy
from app.schemas.discovery import JobListing, JobProvenance
from app.schemas.job_enrichment import (
    JobEnrichmentOutcome,
    JobEnrichmentResponse,
    JobEnrichmentStatus,
)
from app.services.agentic_job_discovery_service import AgenticJobDiscoveryService
from app.services.discovered_job_state_store import DiscoveredJobStateStore
from app.services.job_analysis_service import JobAnalysisService


class JobDetailEnrichmentService:
    """Fetch a known public vacancy URL; never search, rank, or use candidate data."""

    def __init__(
        self,
        *,
        session: Session,
        page_fetcher: PageFetcher,
        vacancy_extractor: PageVacancyExtractor,
        job_analysis_service: JobAnalysisService,
        state_store: DiscoveredJobStateStore,
    ) -> None:
        self._session = session
        self._page_fetcher = page_fetcher
        self._vacancy_extractor = vacancy_extractor
        self._job_analysis_service = job_analysis_service
        self._state_store = state_store

    def enrich_recent(self, *, limit: int) -> JobEnrichmentResponse:
        records = self._session.scalars(
            select(DiscoveredJob)
            .where(DiscoveredJob.source == "agent_runtime")
            .order_by(DiscoveredJob.last_seen_at.desc(), DiscoveredJob.id.asc())
            .limit(limit)
        ).all()
        return JobEnrichmentResponse(outcomes=[self._enrich(record) for record in records])

    def _enrich(self, record: DiscoveredJob) -> JobEnrichmentOutcome:
        if not self._needs_enrichment(record):
            return self._outcome(record, JobEnrichmentStatus.SKIPPED, "existing job detail is usable")
        try:
            page = self._page_fetcher.fetch(record.url)
        except Exception:
            return self._outcome(record, JobEnrichmentStatus.FAILED, "page retrieval failed")
        try:
            extracted = AgenticJobDiscoveryService._metadata_vacancy(page)
            if extracted is None or not self._description(extracted):
                extracted = self._vacancy_extractor.extract(page)
        except Exception:
            return self._outcome(record, JobEnrichmentStatus.FAILED, "vacancy extraction failed")
        if extracted is None or not self._description(extracted):
            return self._outcome(record, JobEnrichmentStatus.STILL_UNASSESSED, "no supported vacancy description found")
        description = self._description(extracted)
        assert description is not None
        try:
            profile = self._job_analysis_service.analyse_text(description)
        except Exception:
            return self._outcome(record, JobEnrichmentStatus.STILL_UNASSESSED, "enriched detail could not be analysed")
        if not profile.requirements:
            return self._outcome(record, JobEnrichmentStatus.STILL_UNASSESSED, "no extractable requirements after enrichment")

        listing = JobListing(
            source=record.source,
            source_token=record.source_token,
            external_id=record.external_id,
            title=self._text(extracted.title) or record.title,
            company=self._text(extracted.company) or record.company,
            location=self._text(extracted.location) or record.location,
            # Keep the existing stable vacancy identity; redirects are fetch evidence only.
            url=record.url,
            description=description,
            posted_at=extracted.posted_at or record.posted_at,
            employment_type=self._text(extracted.employment_type) or record.employment_type,
            work_arrangement=self._text(extracted.work_arrangement) or record.work_arrangement,
            provenance=JobProvenance(
                runtime="career-trans",
                # Existing provenance is deliberately bounded; the job record retains
                # the complete canonical vacancy URL.
                source_ref=record.url[:300],
                discovered_via="direct_page",
            ),
        )
        self._state_store.synchronize([listing], set())
        return self._outcome(record, JobEnrichmentStatus.ENRICHED)

    def _needs_enrichment(self, record: DiscoveredJob) -> bool:
        if not record.description or not record.description.strip():
            return True
        try:
            return not self._job_analysis_service.analyse_text(record.description).requirements
        except Exception:
            return True

    @staticmethod
    def _description(extracted: ExtractedVacancy) -> str | None:
        return JobDetailEnrichmentService._text(extracted.description)

    @staticmethod
    def _text(value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @staticmethod
    def _outcome(record: DiscoveredJob, status: JobEnrichmentStatus, reason: str | None = None) -> JobEnrichmentOutcome:
        return JobEnrichmentOutcome(
            job_id=record.id,
            title=record.title,
            company=record.company,
            status=status,
            reason=reason,
        )

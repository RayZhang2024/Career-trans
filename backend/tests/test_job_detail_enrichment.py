from datetime import datetime, timezone
from hashlib import sha256

import pytest
from sqlalchemy import select

from app.models.discovered_job import DiscoveredJob
from app.models.discovered_job_provenance import DiscoveredJobProvenance
from app.providers.url_safety import UnsafePageUrlError
from app.schemas.agentic_discovery import ExtractedVacancy, PageContent
from app.schemas.job import JobProfile, JobRequirement
from app.schemas.job_enrichment import JobEnrichmentStatus
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.job_detail_enrichment_service import JobDetailEnrichmentService


USABLE_DETAIL = " ".join(["Recovered candidate-criteria detail."] * 20)


def _record(db_session, *, description: str | None = None) -> DiscoveredJob:
    now = datetime.now(timezone.utc)
    record = DiscoveredJob(
        identity_key="url:https://jobs.example.test/1",
        source="agent_runtime",
        source_token="codex",
        title="Applied AI Engineer",
        company="Example Systems",
        location="London",
        url="https://jobs.example.test/1",
        description=description,
        content_hash=sha256((description or "").encode()).hexdigest(),
        state="new",
        last_seen_at=now,
        last_changed_at=now,
    )
    db_session.add(record)
    db_session.commit()
    return record


class FakeFetcher:
    def __init__(self, page: PageContent | Exception) -> None:
        self.page = page
        self.urls: list[str] = []

    def fetch(self, url: str) -> PageContent:
        self.urls.append(url)
        if isinstance(self.page, Exception):
            raise self.page
        return self.page


class FakeVacancyExtractor:
    def __init__(self, vacancy: ExtractedVacancy | Exception | None) -> None:
        self.vacancy = vacancy
        self.calls = 0

    def extract(self, _page: PageContent) -> ExtractedVacancy | None:
        self.calls += 1
        if isinstance(self.vacancy, Exception):
            raise self.vacancy
        return self.vacancy


class FakeJobAnalysis:
    def __init__(self, *, usable_text: str = USABLE_DETAIL) -> None:
        self.usable_text = usable_text
        self.calls: list[str] = []

    def analyse_text(self, text: str) -> JobProfile:
        self.calls.append(text)
        requirements = [JobRequirement(text="Python")] if text == self.usable_text else []
        return JobProfile(title="Applied AI Engineer", requirements=requirements)


def _service(db_session, *, fetcher, extractor, analysis) -> JobDetailEnrichmentService:
    return JobDetailEnrichmentService(
        session=db_session,
        page_fetcher=fetcher,
        vacancy_extractor=extractor,
        job_analysis_service=analysis,
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    )


def _page(description: str) -> PageContent:
    return PageContent(
        requested_url="https://jobs.example.test/1",
        final_url="https://jobs.example.test/1",
        html=(
            '<script type="application/ld+json">'
            '{"@type":"JobPosting","title":"Applied AI Engineer","description":"'
            + description
            + '","hiringOrganization":{"name":"Example Systems"}}'
            "</script>"
        ),
    )


@pytest.mark.parametrize("description", [None, "   "])
def test_missing_or_blank_detail_enriches_existing_record_and_records_direct_page_provenance(
    db_session,
    description: str | None,
) -> None:
    record = _record(db_session, description=description)
    fetcher = FakeFetcher(_page(USABLE_DETAIL))
    extractor = FakeVacancyExtractor(None)
    service = _service(
        db_session,
        fetcher=fetcher,
        extractor=extractor,
        analysis=FakeJobAnalysis(),
    )

    result = service.enrich_recent(limit=10)

    assert result.outcomes[0].status is JobEnrichmentStatus.ENRICHED
    assert fetcher.urls == [record.url]
    assert extractor.calls == 0
    refreshed = db_session.scalar(select(DiscoveredJob).where(DiscoveredJob.id == record.id))
    assert refreshed is not None and refreshed.description == USABLE_DETAIL
    assert len(db_session.scalars(select(DiscoveredJob)).all()) == 1
    provenance = db_session.scalar(select(DiscoveredJobProvenance).where(DiscoveredJobProvenance.job_id == record.id))
    assert provenance is not None
    assert (provenance.runtime, provenance.source_ref, provenance.discovered_via) == (
        "career-trans", record.url, "direct_page"
    )


def test_nonempty_unusable_detail_is_enriched_but_usable_existing_detail_is_skipped(db_session) -> None:
    _record(db_session, description="Old summary")
    analysis = FakeJobAnalysis()
    service = _service(
        db_session,
        fetcher=FakeFetcher(_page(USABLE_DETAIL)),
        extractor=FakeVacancyExtractor(None),
        analysis=analysis,
    )
    assert service.enrich_recent(limit=10).outcomes[0].status is JobEnrichmentStatus.ENRICHED

    second = _service(
        db_session,
        fetcher=FakeFetcher(AssertionError("usable record must not be fetched")),
        extractor=FakeVacancyExtractor(None),
        analysis=analysis,
    )
    assert second.enrich_recent(limit=10).outcomes[0].status is JobEnrichmentStatus.SKIPPED


def test_short_detail_with_one_extractable_requirement_is_reenriched_for_coverage(db_session) -> None:
    summary = "A concise vacancy summary naming Python as one requirement."
    record = _record(db_session, description=summary)

    class SingleRequirementAnalysis:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def analyse_text(self, text: str) -> JobProfile:
            self.calls.append(text)
            return JobProfile(
                title="Applied AI Engineer",
                requirements=[JobRequirement(text="Python")],
            )

    analysis = SingleRequirementAnalysis()
    fetcher = FakeFetcher(_page(USABLE_DETAIL))
    result = _service(
        db_session,
        fetcher=fetcher,
        extractor=FakeVacancyExtractor(None),
        analysis=analysis,
    ).enrich_recent(limit=10)

    assert len(summary) < JobDetailEnrichmentService.MIN_USABLE_DESCRIPTION_CHARACTERS
    assert result.outcomes[0].status is JobEnrichmentStatus.ENRICHED
    assert fetcher.urls == [record.url]
    # The short summary is not sent to semantic extraction before direct-page
    # recovery; only the recovered detail is assessed.
    assert analysis.calls == [USABLE_DETAIL]
    assert db_session.get(DiscoveredJob, record.id).description == USABLE_DETAIL


def test_still_unusable_or_failed_fetch_never_overwrites_existing_description(db_session) -> None:
    record = _record(db_session, description="Old summary")
    still_unusable = _service(
        db_session,
        fetcher=FakeFetcher(_page("Still unusable")),
        extractor=FakeVacancyExtractor(None),
        analysis=FakeJobAnalysis(),
    )
    result = still_unusable.enrich_recent(limit=10)
    assert result.outcomes[0].status is JobEnrichmentStatus.STILL_UNASSESSED
    assert db_session.get(DiscoveredJob, record.id).description == "Old summary"

    failed = _service(
        db_session,
        fetcher=FakeFetcher(UnsafePageUrlError("Page URL resolves to a non-public address.")),
        extractor=FakeVacancyExtractor(None),
        analysis=FakeJobAnalysis(),
    )
    assert failed.enrich_recent(limit=10).outcomes[0].status is JobEnrichmentStatus.FAILED
    assert db_session.get(DiscoveredJob, record.id).description == "Old summary"


def test_page_extraction_failure_does_not_overwrite_better_existing_detail(db_session) -> None:
    record = _record(db_session, description="Old summary")
    service = _service(
        db_session,
        fetcher=FakeFetcher(PageContent(requested_url=record.url, final_url=record.url, html="<html>no metadata</html>")),
        extractor=FakeVacancyExtractor(RuntimeError("bad extraction")),
        analysis=FakeJobAnalysis(),
    )

    assert service.enrich_recent(limit=10).outcomes[0].status is JobEnrichmentStatus.FAILED
    assert db_session.get(DiscoveredJob, record.id).description == "Old summary"

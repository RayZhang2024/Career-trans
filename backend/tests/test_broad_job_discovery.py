from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

import app.cli as cli
from app.models.discovered_job import DiscoveredJob
from app.providers.jobs.adzuna import AdzunaJobSource, AdzunaJobSourceError, BroadJobSourceResult
from app.schemas.broad_job_discovery import BroadJobSearchQuery
from app.schemas.discovery import DiscoveredJobState, JobListing
from app.services.broad_job_discovery_service import BroadJobDiscoveryService
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.job_deduplication_service import JobDeduplicationService


def _adzuna_result(index: int = 1) -> dict:
    return {
        "id": f"adzuna-{index}",
        "title": "Applied AI Engineer",
        "company": {"display_name": "Example Labs"},
        "location": {"display_name": "London"},
        "redirect_url": f"https://jobs.example.test/{index}?tracking=source",
        "description": "Build reliable machine-learning services.",
        "created": "2026-08-25T12:00:00Z",
        "contract_type": "permanent",
        "contract_time": "full_time",
    }


def test_adzuna_maps_query_and_normalizes_shared_listing_contract() -> None:
    urls: list[str] = []

    def fetch(url: str) -> dict:
        urls.append(url)
        return {"results": [_adzuna_result()]}

    result = AdzunaJobSource(app_id="app-id", app_key="app-key", fetch_json=fetch).search(
        BroadJobSearchQuery(
            keywords=["AI Engineer"],
            locations=["London"],
            country="gb",
            posted_within_days=30,
            salary_min=70000,
            max_results=20,
        )
    )

    params = parse_qs(urlsplit(urls[0]).query)
    assert urlsplit(urls[0]).path == "/v1/api/jobs/gb/search/1"
    assert params["what"] == ["AI Engineer"]
    assert params["where"] == ["London"]
    assert params["max_days_old"] == ["30"]
    assert params["salary_min"] == ["70000"]
    assert params["results_per_page"] == ["20"]
    listing = result.listings[0]
    assert result.raw_count == 1
    assert listing.source == "adzuna"
    assert listing.source_token == "gb"
    assert listing.external_id == "adzuna-1"
    assert listing.company == "Example Labs"
    assert listing.location == "London"
    assert listing.description == "Build reliable machine-learning services."
    assert listing.posted_at == datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
    assert listing.employment_type == "Permanent, Full time"
    assert listing.provenance and listing.provenance.discovered_via == "adzuna"


def test_adzuna_pagination_is_bounded_and_malformed_results_are_isolated() -> None:
    urls: list[str] = []

    def fetch(url: str) -> dict:
        urls.append(url)
        if len(urls) == 1:
            return {"results": [_adzuna_result(index) for index in range(49)] + [{"id": "missing-title"}]}
        return {"results": [_adzuna_result(50)]}

    result = AdzunaJobSource(app_id="app-id", app_key="app-key", fetch_json=fetch).search(
        BroadJobSearchQuery(keywords=["Engineer"], max_results=51)
    )

    assert len(urls) == 2
    assert parse_qs(urlsplit(urls[0]).query)["results_per_page"] == ["50"]
    assert parse_qs(urlsplit(urls[1]).query)["results_per_page"] == ["1"]
    assert result.raw_count == 51
    assert len(result.listings) == 50


def test_adzuna_failure_is_safe_and_successful_zero_result_is_distinct() -> None:
    zero = AdzunaJobSource(app_id="app-id", app_key="app-key", fetch_json=lambda _url: {"results": []})
    assert zero.search(BroadJobSearchQuery(keywords=["Engineer"])).raw_count == 0

    def fail(_url: str) -> dict:
        raise RuntimeError("ADZUNA_APP_KEY=do-not-surface")

    failing = AdzunaJobSource(app_id="app-id", app_key="app-key", fetch_json=fail)
    try:
        failing.search(BroadJobSearchQuery(keywords=["Engineer"]))
    except AdzunaJobSourceError as exc:
        assert "do-not-surface" not in str(exc)
    else:
        raise AssertionError("expected safe provider failure")


def test_adzuna_listing_deduplicates_against_equivalent_direct_ats_listing() -> None:
    broad = AdzunaJobSource._normalize(_adzuna_result(), "gb")
    assert broad is not None
    direct = JobListing(
        source="greenhouse",
        source_token="example",
        external_id="greenhouse-1",
        title="Applied AI Engineer",
        company="Example Labs",
        location="London",
        url="https://jobs.example.test/1",
    )
    listings, duplicate_count = JobDeduplicationService().deduplicate([direct, broad])
    assert listings == [direct]
    assert duplicate_count == 1


class _FakeBroadSource:
    name = "adzuna"

    def __init__(self, results: list[JobListing], raw_count: int | None = None) -> None:
        self.results = results
        self.raw_count = len(results) if raw_count is None else raw_count

    def search(self, _query: BroadJobSearchQuery) -> BroadJobSourceResult:
        return BroadJobSourceResult(listings=self.results, raw_count=self.raw_count)


def _listing(index: int, title: str = "Applied AI Engineer") -> JobListing:
    return JobListing(
        source="adzuna",
        source_token="gb",
        external_id=f"adzuna-{index}",
        title=title,
        company="Example Labs",
        location=None,
        url=f"https://jobs.example.test/{index}",
        description="Structured public job detail.",
    )


def test_broad_service_persists_only_screened_bounded_listings_and_repeats_unchanged(db_session) -> None:
    source = _FakeBroadSource([_listing(1), _listing(2), _listing(3, "Excluded Internship")])
    service = BroadJobDiscoveryService(
        session=db_session,
        source=source,
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    )
    query = BroadJobSearchQuery(
        keywords=["AI Engineer"],
        excluded_title_terms=["intern"],
        max_results=1,
    )

    first = service.discover(query)
    second = service.discover(query)

    assert len(db_session.scalars(select(DiscoveredJob)).all()) == 1
    assert first.raw_count == 3
    assert first.rejected_count == 1
    assert first.bounded_count == 1
    assert first.bounded_out_count == 1
    assert first.lifecycle_counts.new == 1
    assert second.lifecycle_counts.new == 0
    assert second.lifecycle_counts.unchanged == 1
    assert second.source_diagnostics[0].imported_count == 0
    assert second.source_diagnostics[0].unchanged_count == 1


def test_broad_query_omission_never_marks_existing_job_inactive(db_session) -> None:
    source = _FakeBroadSource([_listing(1)])
    service = BroadJobDiscoveryService(
        session=db_session,
        source=source,
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    )
    service.discover(BroadJobSearchQuery(keywords=["AI Engineer"]))
    source.results = []
    response = service.discover(BroadJobSearchQuery(keywords=["Different query"]))

    record = db_session.scalar(select(DiscoveredJob))
    assert record is not None
    assert record.state == DiscoveredJobState.NEW.value
    assert response.lifecycle_counts.inactive == 0


def test_cli_discover_broad_is_acquisition_only(monkeypatch, capsys) -> None:
    class _Client:
        def __init__(self, _base_url, _token):
            self.request = None

        def discover_broad_jobs(self, request):
            self.request = request
            return {
                "raw_count": 3,
                "normalized_count": 3,
                "rejected_count": 1,
                "deduplicated_count": 0,
                "bounded_count": 2,
                "lifecycle_counts": {"new": 2, "updated": 0, "unchanged": 0},
                "source_diagnostics": [{"provider": "adzuna", "succeeded": True, "raw_count": 3, "normalized_count": 3}],
            }

    clients = []

    def factory(base_url, token):
        client = _Client(base_url, token)
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "CareerTransApiClient", factory)
    assert cli.main([
        "--token", "token", "jobs", "discover-broad", "--keyword", "AI Engineer", "--location", "London", "--limit", "50"
    ]) == 0
    assert clients[0].request == {
        "keywords": ["AI Engineer"],
        "locations": ["London"],
        "country": "gb",
        "posted_within_days": None,
        "salary_min": None,
        "max_results": 50,
    }
    assert "Broad scan:" in capsys.readouterr().out

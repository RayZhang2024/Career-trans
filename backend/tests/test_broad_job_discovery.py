from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from app.providers.jobs.adzuna import AdzunaJobSource
from app.schemas.discovery import DiscoveredJobState, JobListing, JobSearchQuery
from app.services.broad_job_discovery_service import BroadJobDiscoveryService
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.job_discovery_service import JobDiscoveryService


def query(**overrides: object) -> JobSearchQuery:
    values: dict[str, object] = {
        "keywords": ["AI Engineer"],
        "locations": ["London"],
        "country": "gb",
        "max_results": 50,
    }
    values.update(overrides)
    return JobSearchQuery.model_validate(values)


def adzuna_result(identifier: str = "123", **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "id": identifier,
        "title": "AI Engineer",
        "redirect_url": f"https://jobs.example.test/{identifier}",
        "description": "Build reliable AI systems.",
        "created": "2026-08-23T10:00:00Z",
        "company": {"display_name": "Acme"},
        "location": {"display_name": "London, United Kingdom"},
        "contract_time": "full_time",
    }
    result.update(overrides)
    return result


def test_adzuna_maps_query_parameters_and_normalizes_results() -> None:
    requested_urls: list[str] = []

    def fetch(url: str) -> dict[str, object]:
        requested_urls.append(url)
        return {"results": [adzuna_result()]}

    source = AdzunaJobSource(app_id="public-id", app_key="public-key", fetch_json=fetch)
    results = source.search(
        query(
            salary_min=90_000,
            posted_within_days=14,
            employment_types=["full-time", "permanent"],
        )
    )

    parsed = urlsplit(requested_urls[0])
    params = parse_qs(parsed.query)
    assert parsed.path == "/v1/api/jobs/gb/search/1"
    assert params["what"] == ["AI Engineer"]
    assert params["where"] == ["London"]
    assert params["salary_min"] == ["90000"]
    assert params["max_days_old"] == ["14"]
    assert params["full_time"] == ["1"]
    assert params["permanent"] == ["1"]
    assert results == [
        JobListing(
            source="adzuna",
            source_token="gb",
            external_id="123",
            title="AI Engineer",
            company="Acme",
            location="London, United Kingdom",
            url="https://jobs.example.test/123",
            description="Build reliable AI systems.",
            posted_at=datetime(2026, 8, 23, 10, tzinfo=timezone.utc),
            employment_type="full_time",
        )
    ]


def test_adzuna_is_bounded_and_skips_malformed_listings() -> None:
    calls: list[str] = []
    first_page = [adzuna_result(str(number)) for number in range(50)]
    first_page.append({"id": "bad", "title": "Missing URL"})

    def fetch(url: str) -> dict[str, object]:
        calls.append(url)
        if len(calls) == 1:
            return {"results": first_page}
        return {"results": [adzuna_result("50")]}

    source = AdzunaJobSource(app_id="id", app_key="key", fetch_json=fetch)
    results = source.search(query(max_results=51))

    assert len(results) == 51
    assert len(calls) == 2
    assert urlsplit(calls[1]).path.endswith("/search/2")


def test_adzuna_zero_results_and_temporary_failure_remain_distinct() -> None:
    empty = AdzunaJobSource(app_id="id", app_key="key", fetch_json=lambda _: {"results": []})
    empty_result = JobDiscoveryService([empty]).discover(query())
    assert empty_result.provider_counts == {"adzuna": 0}
    assert empty_result.provider_errors == {}

    def unavailable(_: str) -> dict[str, object]:
        raise HTTPError("https://api.adzuna.com", 503, "Unavailable", {}, None)

    failed = AdzunaJobSource(app_id="id", app_key="key", fetch_json=unavailable)
    failed_result = JobDiscoveryService([failed]).discover(query())
    assert failed_result.provider_counts == {}
    assert failed_result.provider_errors == {"adzuna": "HTTPError: provider request failed"}


def test_broad_results_deduplicate_with_direct_ats_before_persistence(db_session) -> None:
    direct = JobListing(
        source="greenhouse",
        source_token="acme",
        external_id="direct-1",
        title="AI Engineer",
        company="Acme",
        location="London",
        url="https://jobs.example.test/role-1?source=direct",
        description="Direct ATS description.",
    )
    broad = JobListing(
        source="adzuna",
        source_token="gb",
        external_id="broad-1",
        title="AI Engineer",
        company="Acme",
        location="London",
        url="https://JOBS.example.test/role-1/#details",
        description="Broad description.",
    )

    class DirectSource:
        name = "greenhouse"
        source_keys = ["greenhouse:acme"]

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            return [direct]

    class BroadSource:
        name = "adzuna"
        source_keys = ["adzuna"]
        lifecycle_authoritative = False

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            return [broad]

    response = BroadJobDiscoveryService(
        discovery_service=JobDiscoveryService([DirectSource(), BroadSource()]),
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    ).search(query())

    assert response.discovery.deduplicated_count == 1
    assert response.lifecycle_counts.new == 1
    assert set(response.job_states) == {"external:greenhouse:acme:direct-1"}


def test_broad_source_absence_never_marks_prior_job_inactive(db_session) -> None:
    source = AdzunaJobSource(
        app_id="id",
        app_key="key",
        fetch_json=lambda _: {"results": [adzuna_result()]},
    )
    service = BroadJobDiscoveryService(
        discovery_service=JobDiscoveryService([source]),
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    )

    first = service.search(query())
    assert set(first.job_states.values()) == {DiscoveredJobState.NEW}

    source._fetch_json = lambda _: {"results": []}  # type: ignore[method-assign]
    second = service.search(query())
    assert second.job_states == {}
    assert second.lifecycle_counts.inactive == 0


def test_job_first_query_allows_no_companies() -> None:
    query_without_companies = JobSearchQuery(keywords=["AI Engineer"], country="gb")
    assert query_without_companies.companies == []

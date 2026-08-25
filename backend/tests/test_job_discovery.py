from datetime import datetime, timezone

from app.providers.jobs.greenhouse import GreenhouseJobSource
from app.providers.jobs.lever import LeverJobSource
from app.schemas.discovery import JobListing, JobSearchQuery
from app.services.job_deduplication_service import JobDeduplicationService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.job_screening_service import JobScreeningService


def listing(**overrides: object) -> JobListing:
    values: dict[str, object] = {
        "source": "test",
        "external_id": "1",
        "title": "AI Solutions Engineer",
        "company": "Acme",
        "location": "London, United Kingdom",
        "url": "https://jobs.example.com/roles/1",
        "description": "Build AI solutions with Python.",
    }
    values.update(overrides)
    return JobListing.model_validate(values)


def query(**overrides: object) -> JobSearchQuery:
    values: dict[str, object] = {
        "keywords": ["AI Solutions Engineer"],
        "locations": ["London"],
        "remote_ok": True,
    }
    values.update(overrides)
    return JobSearchQuery.model_validate(values)


def test_greenhouse_payload_is_normalized() -> None:
    source = GreenhouseJobSource(
        ["acme"],
        fetch_json=lambda _: {
            "jobs": [
                {
                    "id": 41,
                    "title": "AI Solutions Engineer",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/41",
                    "location": {"name": "London"},
                    "content": "Customer-facing AI implementation role.",
                    "updated_at": "2026-08-20T12:00:00Z",
                }
            ]
        },
    )

    result = source.search(query())

    assert len(result) == 1
    assert result[0].source == "greenhouse"
    assert result[0].external_id == "41"
    assert result[0].title == "AI Solutions Engineer"
    assert result[0].location == "London"
    assert result[0].url == "https://boards.greenhouse.io/acme/jobs/41"
    assert result[0].description == "Customer-facing AI implementation role."
    assert result[0].posted_at == datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


def test_lever_payload_is_normalized() -> None:
    source = LeverJobSource(
        ["acme"],
        fetch_json=lambda _: [
            {
                "id": "lever-1",
                "text": "AI Solutions Engineer",
                "hostedUrl": "https://jobs.lever.co/acme/lever-1",
                "descriptionPlain": "Build AI products with customers.",
                "createdAt": 1_787_040_000_000,
                "categories": {"location": "Remote - United Kingdom", "commitment": "Full-time"},
            }
        ],
    )

    result = source.search(query())

    assert result[0].source == "lever"
    assert result[0].external_id == "lever-1"
    assert result[0].work_arrangement == "remote"
    assert result[0].employment_type == "Full-time"
    assert result[0].posted_at == datetime(2026, 8, 18, 8, tzinfo=timezone.utc)


def test_deduplication_prefers_first_listing_and_canonical_urls() -> None:
    first = listing(url="https://jobs.example.com/roles/1?source=greenhouse")
    duplicate = listing(
        source="other",
        external_id="different",
        url="https://JOBS.example.com/roles/1/#details",
    )
    distinct = listing(external_id="2", title="Platform Engineer", url="https://jobs.example.com/roles/2")

    result, removed = JobDeduplicationService().deduplicate([first, duplicate, distinct])

    assert result == [first, distinct]
    assert removed == 1


def test_screening_treats_keywords_as_discovery_seeds_but_rejects_known_location_mismatches() -> None:
    service = JobScreeningService()
    relevant = listing()
    adjacent_title = listing(title="Forward Deployed Engineer", description="Deploy AI systems for customers.", url="https://jobs.example.com/roles/2")
    wrong_location = listing(location="Berlin, Germany", url="https://jobs.example.com/roles/3")
    remote = listing(location="Remote", work_arrangement="remote", url="https://jobs.example.com/roles/4")

    result = service.screen([relevant, adjacent_title, wrong_location, remote], query())

    assert result == [relevant, adjacent_title, remote]


def test_screening_supports_conservative_exclusions_and_employment_type() -> None:
    service = JobScreeningService()
    accepted = listing(employment_type="Full-time")
    excluded_company = listing(company="Excluded Labs", url="https://jobs.example.com/roles/2")
    excluded_title = listing(title="AI Engineering Intern", url="https://jobs.example.com/roles/3")
    wrong_employment = listing(employment_type="Contract", url="https://jobs.example.com/roles/4")

    result = service.screen(
        [accepted, excluded_company, excluded_title, wrong_employment],
        query(
            excluded_companies=["Excluded"],
            excluded_title_terms=["intern"],
            employment_types=["full-time"],
        ),
    )

    assert result == [accepted]


def test_employment_type_normalizes_full_time_separator_variants() -> None:
    service = JobScreeningService()

    assert service._matches_employment_type(listing(employment_type="Full time"), ["Full-time"])
    assert service._matches_employment_type(listing(employment_type="full-time"), ["full time"])


def test_employment_type_does_not_conflate_contract_and_permanent() -> None:
    assert not JobScreeningService()._matches_employment_type(
        listing(employment_type="Contract"),
        ["Permanent"],
    )


def test_unknown_location_and_employment_type_do_not_equal_explicit_incompatibility() -> None:
    service = JobScreeningService()
    unknown = listing(
        location=None,
        employment_type=None,
        url="https://jobs.example.com/roles/unknown",
    )

    assert service.screen(
        [unknown],
        query(locations=["London"], employment_types=["Permanent"]),
    ) == [unknown]


def test_discovery_combines_sources_and_tolerates_a_provider_failure() -> None:
    class WorkingSource:
        name = "working"

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            return [listing(), listing(url="https://jobs.example.com/roles/1?duplicate=yes")]

    class BrokenSource:
        name = "broken"

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            raise RuntimeError("temporary failure")

    result = JobDiscoveryService([WorkingSource(), BrokenSource()]).discover(query())

    assert result.provider_counts == {"working": 2}
    assert result.raw_count == 2
    assert result.deduplicated_count == 1
    assert len(result.listings) == 1
    assert result.listings[0].url == "https://jobs.example.com/roles/1"
    assert result.provider_errors == {"broken": "RuntimeError: provider request failed"}

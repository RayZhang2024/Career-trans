from io import BytesIO
from urllib.error import HTTPError

import pytest

from app.providers.jobs.ashby import AshbyJobSource
from app.providers.jobs.greenhouse import GreenhouseJobSource
from app.providers.jobs.lever import LeverJobSource
from app.providers.jobs.probes.ashby import AshbyJobSourceProbe
from app.providers.jobs.probes.greenhouse import GreenhouseJobSourceProbe
from app.providers.jobs.probes.lever import LeverJobSourceProbe
from app.providers.jobs.probes.recruitee import RecruiteeJobSourceProbe
from app.providers.jobs.probes.smartrecruiters import SmartRecruitersJobSourceProbe
from app.providers.jobs.probes.workable import WorkableJobSourceProbe
from app.providers.jobs.recruitee import RecruiteeJobSource
from app.providers.jobs.smartrecruiters import SmartRecruitersJobSource
from app.providers.jobs.workable import WorkableJobSource
from app.schemas.discovery import JobSearchQuery
from app.schemas.job_sources import CompanySourceDiscoveryRequest, CompanyTarget, ResolvedJobSource
from app.services.ats_resolver_service import AtsResolverService
from app.services.company_source_discovery_service import CompanySourceDiscoveryService
from app.services.job_source_factory import create_job_source


def _query() -> JobSearchQuery:
    return JobSearchQuery(keywords=["engineer"])


@pytest.mark.parametrize(
    ("probe", "expected_provider"),
    [
        (GreenhouseJobSourceProbe(fetch_json=lambda _: {"jobs": [{"id": 1}]}), "greenhouse"),
        (AshbyJobSourceProbe(fetch_json=lambda _: {"jobs": [{"id": "1"}]}), "ashby"),
        (LeverJobSourceProbe(fetch_json=lambda _: [{"id": "1"}]), "lever"),
    ],
)
def test_existing_provider_probes_keep_resolving(probe: object, expected_provider: str) -> None:
    result = probe.probe(CompanyTarget(name="Example"), "example")  # type: ignore[attr-defined]

    assert result is not None
    assert result.provider == expected_provider


@pytest.mark.parametrize(
    ("probe", "expected_provider"),
    [
        (
            SmartRecruitersJobSourceProbe(
                fetch_json=lambda _: {"content": [{"id": "1", "company": {"identifier": "example", "name": "Example"}}]}
            ),
            "smartrecruiters",
        ),
        (WorkableJobSourceProbe(fetch_json=lambda _: {"jobs": [{"id": "1", "url": "https://example.workable.com/jobs/1"}]}), "workable"),
        (RecruiteeJobSourceProbe(fetch_json=lambda _: {"offers": [{"id": "1", "careers_url": "https://example.recruitee.com/o/1"}]}), "recruitee"),
    ],
)
def test_new_provider_probes_resolve_fake_structured_responses(probe: object, expected_provider: str) -> None:
    result = probe.probe(CompanyTarget(name="Example"), "example")  # type: ignore[attr-defined]

    assert result is not None
    assert result.provider == expected_provider
    assert result.source_token == "example"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            SmartRecruitersJobSource(
                ["example"],
                company="Example",
                fetch_json=lambda _: {
                    "content": [
                        {
                            "id": "smart-1",
                            "name": "Platform Engineer",
                            "applyUrl": "https://jobs.example.test/smart-1",
                            "location": {"city": "London", "country": "United Kingdom", "remote": True},
                            "typeOfEmployment": {"label": "Permanent"},
                            "description": "Build reliable platforms.",
                            "releasedDate": "2026-08-01T12:00:00Z",
                        }
                    ]
                },
            ),
            ("smartrecruiters", "smart-1", "London, United Kingdom", "Permanent", "remote"),
        ),
        (
            WorkableJobSource(
                ["example"],
                company="Example",
                fetch_json=lambda _: {
                    "jobs": [
                        {
                            "id": "workable-1",
                            "title": "Platform Engineer",
                            "url": "https://jobs.example.test/workable-1",
                            "description": "Build reliable platforms.",
                            "location": {"location_str": "Remote - United Kingdom", "workplace_type": "remote"},
                            "employment_type": "Full-time",
                            "created_at": "2026-08-01T12:00:00Z",
                        }
                    ]
                },
            ),
            ("workable", "workable-1", "Remote - United Kingdom", "Full-time", "remote"),
        ),
        (
            RecruiteeJobSource(
                ["example"],
                company="Example",
                fetch_json=lambda _: {
                    "offers": [
                        {
                            "id": "recruitee-1",
                            "title": "Platform Engineer",
                            "careers_url": "https://jobs.example.test/recruitee-1",
                            "description": "Build reliable platforms.",
                            "location": "London",
                            "employment_type": "Full-time",
                            "published_at": "2026-08-01T12:00:00Z",
                        }
                    ]
                },
            ),
            ("recruitee", "recruitee-1", "London", "Full-time", None),
        ),
    ],
)
def test_new_sources_normalize_shared_job_contract(source: object, expected: tuple[str, str, str, str, str | None]) -> None:
    listing = source.search(_query())[0]  # type: ignore[attr-defined]

    assert (listing.source, listing.external_id, listing.location, listing.employment_type, listing.work_arrangement) == expected
    assert listing.company == "Example"
    assert listing.source_token == "example"
    assert listing.description == "Build reliable platforms."


def test_resolver_order_is_deterministic_for_a_single_new_provider_candidate() -> None:
    result = AtsResolverService(
        [
            SmartRecruitersJobSourceProbe(fetch_json=lambda _: {"content": [{"id": "1", "company": {"identifier": "example", "name": "Example"}}]}),
            WorkableJobSourceProbe(fetch_json=lambda _: {}),
        ]
    ).resolve([CompanyTarget(name="Example")])

    assert result.results[0].attempted_providers == ["smartrecruiters", "workable"]
    assert result.results[0].resolved is not None
    assert result.results[0].resolved.provider == "smartrecruiters"


def test_non_matching_smartrecruiters_response_falls_through_to_confirmed_workable() -> None:
    smartrecruiters = SmartRecruitersJobSourceProbe(
        fetch_json=lambda _: {
            "content": [
                {"id": "other-1", "company": {"identifier": "other-company", "name": "Other Company"}}
            ]
        }
    )
    workable = WorkableJobSourceProbe(
        fetch_json=lambda _: {"jobs": [{"id": "workable-1", "url": "https://and-digital.workable.com/jobs/1"}]}
    )

    result = AtsResolverService([smartrecruiters, workable]).resolve(
        [CompanyTarget(name="AND Digital", slug="and-digital")]
    )

    assert result.results[0].attempted_providers == ["smartrecruiters", "workable"]
    assert result.results[0].resolved is not None
    assert result.results[0].resolved.provider == "workable"


def test_multiple_confirmed_new_provider_candidates_are_explicitly_ambiguous() -> None:
    smartrecruiters = SmartRecruitersJobSourceProbe(
        fetch_json=lambda _: {
            "content": [
                {"id": "smart-1", "company": {"identifier": "and-digital", "name": "AND Digital"}}
            ]
        }
    )
    workable = WorkableJobSourceProbe(
        fetch_json=lambda _: {"jobs": [{"id": "workable-1", "url": "https://and-digital.workable.com/jobs/1"}]}
    )

    result = AtsResolverService([smartrecruiters, workable]).resolve(
        [CompanyTarget(name="AND Digital", slug="and-digital")]
    )

    resolution = result.results[0]
    assert resolution.resolved is None
    assert [source.provider for source in resolution.candidate_sources] == ["smartrecruiters", "workable"]
    assert resolution.error == "Multiple matching structured sources were found."


@pytest.mark.parametrize(
    "probe",
    [
        WorkableJobSourceProbe(fetch_json=lambda _: {"jobs": [{"id": "other-1", "url": "https://other.workable.com/jobs/1"}]}),
        RecruiteeJobSourceProbe(fetch_json=lambda _: {"offers": [{"id": "other-1", "careers_url": "https://other.recruitee.com/o/1"}]}),
    ],
)
def test_new_provider_probes_reject_non_matching_tenant_data(probe: object) -> None:
    result = probe.probe(CompanyTarget(name="AND Digital", slug="and-digital"), "and-digital")  # type: ignore[attr-defined]

    assert result is None


def test_provider_failure_isolated_and_network_failure_maps_to_temporary_status(db_session) -> None:
    def server_error(_: str) -> dict[str, object]:
        raise HTTPError("https://example.test", 503, "Unavailable", None, BytesIO())

    resolver = AtsResolverService([WorkableJobSourceProbe(fetch_json=server_error)])
    result = resolver.resolve([CompanyTarget(name="Broken"), CompanyTarget(name="Also Broken")])

    assert [item.error for item in result.results] == ["workable: request failed", "workable: request failed"]

    service = CompanySourceDiscoveryService(session=db_session, resolver=resolver)
    persisted = service.resolve_sources(CompanySourceDiscoveryRequest(companies=[CompanyTarget(name="Broken")]))
    assert persisted.results[0].status == "temporarily_failed"


@pytest.mark.parametrize(
    "probe",
    [
        SmartRecruitersJobSourceProbe(fetch_json=lambda _: {"content": []}),
        WorkableJobSourceProbe(fetch_json=lambda _: {"jobs": []}),
        RecruiteeJobSourceProbe(fetch_json=lambda _: {"offers": []}),
    ],
)
def test_valid_zero_job_provider_is_resolved_not_unsupported(probe: object) -> None:
    result = AtsResolverService([probe]).resolve([CompanyTarget(name="Example")])  # type: ignore[list-item]

    assert result.results[0].resolved is not None
    assert result.results[0].error is None


@pytest.mark.parametrize(
    "resolved",
    [
        ResolvedJobSource(company="Example", provider="smartrecruiters", source_token="example", careers_url="https://careers.smartrecruiters.com/example"),
        ResolvedJobSource(company="Example", provider="workable", source_token="example", careers_url="https://apply.workable.com/example"),
        ResolvedJobSource(company="Example", provider="recruitee", source_token="example", careers_url="https://example.recruitee.com"),
    ],
)
def test_factory_preserves_resolved_company_and_source_token(resolved: ResolvedJobSource) -> None:
    source = create_job_source(resolved)
    payloads = {
        "smartrecruiters": {"content": [{"id": "1", "name": "Engineer", "applyUrl": "https://example.test/1"}]},
        "workable": {"jobs": [{"id": "1", "title": "Engineer", "url": "https://example.test/1"}]},
        "recruitee": {"offers": [{"id": "1", "title": "Engineer", "careers_url": "https://example.test/1"}]},
    }
    setattr(source, "_fetch_json", lambda _: payloads[resolved.provider])

    listing = source.search(_query())[0]
    assert listing.company == "Example"
    assert listing.source_token == "example"

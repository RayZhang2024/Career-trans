import json
import socket
from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError, URLError

from pydantic import BaseModel, ValidationError

from sqlalchemy import select

from app.models.company_career_source import CompanyCareerSource
from app.models.discovered_job import DiscoveredJob
from app.schemas.discovery import DiscoveredJobState, JobListing
from app.schemas.structured_ats_discovery import StructuredAtsDiscoveryRequest, StructuredAtsFailureKind
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.structured_ats_failure_diagnostics import (
    StructuredAtsSourceConfigurationError,
    classify_structured_ats_failure,
)
from app.services.structured_ats_discovery_service import StructuredAtsDiscoveryService
import app.cli as cli


def _record(company: str, provider: str, token: str) -> CompanyCareerSource:
    now = datetime.now(timezone.utc)
    return CompanyCareerSource(
        company_name=company,
        canonical_company_key=company.casefold(),
        provider=provider,
        source_token=token,
        careers_url=f"https://careers.example.test/{token}",
        status="resolved",
        first_seen_at=now,
        last_checked_at=now,
        last_successful_resolution_at=now,
    )


class _FakeStateStore:
    def __init__(self) -> None:
        self.listings = []

    def persist(self, listings):
        self.listings = list(listings)
        return {
            SqlAlchemyDiscoveredJobStateStore.identity_key(listing): DiscoveredJobState.NEW
            for listing in listings
        }


def test_scans_multiple_registry_sources_preserves_normalized_provenance_and_isolates_failure(
    db_session,
    monkeypatch,
) -> None:
    db_session.add_all(
        [
            _record("Alpha", "greenhouse", "alpha"),
            _record("Beta", "recruitee", "beta"),
            _record("Broken", "lever", "broken"),
        ]
    )
    db_session.commit()

    class _Source:
        def __init__(self, listings):
            self._listings = listings

        def search(self, _query):
            if isinstance(self._listings, Exception):
                raise self._listings
            return self._listings

    def factory(resolved):
        if resolved.source_token == "broken":
            return _Source(RuntimeError("private upstream response"))
        return _Source(
            [
                JobListing(
                    source=resolved.provider,
                    source_token=resolved.source_token,
                    external_id=f"{resolved.source_token}-1",
                    title="Forward Deployed Engineer",
                    company=None,
                    location=None,
                    url=f"https://jobs.example.test/{resolved.source_token}/1",
                    description="Public structured vacancy.",
                )
            ]
        )

    monkeypatch.setattr("app.services.structured_ats_discovery_service.create_job_source", factory)
    state_store = _FakeStateStore()
    response = StructuredAtsDiscoveryService(
        session=db_session,
        state_store=state_store,
    ).discover(StructuredAtsDiscoveryRequest(max_sources=3, max_results=10))

    assert [item.company for item in response.listings] == ["Alpha", "Beta"]
    assert all(item.location is None for item in response.listings)
    assert all(item.title == "Forward Deployed Engineer" for item in response.listings)
    assert all(item.provenance and item.provenance.discovered_via == "structured_ats" for item in response.listings)
    assert {item.provenance.source_ref for item in response.listings if item.provenance} == {
        "https://careers.example.test/alpha",
        "https://careers.example.test/beta",
    }
    assert [item.succeeded for item in response.source_diagnostics] == [True, True, False]
    assert response.source_diagnostics[-1].failure_kind is StructuredAtsFailureKind.UNKNOWN
    assert "private upstream response" not in response.source_diagnostics[-1].model_dump_json()
    assert len(state_store.listings) == 2
    assert [item.imported_count for item in response.source_diagnostics[:2]] == [1, 1]


def test_repeated_scans_use_shared_state_store_identity_without_duplicate_records(db_session, monkeypatch) -> None:
    db_session.add(_record("Alpha", "greenhouse", "alpha"))
    db_session.commit()

    class _Source:
        def search(self, _query):
            return [
                JobListing(
                    source="greenhouse",
                    source_token="alpha",
                    external_id="external-1",
                    title="Unusual Opportunity Title",
                    company=None,
                    location=None,
                    url="https://jobs.example.test/alpha/1?tracking=ignored",
                    description="Structured job detail.",
                )
            ]

    monkeypatch.setattr(
        "app.services.structured_ats_discovery_service.create_job_source",
        lambda _resolved: _Source(),
    )
    service = StructuredAtsDiscoveryService(
        session=db_session,
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    )
    first = service.discover(StructuredAtsDiscoveryRequest(max_sources=1))
    second = service.discover(StructuredAtsDiscoveryRequest(max_sources=1))

    assert first.lifecycle_counts.new == 1
    assert second.lifecycle_counts.unchanged == 1
    assert first.source_diagnostics[0].imported_count == 1
    assert second.source_diagnostics[0].imported_count == 0
    assert second.source_diagnostics[0].unchanged_count == 1
    assert len(db_session.scalars(select(DiscoveredJob)).all()) == 1
    assert second.listings[0].company == "Alpha"
    assert second.listings[0].location is None


def test_explicit_exclusions_and_deterministic_source_bound_are_applied(db_session, monkeypatch) -> None:
    db_session.add_all([_record("Alpha", "greenhouse", "alpha"), _record("Beta", "lever", "beta")])
    db_session.commit()
    called = []

    class _Source:
        def __init__(self, token):
            self._token = token

        def search(self, _query):
            called.append(self._token)
            return [
                JobListing(
                    source="greenhouse",
                    source_token=self._token,
                    title="Excluded Internship" if self._token == "alpha" else "Engineer",
                    company=None,
                    location=None,
                    url=f"https://jobs.example.test/{self._token}",
                )
            ]

    monkeypatch.setattr(
        "app.services.structured_ats_discovery_service.create_job_source",
        lambda resolved: _Source(resolved.source_token),
    )
    response = StructuredAtsDiscoveryService(
        session=db_session,
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    ).discover(
        StructuredAtsDiscoveryRequest(max_sources=1, excluded_title_terms=["intern"])
    )

    assert called == ["alpha"]
    assert response.listings == []
    assert response.source_diagnostics[0].rejected_count == 1
    assert db_session.scalars(select(DiscoveredJob)).all() == []


def test_max_results_bounds_persistence_and_diagnostics(db_session, monkeypatch) -> None:
    db_session.add(_record("Alpha", "greenhouse", "alpha"))
    db_session.commit()

    class _Source:
        def search(self, _query):
            return [
                JobListing(
                    source="greenhouse",
                    source_token="alpha",
                    external_id=f"external-{index}",
                    title=f"Engineering role {index}",
                    company="Alpha",
                    location="London",
                    url=f"https://jobs.example.test/alpha/{index}",
                    description="Structured job detail.",
                )
                for index in range(3)
            ]

    monkeypatch.setattr(
        "app.services.structured_ats_discovery_service.create_job_source",
        lambda _resolved: _Source(),
    )
    service = StructuredAtsDiscoveryService(
        session=db_session,
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    )
    first = service.discover(StructuredAtsDiscoveryRequest(max_sources=1, max_results=1))
    second = service.discover(StructuredAtsDiscoveryRequest(max_sources=1, max_results=1))

    assert len(db_session.scalars(select(DiscoveredJob)).all()) == 1
    assert first.lifecycle_counts.new == 1
    assert first.source_diagnostics[0].imported_count == 1
    assert first.source_diagnostics[0].bounded_out_count == 2
    assert first.source_diagnostics[0].deduplicated_count == 0
    assert second.lifecycle_counts.new == 0
    assert second.lifecycle_counts.unchanged == 1
    assert second.source_diagnostics[0].imported_count == 0
    assert second.source_diagnostics[0].unchanged_count == 1
    assert second.source_diagnostics[0].bounded_out_count == 2

    assert first.lifecycle_jobs[0].state is DiscoveredJobState.NEW
    assert second.lifecycle_jobs[0].state is DiscoveredJobState.UNCHANGED


def test_capped_observation_never_marks_unpersisted_board_jobs_inactive(db_session, monkeypatch) -> None:
    db_session.add(_record("Alpha", "greenhouse", "alpha"))
    db_session.commit()

    class _Source:
        def search(self, _query):
            return [
                JobListing(
                    source="greenhouse",
                    source_token="alpha",
                    external_id=f"external-{index}",
                    title=f"Engineering role {index}",
                    company="Alpha",
                    location="London",
                    url=f"https://jobs.example.test/alpha/{index}",
                    description="Structured job detail.",
                )
                for index in range(2)
            ]

    monkeypatch.setattr(
        "app.services.structured_ats_discovery_service.create_job_source",
        lambda _resolved: _Source(),
    )
    service = StructuredAtsDiscoveryService(
        session=db_session,
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    )
    service.discover(StructuredAtsDiscoveryRequest(max_sources=1, max_results=2))
    response = service.discover(StructuredAtsDiscoveryRequest(max_sources=1, max_results=1))

    records = db_session.scalars(select(DiscoveredJob).order_by(DiscoveredJob.external_id)).all()
    assert len(records) == 2
    assert all(record.state is not DiscoveredJobState.INACTIVE for record in records)
    assert response.lifecycle_counts.inactive == 0


def test_filtered_observation_never_marks_previously_imported_job_inactive(db_session, monkeypatch) -> None:
    db_session.add(_record("Alpha", "greenhouse", "alpha"))
    db_session.commit()

    class _Source:
        def search(self, _query):
            return [
                JobListing(
                    source="greenhouse",
                    source_token="alpha",
                    external_id="external-1",
                    title="Engineering Internship",
                    company="Alpha",
                    location="London",
                    url="https://jobs.example.test/alpha/1",
                    description="Structured job detail.",
                )
            ]

    monkeypatch.setattr(
        "app.services.structured_ats_discovery_service.create_job_source",
        lambda _resolved: _Source(),
    )
    service = StructuredAtsDiscoveryService(
        session=db_session,
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    )
    service.discover(StructuredAtsDiscoveryRequest(max_sources=1))
    response = service.discover(
        StructuredAtsDiscoveryRequest(max_sources=1, excluded_title_terms=["intern"])
    )

    record = db_session.scalar(select(DiscoveredJob))
    assert record is not None
    assert record.state == DiscoveredJobState.NEW.value
    assert response.listings == []
    assert response.lifecycle_counts.inactive == 0


def test_cli_reports_per_source_ats_scan_diagnostics_without_ranking(monkeypatch, capsys) -> None:
    class _Client:
        def __init__(self, _base_url, _token):
            self.request = None

        def discover_known_ats_sources(self, request):
            self.request = request
            return {
                "listings": [{"title": "Engineer"}],
                "raw_count": 2,
                "rejected_count": 0,
                "deduplicated_count": 1,
                "source_diagnostics": [
                    {
                        "company": "Alpha",
                        "provider": "greenhouse",
                        "source_token": "alpha",
                        "succeeded": True,
                        "discovered_count": 2,
                        "imported_count": 1,
                        "unchanged_count": 0,
                        "updated_count": 0,
                        "deduplicated_count": 1,
                        "bounded_out_count": 0,
                        "rejected_count": 0,
                    },
                    {
                        "company": "Broken",
                        "provider": "lever",
                        "source_token": "broken",
                        "succeeded": False,
                        "discovered_count": 0,
                        "imported_count": 0,
                        "unchanged_count": 0,
                        "updated_count": 0,
                        "deduplicated_count": 0,
                        "bounded_out_count": 0,
                        "rejected_count": 0,
                        "failure_kind": "provider_failure",
                    },
                ],
            }

    clients = []

    def factory(base_url, token):
        client = _Client(base_url, token)
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "CareerTransApiClient", factory)
    assert cli.main(
        ["--token", "token", "jobs", "discover-ats", "--limit", "7", "--max-sources", "2"]
    ) == 0

    assert clients[0].request == {
        "max_results": 7,
        "max_sources": 2,
        "providers": [],
        "companies": [],
    }
    output = capsys.readouterr().out
    assert "ATS scan: 1 jobs" in output
    assert "OK | Alpha | greenhouse:alpha" in output
    assert "FAILED | Broken | lever:broken" in output
    assert "Failure kind: provider_failure" in output


class _Schema(BaseModel):
    count: int


def test_structured_ats_failure_kinds_are_bounded_and_do_not_expose_messages() -> None:
    parse_error: ValidationError
    try:
        _Schema.model_validate({"count": "not-a-number"})
    except ValidationError as exc:
        parse_error = exc
    else:  # pragma: no cover - protects the fixture itself
        raise AssertionError("Expected validation error")

    cases = [
        (URLError("private host secret-token"), StructuredAtsFailureKind.CONNECTION_FAILURE),
        (URLError(socket.timeout("private timeout detail")), StructuredAtsFailureKind.TIMEOUT),
        (HTTPError("https://provider.example.test/?token=secret", 503, "private body", None, BytesIO()), StructuredAtsFailureKind.HTTP_FAILURE),
        (json.JSONDecodeError("private parse content", "{", 1), StructuredAtsFailureKind.PARSE_FAILURE),
        (parse_error, StructuredAtsFailureKind.PARSE_FAILURE),
        (StructuredAtsSourceConfigurationError(), StructuredAtsFailureKind.CONFIGURATION_FAILURE),
        (ValueError("private adapter value error"), StructuredAtsFailureKind.PROVIDER_FAILURE),
        (KeyError("private provider schema"), StructuredAtsFailureKind.PROVIDER_FAILURE),
        (RuntimeError("Bearer secret-value and provider body"), StructuredAtsFailureKind.UNKNOWN),
    ]

    for exc, expected in cases:
        actual = classify_structured_ats_failure(exc)
        assert actual is expected
        assert "secret" not in actual.value
        assert "private" not in actual.value


def test_adapter_value_error_is_not_misclassified_as_configuration_failure(db_session, monkeypatch) -> None:
    db_session.add(_record("Alpha", "greenhouse", "alpha"))
    db_session.commit()

    class _Source:
        def search(self, _query):
            raise ValueError("private provider payload detail")

    monkeypatch.setattr(
        "app.services.structured_ats_discovery_service.create_job_source",
        lambda _resolved: _Source(),
    )
    response = StructuredAtsDiscoveryService(
        session=db_session,
        state_store=_FakeStateStore(),
    ).discover(StructuredAtsDiscoveryRequest(max_sources=1))

    diagnostic = response.source_diagnostics[0]
    assert diagnostic.succeeded is False
    assert diagnostic.failure_kind is StructuredAtsFailureKind.PROVIDER_FAILURE
    assert "private provider payload detail" not in diagnostic.model_dump_json()

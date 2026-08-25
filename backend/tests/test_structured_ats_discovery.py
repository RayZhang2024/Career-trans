from datetime import datetime, timezone

from sqlalchemy import select

from app.models.company_career_source import CompanyCareerSource
from app.models.discovered_job import DiscoveredJob
from app.schemas.discovery import DiscoveredJobState, JobListing
from app.schemas.structured_ats_discovery import StructuredAtsDiscoveryRequest
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
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
        self.successful_source_keys = set()

    def synchronize(self, listings, successful_source_keys):
        self.listings = list(listings)
        self.successful_source_keys = set(successful_source_keys)
        return {f"job-{index}": DiscoveredJobState.NEW for index, _ in enumerate(listings)}


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
    assert response.source_diagnostics[-1].failure == "RuntimeError: provider request failed"
    assert state_store.successful_source_keys == {"greenhouse:alpha", "recruitee:beta"}
    assert len(state_store.listings) == 2


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
    response = StructuredAtsDiscoveryService(session=db_session, state_store=_FakeStateStore()).discover(
        StructuredAtsDiscoveryRequest(max_sources=1, excluded_title_terms=["intern"])
    )

    assert called == ["alpha"]
    assert response.listings == []
    assert response.source_diagnostics[0].rejected_count == 1


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
                        "deduplicated_count": 1,
                        "rejected_count": 0,
                    },
                    {
                        "company": "Broken",
                        "provider": "lever",
                        "source_token": "broken",
                        "succeeded": False,
                        "discovered_count": 0,
                        "imported_count": 0,
                        "deduplicated_count": 0,
                        "rejected_count": 0,
                        "failure": "RuntimeError: provider request failed",
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
    assert "provider request failed" in output

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_career_source import CompanyCareerSource
from app.schemas.employer_universe import EmployerUniverseRequest
from app.schemas.job_sources import (
    AtsResolutionResponse,
    CompanySourceDiscoveryRequest,
    CompanySourceResolution,
    ResolvedJobSource,
)
from app.services.company_source_discovery_service import CompanySourceDiscoveryService
from app.services.employer_universe_service import EmployerUniverseService


NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


def registry_record(name: str, key: str) -> CompanyCareerSource:
    return CompanyCareerSource(
        company_name=name,
        canonical_company_key=key,
        provider="greenhouse",
        source_token=key.replace(" ", "-"),
        careers_url=f"https://jobs.example.test/{key}",
        status="resolved",
        first_seen_at=NOW,
        last_checked_at=NOW,
        last_successful_resolution_at=NOW,
    )


def test_watched_only_universe_preserves_stable_order(db_session: Session) -> None:
    result = EmployerUniverseService(db_session).build(
        EmployerUniverseRequest(
            watched_companies=[{"name": "First Co"}, {"name": "Second Co"}]
        )
    )

    assert [item.company.name for item in result.companies] == ["First Co", "Second Co"]
    assert [item.provenance[0].source for item in result.companies] == ["watched", "watched"]


def test_imported_only_universe_converts_entries_to_company_targets(db_session: Session) -> None:
    result = EmployerUniverseService(db_session).build(
        EmployerUniverseRequest(
            imported_entries=[
                {
                    "name": "Imported Co",
                    "website": "https://example.test",
                    "slug": "imported",
                    "priority": 2,
                    "source": "directory",
                    "source_ref": "row-7",
                    "tags": ["technology"],
                }
            ]
        )
    )

    company = result.companies[0]
    assert company.company.slug == "imported"
    assert company.company.website == "https://example.test"
    assert company.provenance[0].model_dump() == {"source": "imported:directory", "source_ref": "row-7"}


def test_registry_only_universe_reads_records_without_mutating_them(db_session: Session) -> None:
    record = registry_record("Registry Co", "registry co")
    db_session.add(record)
    db_session.commit()

    result = EmployerUniverseService(db_session).build(
        EmployerUniverseRequest(include_registry=True)
    )

    persisted = db_session.scalar(select(CompanyCareerSource))
    assert [item.company.name for item in result.companies] == ["Registry Co"]
    assert persisted is record
    assert persisted.last_checked_at.replace(tzinfo=timezone.utc) == NOW


def test_mixed_universe_deduplicates_with_watched_precedence_and_provenance(db_session: Session) -> None:
    db_session.add_all(
        [registry_record("First Co", "first co"), registry_record("Registry Co", "registry co")]
    )
    db_session.commit()

    result = EmployerUniverseService(db_session).build(
        EmployerUniverseRequest(
            watched_companies=[{"name": "First Co", "slug": "watched-first"}],
            imported_entries=[
                {"name": "first-co", "slug": "imported-first", "source": "list-a"},
                {"name": "Imported Co", "source": "list-a"},
            ],
            include_registry=True,
        )
    )

    assert [item.company.name for item in result.companies] == ["First Co", "Imported Co", "Registry Co"]
    first = result.companies[0]
    assert first.company.slug == "watched-first"
    assert [item.source for item in first.provenance] == ["watched", "imported:list-a", "registry"]
    assert result.deduplicated_count == 2


def test_exclusions_disabled_entries_and_cap_are_deterministic(db_session: Session) -> None:
    db_session.add(registry_record("Registry Co", "registry co"))
    db_session.commit()

    result = EmployerUniverseService(db_session).build(
        EmployerUniverseRequest(
            watched_companies=[{"name": "Excluded Co"}, {"name": "Watched Co"}],
            imported_entries=[
                {"name": "Disabled Co", "enabled": False, "source": "list"},
                {"name": "Imported Co", "source": "list"},
            ],
            include_registry=True,
            excluded_companies=["excluded-co"],
            max_companies=2,
        )
    )

    assert [item.company.name for item in result.companies] == ["Watched Co", "Imported Co"]
    assert result.disabled_count == 1
    assert result.excluded_count == 1
    assert result.output_count == 2
    assert db_session.scalar(select(CompanyCareerSource)).company_name == "Registry Co"


def test_universe_targets_feed_existing_company_source_discovery_contract(db_session: Session) -> None:
    universe = EmployerUniverseService(db_session).build(
        EmployerUniverseRequest(
            watched_companies=[{"name": "Example Co"}],
            imported_entries=[{"name": "Imported Co", "source": "list"}],
        )
    )

    class FakeResolver:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def resolve(self, companies: list[object]) -> AtsResolutionResponse:
            self.calls.append([company.name for company in companies])
            return AtsResolutionResponse(
                results=[
                    CompanySourceResolution(
                        company=company.name,
                        resolved=ResolvedJobSource(
                            company=company.name,
                            provider="greenhouse",
                            source_token=company.name.casefold().replace(" ", "-"),
                            careers_url="https://jobs.example.test",
                        ),
                    )
                    for company in companies
                ]
            )

    resolver = FakeResolver()
    source_service = CompanySourceDiscoveryService(
        session=db_session, resolver=resolver  # type: ignore[arg-type]
    )
    source_service.resolve_sources(
        CompanySourceDiscoveryRequest(
            companies=[item.company for item in universe.companies]
        )
    )

    assert resolver.calls == [["Example Co"], ["Imported Co"]]

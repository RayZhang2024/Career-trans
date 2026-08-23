from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_career_source import CompanyCareerSource
from app.schemas.job_sources import (
    AtsResolutionResponse,
    CompanySourceDiscoveryRequest,
    CompanySourceResolution,
    CompanySourceStatus,
    ResolvedJobSource,
)
from app.services.company_source_discovery_service import (
    CompanySourceDiscoveryService,
    _as_utc,
    canonical_company_key,
)


NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


class FakeResolver:
    def __init__(self, results: dict[str, CompanySourceResolution]) -> None:
        self._results = results
        self.calls: list[list[str]] = []

    def resolve(self, companies: list[object]) -> AtsResolutionResponse:
        names = [company.name for company in companies]
        self.calls.append(names)
        return AtsResolutionResponse(results=[self._results[name] for name in names])


def resolved(company: str, *, provider: str = "greenhouse", token: str = "example") -> CompanySourceResolution:
    return CompanySourceResolution(
        company=company,
        resolved=ResolvedJobSource(
            company=company,
            provider=provider,
            source_token=token,
            careers_url=f"https://jobs.example.test/{token}",
        ),
    )


def service(session: Session, resolver: FakeResolver, now: datetime = NOW) -> CompanySourceDiscoveryService:
    return CompanySourceDiscoveryService(session=session, resolver=resolver, now=lambda: now)  # type: ignore[arg-type]


def test_known_healthy_registry_source_is_reused_without_resolver_call(db_session: Session) -> None:
    db_session.add(
        CompanyCareerSource(
            company_name="Example Co",
            canonical_company_key="example co",
            provider="greenhouse",
            source_token="example",
            careers_url="https://jobs.example.test/example",
            status="resolved",
            first_seen_at=NOW - timedelta(days=2),
            last_checked_at=NOW - timedelta(days=1),
            last_successful_resolution_at=NOW - timedelta(days=1),
        )
    )
    db_session.commit()
    resolver = FakeResolver({})

    result = service(db_session, resolver).resolve_sources(
        CompanySourceDiscoveryRequest(companies=[{"name": "Example Co"}])
    )

    assert resolver.calls == []
    assert result.results[0].status is CompanySourceStatus.RESOLVED
    assert result.results[0].provenance == "registry_reuse"
    assert result.results[0].source_token == "example"


def test_unknown_company_resolves_persists_and_second_run_reuses_mapping(db_session: Session) -> None:
    resolver = FakeResolver({"Example Co": resolved("Example Co", token="example")})
    request = CompanySourceDiscoveryRequest(companies=[{"name": "Example Co"}])

    first = service(db_session, resolver).resolve_sources(request)
    second = service(db_session, resolver).resolve_sources(request)

    assert first.results[0].provenance == "new_resolution"
    assert first.results[0].status is CompanySourceStatus.RESOLVED
    assert second.results[0].provenance == "registry_reuse"
    assert resolver.calls == [["Example Co"]]
    record = db_session.scalar(select(CompanyCareerSource))
    assert record is not None
    assert _as_utc(record.last_successful_resolution_at) == NOW


def test_registry_reuse_does_not_postpone_successful_resolution_refresh(db_session: Session) -> None:
    resolver = FakeResolver({"Example Co": resolved("Example Co", token="example")})
    current_time = [NOW]
    registry = CompanySourceDiscoveryService(
        session=db_session,
        resolver=resolver,  # type: ignore[arg-type]
        now=lambda: current_time[0],
    )
    request = CompanySourceDiscoveryRequest(
        companies=[{"name": "Example Co"}], stale_after_days=30
    )

    registry.resolve_sources(request)
    current_time[0] = NOW + timedelta(days=10)
    reuse = registry.resolve_sources(request)
    current_time[0] = NOW + timedelta(days=31)
    refreshed = registry.resolve_sources(request)

    assert reuse.results[0].provenance == "registry_reuse"
    assert refreshed.results[0].provenance == "refreshed_resolution"
    assert resolver.calls == [["Example Co"], ["Example Co"]]


def test_stale_registry_mapping_is_refreshed(db_session: Session) -> None:
    db_session.add(
        CompanyCareerSource(
            company_name="Example Co",
            canonical_company_key="example co",
            provider="greenhouse",
            source_token="old-token",
            careers_url="https://jobs.example.test/old-token",
            status="resolved",
            first_seen_at=NOW - timedelta(days=40),
            last_checked_at=NOW - timedelta(days=31),
            last_successful_resolution_at=NOW - timedelta(days=31),
        )
    )
    db_session.commit()
    resolver = FakeResolver({"Example Co": resolved("Example Co", provider="lever", token="new-token")})

    result = service(db_session, resolver).resolve_sources(
        CompanySourceDiscoveryRequest(companies=[{"name": "Example Co"}], stale_after_days=30)
    )

    assert resolver.calls == [["Example Co"]]
    assert result.results[0].provenance == "refreshed_resolution"
    assert result.results[0].provider == "lever"
    assert result.results[0].source_token == "new-token"


def test_unresolved_and_temporary_failure_are_isolated_and_never_no_jobs(db_session: Session) -> None:
    resolver = FakeResolver(
        {
            "Unsupported Co": CompanySourceResolution(
                company="Unsupported Co",
                error="No supported source with published jobs was found.",
            ),
            "Temporary Co": CompanySourceResolution(
                company="Temporary Co", error="greenhouse: request failed"
            ),
            "Unresolved Co": CompanySourceResolution(
                company="Unresolved Co", error="ATS slug contains unsafe characters."
            ),
        }
    )

    result = service(db_session, resolver).resolve_sources(
        CompanySourceDiscoveryRequest(
            companies=[
                {"name": "Unsupported Co"},
                {"name": "Temporary Co"},
                {"name": "Unresolved Co"},
            ]
        )
    )

    assert [item.status for item in result.results] == [
        CompanySourceStatus.UNSUPPORTED,
        CompanySourceStatus.TEMPORARILY_FAILED,
        CompanySourceStatus.UNRESOLVED,
    ]
    assert all(item.provider is None for item in result.results)
    assert all("jobs" not in (item.diagnostic or "").casefold() for item in result.results[1:])


def test_ambiguous_sources_remain_distinct_from_unresolved_and_do_not_persist_a_mapping(db_session: Session) -> None:
    resolver = FakeResolver(
        {
            "Ambiguous Co": CompanySourceResolution(
                company="Ambiguous Co",
                candidate_sources=[
                    ResolvedJobSource(
                        company="Ambiguous Co",
                        provider="smartrecruiters",
                        source_token="ambiguous-co",
                        careers_url="https://careers.smartrecruiters.com/ambiguous-co",
                    ),
                    ResolvedJobSource(
                        company="Ambiguous Co",
                        provider="recruitee",
                        source_token="ambiguous-co",
                        careers_url="https://ambiguous-co.recruitee.com",
                    ),
                ],
                error="Multiple matching structured sources were found.",
            )
        }
    )

    result = service(db_session, resolver).resolve_sources(
        CompanySourceDiscoveryRequest(companies=[{"name": "Ambiguous Co"}])
    )

    assert result.results[0].status is CompanySourceStatus.AMBIGUOUS
    assert result.results[0].provider is None


def test_indeterminate_candidate_with_provider_failure_is_temporary_and_does_not_persist_mapping(db_session: Session) -> None:
    resolver = FakeResolver(
        {
            "Example Co": CompanySourceResolution(
                company="Example Co",
                candidate_sources=[
                    ResolvedJobSource(
                        company="Example Co",
                        provider="smartrecruiters",
                        source_token="example-co",
                        careers_url="https://careers.smartrecruiters.com/example-co",
                    )
                ],
                error="recruitee: request failed",
            )
        }
    )

    result = service(db_session, resolver).resolve_sources(
        CompanySourceDiscoveryRequest(companies=[{"name": "Example Co"}])
    )

    assert result.results[0].status is CompanySourceStatus.TEMPORARILY_FAILED
    assert result.results[0].provider is None


def test_watched_company_normalization_deduplicates_and_skips_disabled_targets(db_session: Session) -> None:
    resolver = FakeResolver({"Example Co": resolved("Example Co")})

    result = service(db_session, resolver).resolve_sources(
        CompanySourceDiscoveryRequest(
            companies=[
                {"name": " Example-Co ", "priority": 1},
                {"name": "example co", "priority": 2},
                {"name": "Disabled Co", "enabled": False},
            ]
        )
    )

    assert canonical_company_key("Example-Co") == "example co"
    assert len(result.results) == 1
    assert resolver.calls == [[" Example-Co "]]
    assert result.results[0].canonical_company_key == "example co"

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.discovered_job import DiscoveredJob
from app.schemas.candidate import CandidateContext
from app.schemas.discovery import JobListing, JobSearchQuery
from app.schemas.discovery_pipeline import DiscoverAndRankRequest
from app.schemas.job_ranking import JobRankingResponse
from app.schemas.job_sources import (
    AtsResolutionResponse,
    CompanySourceResolution,
    ResolvedJobSource,
)
from app.services.discover_and_rank_service import DiscoverAndRankService
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore


def listing(**overrides: object) -> JobListing:
    values: dict[str, object] = {
        "source": "greenhouse",
        "source_token": "acme",
        "external_id": "role-1",
        "title": "AI Engineer",
        "company": "Acme",
        "location": "London",
        "url": "https://jobs.example.test/acme/role-1",
        "description": "Build AI systems with Python.",
        "posted_at": datetime(2026, 8, 20, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return JobListing.model_validate(values)


def test_persistent_state_tracks_changes_and_requires_success_before_inactive(db_session: Session) -> None:
    store = SqlAlchemyDiscoveredJobStateStore(db_session)
    first = listing()

    first_states = store.synchronize([first], {"greenhouse:acme"})
    assert set(first_states.values()) == {"new"}
    assert set(store.synchronize([first], {"greenhouse:acme"}).values()) == {"unchanged"}

    changed = listing(description="Build reliable AI systems with Python.")
    assert set(store.synchronize([changed], {"greenhouse:acme"}).values()) == {"updated"}

    # A transient source failure supplies no successful source key, so absence cannot
    # be interpreted as an inactive posting.
    assert store.synchronize([], set()) == {}
    record = db_session.scalar(select(DiscoveredJob))
    assert record is not None
    assert record.state == "updated"

    assert set(store.synchronize([], {"greenhouse:acme"}).values()) == {"inactive"}
    assert record.state == "inactive"


def test_pipeline_uses_resolved_source_once_persists_raw_jobs_and_ranks_screened_jobs(
    monkeypatch,
) -> None:
    raw_jobs = [
        listing(),
        listing(
            external_id="role-2",
            title="Sales Executive",
            url="https://jobs.example.test/acme/role-2",
            description="Sell enterprise software.",
        ),
    ]

    class FakeResolver:
        def resolve(self, _: object) -> AtsResolutionResponse:
            return AtsResolutionResponse(
                results=[
                    CompanySourceResolution(
                        company="Acme",
                        resolved=ResolvedJobSource(
                            company="Acme",
                            provider="greenhouse",
                            source_token="acme",
                            careers_url="https://job-boards.greenhouse.io/acme",
                        ),
                    )
                ]
            )

    class FakeSource:
        name = "greenhouse"
        source_keys = ["greenhouse:acme"]
        calls = 0

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            self.calls += 1
            return raw_jobs

    class FakeStore:
        def __init__(self) -> None:
            self.observed: list[JobListing] = []
            self.successful_source_keys: set[str] = set()

        def synchronize(self, listings: list[JobListing], successful_source_keys: set[str]) -> dict[str, str]:
            self.observed = listings
            self.successful_source_keys = successful_source_keys
            return {"external:greenhouse:acme:role-1": "new"}

    class FakeRankingService:
        def __init__(self) -> None:
            self.jobs: list[JobListing] = []
            self.caps: tuple[int, int, float] | None = None

        def rank(self, request: object) -> JobRankingResponse:
            self.jobs = request.jobs
            self.caps = (
                request.max_semantic_candidates,
                request.max_full_analyses,
                request.min_relevance_score,
            )
            return JobRankingResponse(
                discovered_count=len(request.jobs),
                gated_out_count=0,
                relevance_screened_count=len(request.jobs),
                finalist_count=0,
                analysed_count=0,
            )

    source = FakeSource()
    store = FakeStore()
    ranking = FakeRankingService()
    monkeypatch.setattr(
        "app.services.discover_and_rank_service.create_job_source",
        lambda _: source,
    )
    service = DiscoverAndRankService(
        resolver=FakeResolver(), ranking_service=ranking, state_store=store
    )
    result = service.discover_and_rank(
        DiscoverAndRankRequest(
            companies=[{"name": "Acme"}],
            query={"keywords": ["AI"], "max_results": 50},
            candidate_context=CandidateContext(),
            max_semantic_candidates=6,
            max_full_analyses=3,
            min_relevance_score=0.5,
        )
    )

    assert source.calls == 1
    assert store.observed == raw_jobs
    assert store.successful_source_keys == {"greenhouse:acme"}
    assert ranking.jobs == [raw_jobs[0]]
    assert ranking.caps == (6, 3, 0.5)
    assert result.discovery.raw_count == 2
    assert result.discovery.screened_out_count == 1

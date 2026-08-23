from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.discovered_job import DiscoveredJob
from app.schemas.candidate import CandidateContext
from app.schemas.discovery import DiscoveredJobState, JobListing, JobSearchQuery
from app.schemas.discovery_pipeline import DiscoverAndRankRequest
from app.schemas.job_ranking import (
    JobRankingResponse,
    JobRelevanceAssessment,
)
from app.schemas.job_sources import (
    AtsResolutionResponse,
    CompanySourceResolution,
    ResolvedJobSource,
)
from app.services.discover_and_rank_service import DiscoverAndRankService
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.job_ranking_service import JobRankingService


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

        def synchronize(
            self,
            listings: list[JobListing],
            successful_source_keys: set[str],
        ) -> dict[str, DiscoveredJobState]:
            self.observed = listings
            self.successful_source_keys = successful_source_keys
            return {"external:greenhouse:acme:role-1": DiscoveredJobState.NEW}

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
    assert result.lifecycle_counts.new == 1


def test_pipeline_keeps_equivalent_authoritative_source_records_active(
    monkeypatch, db_session: Session
) -> None:
    greenhouse_job = listing(
        external_id="greenhouse-1",
        source="greenhouse",
        source_token="acme",
        url="https://jobs.example.test/role-1?source=greenhouse",
    )
    lever_job = listing(
        external_id="lever-1",
        source="lever",
        source_token="acme",
        url="https://JOBS.example.test/role-1/#details",
    )
    resolved_sources = [
        ResolvedJobSource(
            company="Acme", provider="greenhouse", source_token="greenhouse-acme",
            careers_url="https://job-boards.greenhouse.io/greenhouse-acme",
        ),
        ResolvedJobSource(
            company="Acme", provider="lever", source_token="lever-acme",
            careers_url="https://jobs.lever.co/lever-acme",
        ),
    ]

    class FakeResolver:
        def resolve(self, _: object) -> AtsResolutionResponse:
            return AtsResolutionResponse(
                results=[
                    CompanySourceResolution(company=item.company, resolved=item)
                    for item in resolved_sources
                ]
            )

    class GreenhouseSource:
        name = "greenhouse"
        source_keys = ["greenhouse:acme"]

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            return [greenhouse_job]

    class LeverSource:
        name = "lever"
        source_keys = ["lever:acme"]

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            return [lever_job]

    class FakeRanking:
        def rank(self, request: object) -> JobRankingResponse:
            return JobRankingResponse(
                discovered_count=len(request.jobs), gated_out_count=0,
                relevance_screened_count=0, finalist_count=0, analysed_count=0,
            )

    monkeypatch.setattr(
        "app.services.discover_and_rank_service.create_job_source",
        lambda item: GreenhouseSource() if item.provider == "greenhouse" else LeverSource(),
    )
    service = DiscoverAndRankService(
        resolver=FakeResolver(),
        ranking_service=FakeRanking(),
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
    )
    request = DiscoverAndRankRequest(
        companies=[{"name": "Acme"}],
        query={"keywords": ["AI"]},
        candidate_context=CandidateContext(),
    )
    assert service.discover_and_rank(request).lifecycle_counts.new == 2

    refreshed = service.discover_and_rank(request)

    assert len(refreshed.discovery.listings) == 1
    assert refreshed.discovery.deduplicated_count == 1
    assert refreshed.lifecycle_counts.unchanged == 2
    assert refreshed.lifecycle_counts.inactive == 0


@pytest.mark.parametrize(
    ("provider", "token"),
    [("greenhouse", "acme-gh"), ("ashby", "acme-ashby"), ("lever", "acme-lever")],
)
def test_pipeline_supports_each_resolved_provider_and_isolates_unresolved_targets(
    monkeypatch, provider: str, token: str
) -> None:
    resolved = ResolvedJobSource(
        company="Acme",
        provider=provider,
        source_token=token,
        careers_url=f"https://jobs.example.test/{token}",
    )

    class FakeResolver:
        def resolve(self, _: object) -> AtsResolutionResponse:
            return AtsResolutionResponse(
                results=[
                    CompanySourceResolution(company="Acme", resolved=resolved),
                    CompanySourceResolution(company="Unavailable", error="No source found."),
                ]
            )

    class FakeSource:
        name = provider
        source_keys = [f"{provider}:{token}"]

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            return [listing(source=provider, source_token=token, company="Acme")]

    class FakeStore:
        def synchronize(self, _: list[JobListing], __: set[str]) -> dict[str, DiscoveredJobState]:
            return {}

    class FakeRanking:
        def rank(self, request: object) -> JobRankingResponse:
            return JobRankingResponse(
                discovered_count=len(request.jobs), gated_out_count=0,
                relevance_screened_count=0, finalist_count=0, analysed_count=0,
            )

    monkeypatch.setattr(
        "app.services.discover_and_rank_service.create_job_source", lambda _: FakeSource()
    )
    result = DiscoverAndRankService(
        resolver=FakeResolver(), ranking_service=FakeRanking(), state_store=FakeStore()
    ).discover_and_rank(
        DiscoverAndRankRequest(
            companies=[{"name": "Acme"}, {"name": "Unavailable"}],
            query={"keywords": ["AI"]}, candidate_context=CandidateContext(),
        )
    )

    assert len(result.resolutions) == 2
    assert result.discovery.provider_counts == {provider: 1}
    assert [job.company for job in result.discovery.listings] == ["Acme"]


def test_pipeline_isolates_provider_failure_and_preserves_successful_source(monkeypatch) -> None:
    resolved = [
        ResolvedJobSource(company="Working", provider="greenhouse", source_token="working", careers_url="https://jobs.example.test/working"),
        ResolvedJobSource(company="Broken", provider="lever", source_token="broken", careers_url="https://jobs.example.test/broken"),
    ]

    class FakeResolver:
        def resolve(self, _: object) -> AtsResolutionResponse:
            return AtsResolutionResponse(results=[CompanySourceResolution(company=item.company, resolved=item) for item in resolved])

    class WorkingSource:
        name = "greenhouse"
        source_keys = ["greenhouse:working"]

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            return [listing(company="Working", source_token="working")]

    class BrokenSource:
        name = "lever"
        source_keys = ["lever:broken"]

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            raise RuntimeError("temporary")

    class FakeStore:
        def __init__(self) -> None:
            self.successes: set[str] = set()

        def synchronize(self, _: list[JobListing], successes: set[str]) -> dict[str, DiscoveredJobState]:
            self.successes = successes
            return {}

    class FakeRanking:
        def rank(self, request: object) -> JobRankingResponse:
            return JobRankingResponse(discovered_count=len(request.jobs), gated_out_count=0, relevance_screened_count=0, finalist_count=0, analysed_count=0)

    store = FakeStore()
    monkeypatch.setattr(
        "app.services.discover_and_rank_service.create_job_source",
        lambda item: WorkingSource() if item.source_token == "working" else BrokenSource(),
    )
    result = DiscoverAndRankService(
        resolver=FakeResolver(), ranking_service=FakeRanking(), state_store=store
    ).discover_and_rank(
        DiscoverAndRankRequest(
            companies=[{"name": "Working"}, {"name": "Broken"}],
            query={"keywords": ["AI"]}, candidate_context=CandidateContext(),
        )
    )

    assert [job.company for job in result.discovery.listings] == ["Working"]
    assert result.discovery.provider_errors == {"lever:broken": "RuntimeError: provider request failed"}
    assert store.successes == {"greenhouse:working"}


def test_pipeline_preserves_ranking_caps_and_company_fairness(monkeypatch) -> None:
    resolved = [
        ResolvedJobSource(company="First", provider="greenhouse", source_token="first", careers_url="https://jobs.example.test/first"),
        ResolvedJobSource(company="Later", provider="greenhouse", source_token="later", careers_url="https://jobs.example.test/later"),
    ]

    class FakeResolver:
        def resolve(self, _: object) -> AtsResolutionResponse:
            return AtsResolutionResponse(results=[CompanySourceResolution(company=item.company, resolved=item) for item in resolved])

    class FakeSource:
        name = "greenhouse"

        def __init__(self, token: str, jobs: list[JobListing]) -> None:
            self.source_keys = [f"greenhouse:{token}"]
            self._jobs = jobs

        def search(self, _: JobSearchQuery) -> list[JobListing]:
            return self._jobs

    first_jobs = [
        listing(company="First", source_token="first", external_id=f"first-{number}", title=f"AI First {number}", url=f"https://jobs.example.test/first/{number}")
        for number in range(3)
    ]
    later_jobs = [listing(company="Later", source_token="later", external_id="later-1", title="AI Later", url="https://jobs.example.test/later/1")]
    sources = {"first": FakeSource("first", first_jobs), "later": FakeSource("later", later_jobs)}

    class FakeStore:
        def synchronize(self, _: list[JobListing], __: set[str]) -> dict[str, DiscoveredJobState]:
            return {}

    assessed: list[str] = []

    class FakeRelevance:
        def assess(self, job: JobListing, _: CandidateContext) -> JobRelevanceAssessment:
            assessed.append(job.title)
            return JobRelevanceAssessment(relevant=False, score=0.0, reasoning="Not relevant.")

    class FakeArchetype:
        def classify(self, _: JobListing) -> object:
            raise AssertionError("Irrelevant jobs should not be classified.")

    class FakeGraph:
        def invoke(self, **_: object) -> object:
            raise AssertionError("No irrelevant job should receive full analysis.")

    monkeypatch.setattr(
        "app.services.discover_and_rank_service.create_job_source",
        lambda item: sources[item.source_token],
    )
    ranking = JobRankingService(
        relevance_agent=FakeRelevance(), archetype_agent=FakeArchetype(), career_analysis_graph=FakeGraph()  # type: ignore[arg-type]
    )
    result = DiscoverAndRankService(
        resolver=FakeResolver(), ranking_service=ranking, state_store=FakeStore()
    ).discover_and_rank(
        DiscoverAndRankRequest(
            companies=[{"name": "First"}, {"name": "Later"}],
            query={"keywords": ["AI"]}, candidate_context=CandidateContext(),
            max_semantic_candidates=2, max_full_analyses=1,
        )
    )

    assert assessed == ["AI First 0", "AI Later"]
    assert result.ranking.relevance_screened_count == 2
    assert result.ranking.finalist_count == 0
    assert result.ranking.analysed_count == 0

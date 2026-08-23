from app.api.deps import get_agentic_job_discovery_service
from app.main import app
from app.schemas.agentic_discovery import (
    AgenticDiscoveryRequest,
    ExtractedVacancy,
    PageContent,
    SearchResult,
    SearchStrategy,
)
from app.schemas.candidate import CandidateContext
from app.services.agentic_job_discovery_service import AgenticJobDiscoveryService
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.providers.web_search import BraveWebSearchProvider


class FakeStrategies:
    def __init__(self, strategies: list[SearchStrategy]) -> None:
        self.strategies = strategies
        self.received = None

    def generate(self, search_profile, career_profile, limit: int) -> list[SearchStrategy]:
        self.received = (search_profile, career_profile, limit)
        return self.strategies


class FakeSearch:
    def __init__(self, results: dict[str, list[SearchResult] | Exception]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int) -> list[SearchResult]:
        self.calls.append((query, limit))
        value = self.results[query]
        if isinstance(value, Exception):
            raise value
        return value


class FakePages:
    def __init__(self, pages: dict[str, PageContent | Exception]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def fetch(self, url: str) -> PageContent:
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value


class FakeExtractor:
    def __init__(self, values: dict[str, ExtractedVacancy | None | Exception]) -> None:
        self.values = values
        self.calls: list[str] = []

    def extract(self, page: PageContent) -> ExtractedVacancy | None:
        self.calls.append(page.final_url)
        value = self.values[page.final_url]
        if isinstance(value, Exception):
            raise value
        return value


def strategy(query: str, *, priority: int = 1, intent: str = "exact") -> SearchStrategy:
    return SearchStrategy(query=query, intent=intent, priority=priority, rationale="Explore this front.")


def result(title: str, url: str, *, rank: int = 1, snippet: str = "AI role in London") -> SearchResult:
    return SearchResult(title=title, snippet=snippet, url=url, domain=url.split("/")[2], rank=rank)


def page(url: str) -> PageContent:
    return PageContent(requested_url=url, final_url=url, html="<html><body>Vacancy</body></html>")


def request(**overrides: object) -> AgenticDiscoveryRequest:
    values: dict[str, object] = {
        "candidate_context": CandidateContext(
            profile_text="Experienced technical professional.",
            skills_text="Python, machine learning",
            career_strategy_text="Move into applied AI delivery.",
            job_search_criteria_text="UK or remote roles.",
        ),
            "query": {"keywords": ["AI Engineer"], "locations": ["London"]},
            "country": "gb",
    }
    values.update(overrides)
    return AgenticDiscoveryRequest.model_validate(values)


def service(db_session, *, strategies, results, pages, extracted) -> tuple[AgenticJobDiscoveryService, FakeStrategies, FakeSearch, FakePages, FakeExtractor]:
    strategy_fake = FakeStrategies(strategies)
    search_fake = FakeSearch(results)
    pages_fake = FakePages(pages)
    extractor_fake = FakeExtractor(extracted)
    return (
        AgenticJobDiscoveryService(
            strategy_generator=strategy_fake,
            search_provider=search_fake,
            page_fetcher=pages_fake,
            vacancy_extractor=extractor_fake,
            state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
        ),
        strategy_fake,
        search_fake,
        pages_fake,
        extractor_fake,
    )


def test_profile_generates_bounded_diverse_unique_strategies(db_session) -> None:
    strategies = [
        strategy("AI engineer London", priority=5, intent="exact"),
        strategy("AI engineer London", priority=4, intent="duplicate"),
        strategy("scientific software engineer UK", priority=3, intent="adjacent"),
        strategy("site:jobs.lever.co machine learning UK", priority=2, intent="ats"),
    ]
    discovery, generator, search, _, _ = service(
        db_session,
        strategies=strategies,
        results={item.query: [] for item in strategies},
        pages={}, extracted={},
    )

    response = discovery.discover(request(max_search_queries=2))

    assert [item.intent for item in response.strategies] == ["exact", "adjacent"]
    assert generator.received is not None
    assert generator.received[0].skills == ["Python", "machine learning"]
    assert len(search.calls) == 2
    assert response.diagnostics.search_strategies_generated == 2


def test_brave_provider_preserves_structured_result_provenance_without_exposing_key() -> None:
    captured: dict[str, object] = {}

    def fetch(url: str, headers: dict[str, str]) -> dict[str, object]:
        captured["url"] = url
        captured["headers"] = headers
        return {"web": {"results": [{"title": "AI Engineer", "description": "Role", "url": "https://jobs.example.test/jobs/1"}]}}

    results = BraveWebSearchProvider(api_key="test-secret", fetch_json=fetch).search("AI Engineer UK", 10)

    assert len(results) == 1
    assert results[0].domain == "jobs.example.test"
    assert "q=AI+Engineer+UK" in str(captured["url"])
    assert captured["headers"] == {"X-Subscription-Token": "test-secret", "Accept": "application/json"}


def test_search_failures_and_candidate_filtering_are_isolated_and_bounded(db_session) -> None:
    good_url = "https://jobs.example.test/jobs/1"
    duplicate = "https://jobs.example.test/jobs/1?tracking=1"
    rejected = "https://news.example.test/blog/ai-salary-guide"
    strategies = [strategy("first", priority=2), strategy("failed", priority=1)]
    discovery, _, _, pages, _ = service(
        db_session,
        strategies=strategies,
        results={
            "first": [result("AI Engineer", good_url), result("AI Engineer", duplicate, rank=2), result("Salary guide", rejected, rank=3)],
            "failed": RuntimeError("search unavailable"),
        },
        pages={good_url: page(good_url)},
        extracted={good_url: ExtractedVacancy(title="AI Engineer", company="Example", location="London")},
    )

    response = discovery.discover(request(max_pages_to_open=1))

    assert pages.calls == [good_url]
    assert response.diagnostics.search_results_raw == 3
    assert response.diagnostics.search_results_unique == 2
    assert response.diagnostics.deterministic_filtered_count == 1
    assert response.diagnostics.search_errors == {"failed": "RuntimeError: operation failed"}
    assert len(response.listings) == 1


def test_fetch_and_extraction_failures_do_not_stop_other_pages_or_invent_facts(db_session) -> None:
    failed_url = "https://jobs.example.test/jobs/fail"
    good_url = "https://jobs.example.test/jobs/good"
    discovery, _, _, _, _ = service(
        db_session,
        strategies=[strategy("roles")],
        results={"roles": [result("AI Engineer", failed_url), result("AI Engineer", good_url, rank=2)]},
        pages={failed_url: RuntimeError("timeout"), good_url: page(good_url)},
        extracted={good_url: ExtractedVacancy(title="AI Engineer", description="Build systems.")},
    )

    response = discovery.discover(request(max_pages_to_open=2))

    assert response.diagnostics.page_fetch_failures == 1
    assert response.diagnostics.extraction_successes == 1
    assert response.listings[0].company is None
    assert response.listings[0].location is None
    assert response.listings[0].employment_type is None


def test_duplicate_vacancies_are_deduplicated_and_web_omission_never_inactivates(db_session) -> None:
    first_url = "https://jobs.example.test/jobs/1"
    duplicate_url = "https://JOBS.example.test/jobs/1/#details"
    discovery, _, _, _, _ = service(
        db_session,
        strategies=[strategy("one"), strategy("two")],
        results={"one": [result("AI Engineer", first_url)], "two": [result("AI Engineer", duplicate_url)]},
        pages={first_url: page(first_url), "https://jobs.example.test/jobs/1": page("https://jobs.example.test/jobs/1")},
        extracted={first_url: ExtractedVacancy(title="AI Engineer", company="Example", location="London"), "https://jobs.example.test/jobs/1": ExtractedVacancy(title="AI Engineer", company="Example", location="London")},
    )

    first = discovery.discover(request())
    second = discovery.discover(request())

    assert len(first.listings) == 1
    assert first.lifecycle_counts.new == 1
    assert second.lifecycle_counts.unchanged == 1
    empty, _, _, _, _ = service(db_session, strategies=[strategy("empty")], results={"empty": []}, pages={}, extracted={})
    omitted = empty.discover(request())
    assert omitted.lifecycle_counts.inactive == 0


def test_api_uses_fake_bounded_service_without_candidate_specific_behavior(client, db_session) -> None:
    class FakeService:
        def discover(self, payload: AgenticDiscoveryRequest):
            assert payload.max_search_queries == 1
            return service(
                db_session,
                strategies=[strategy("generic")], results={"generic": []}, pages={}, extracted={},
            )[0].discover(payload)

    app.dependency_overrides[get_agentic_job_discovery_service] = FakeService
    try:
        response = client.post(
            "/api/v1/jobs/discover-agentic",
            json={
                "candidate_context": {"profile_text": "Generic profile"},
                "query": {"keywords": ["Engineer"]},
                "max_search_queries": 1,
            },
        )
    finally:
        app.dependency_overrides.pop(get_agentic_job_discovery_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["diagnostics"]["search_strategies_generated"] == 1
    assert "api_key" not in str(body).casefold()

import pytest
from fastapi import HTTPException

from app.api import deps
from app.api.deps import get_agentic_job_discovery_service, get_agentic_web_search_provider
from app.core.config import Settings
from app.providers.web_search import BraveWebSearchProvider, OpenAIWebSearchProvider


class FakeResponses:
    def __init__(self, response: object | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class FakeOpenAIClient:
    def __init__(self, response: object | Exception) -> None:
        self.responses = FakeResponses(response)


def output_response(*annotations: object) -> dict[str, object]:
    return {
        "output": [
            {"type": "web_search_call", "action": {"type": "search", "sources": [{"type": "url", "url": "https://source-only.example"}]}},
            {"type": "message", "content": [{"type": "output_text", "annotations": list(annotations)}]},
        ]
    }


def test_openai_web_search_maps_url_citations_to_search_results() -> None:
    client = FakeOpenAIClient(
        output_response(
            {"type": "url_citation", "title": "AI Engineer vacancy", "url": "https://jobs.example.com/roles/1"},
            {"type": "url_citation", "title": "Applied AI role", "url": "https://careers.example.org/jobs/2"},
        )
    )

    results = OpenAIWebSearchProvider(api_key="test-key", model="search-model", client=client).search("AI engineer UK", 5)

    assert [(item.title, item.url, item.domain, item.rank, item.snippet) for item in results] == [
        ("AI Engineer vacancy", "https://jobs.example.com/roles/1", "jobs.example.com", 1, ""),
        ("Applied AI role", "https://careers.example.org/jobs/2", "careers.example.org", 2, ""),
    ]
    assert client.responses.calls[0]["tools"] == [{"type": "web_search"}]
    assert client.responses.calls[0]["model"] == "search-model"


def test_openai_web_search_respects_limit_and_skips_unsupported_entries() -> None:
    client = FakeOpenAIClient(
        output_response(
            {"type": "url_citation", "title": "Missing URL"},
            {"type": "url_citation", "url": "https://missing-title.example/jobs/1"},
            {"type": "file_citation", "title": "Not web", "url": "https://ignored.example"},
            {"type": "url_citation", "title": "First", "url": "https://jobs.example.com/1"},
            {"type": "url_citation", "title": "Duplicate", "url": "https://jobs.example.com/1"},
            {"type": "url_citation", "title": "Second", "url": "https://jobs.example.com/2"},
        )
    )

    results = OpenAIWebSearchProvider(api_key="test-key", model="search-model", client=client).search("roles", 1)

    assert [(item.title, item.url, item.rank) for item in results] == [("First", "https://jobs.example.com/1", 1)]


def test_openai_web_search_provider_failure_propagates_for_existing_workflow_isolation() -> None:
    provider = OpenAIWebSearchProvider(
        api_key="test-key",
        model="search-model",
        client=FakeOpenAIClient(RuntimeError("unavailable")),
    )

    with pytest.raises(RuntimeError, match="unavailable"):
        provider.search("roles", 5)


def test_openai_search_provider_is_available_only_when_explicitly_selected() -> None:
    settings = Settings(
        openai_api_key="test-key",
        openai_web_search_model="search-model",
        openai_agentic_discovery_model="reasoning-model",
        agentic_search_provider="openai",
        brave_search_api_key=None,
    )

    provider = get_agentic_web_search_provider(settings)

    assert isinstance(provider, OpenAIWebSearchProvider)
    assert provider._model == "search-model"


def test_search_provider_defaults_to_disabled_without_constructing_openai(monkeypatch) -> None:
    constructed = []

    class UnexpectedOpenAIProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            constructed.append(True)

    monkeypatch.setattr("app.api.deps.OpenAIWebSearchProvider", UnexpectedOpenAIProvider)

    with pytest.raises(HTTPException, match="Agentic web discovery is disabled"):
        get_agentic_web_search_provider(Settings(openai_api_key="semantic-key"))

    assert constructed == []


def test_disabled_discovery_fails_before_semantic_components_are_constructed(monkeypatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(agentic_search_provider="disabled"))
    monkeypatch.setattr(
        deps,
        "get_semantic_response_client",
        lambda *_args, **_kwargs: pytest.fail("disabled discovery must not construct semantic clients"),
    )

    with pytest.raises(HTTPException, match="Agentic web discovery is disabled"):
        get_agentic_job_discovery_service(object())


def test_brave_remains_available_only_when_explicitly_selected() -> None:
    provider = get_agentic_web_search_provider(
        Settings(agentic_search_provider="brave", brave_search_api_key="test-brave-key")
    )

    assert isinstance(provider, BraveWebSearchProvider)


def test_missing_provider_credentials_produce_provider_specific_safe_errors() -> None:
    with pytest.raises(HTTPException, match="OPENAI_API_KEY"):
        get_agentic_web_search_provider(Settings(agentic_search_provider="openai", openai_api_key=None))
    with pytest.raises(HTTPException, match="BRAVE_SEARCH_API_KEY"):
        get_agentic_web_search_provider(Settings(agentic_search_provider="brave", brave_search_api_key=None))

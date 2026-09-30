import pytest

from app.providers.web_search import TavilyProviderError, TavilyWebSearchProvider


def test_tavily_posts_basic_search_and_normalizes_results() -> None:
    captured = {}

    def post_json(url, headers, payload, timeout):
        captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return {
            "answer": "must be ignored",
            "results": [
                {"title": "Software Engineer", "url": "https://jobs.example/role", "content": "Build services"},
                {"title": "Second", "url": "https://careers.example/role", "content": "Platform"},
                {"title": "Duplicate", "url": "https://jobs.example/role", "content": "duplicate"},
                {"title": "Malformed", "url": "not a URL"},
            ],
        }

    provider = TavilyWebSearchProvider(api_key="synthetic-tavily-key", post_json=post_json)
    results = provider.search("senior platform engineer", 3)

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"]["Authorization"] == "Bearer synthetic-tavily-key"
    assert captured["payload"] == {
        "query": "senior platform engineer",
        "search_depth": "basic",
        "max_results": 3,
        "include_answer": False,
        "include_raw_content": False,
    }
    assert 0 < captured["timeout"] <= 30
    assert [item.model_dump() for item in results] == [
        {
            "title": "Software Engineer",
            "snippet": "Build services",
            "url": "https://jobs.example/role",
            "domain": "jobs.example",
            "rank": 1,
        },
        {
            "title": "Second",
            "snippet": "Platform",
            "url": "https://careers.example/role",
            "domain": "careers.example",
            "rank": 2,
        },
    ]
    assert all(item.__class__.__name__ == "SearchResult" for item in results)


def test_tavily_bounds_limit_and_handles_empty_queries_without_requests() -> None:
    calls = []
    provider = TavilyWebSearchProvider(
        api_key="synthetic-key",
        post_json=lambda *args: calls.append(args) or {"results": []},
    )

    assert provider.search("  ", 4) == []
    assert provider.search("query", 0) == []
    assert calls == []
    provider.search("query", 500)
    assert calls[0][2]["max_results"] == 20


@pytest.mark.parametrize("response", [None, [], {}, {"results": {"bad": True}}])
def test_tavily_rejects_malformed_response_without_exposing_payload(response) -> None:
    provider = TavilyWebSearchProvider(api_key="secret-value", post_json=lambda *args: response)

    with pytest.raises(TavilyProviderError, match="invalid search response") as error:
        provider.search("query", 2)
    assert "secret-value" not in str(error.value)
    assert error.value.category == "invalid_response"


def test_tavily_wraps_network_errors_with_bounded_secret_safe_message() -> None:
    provider = TavilyWebSearchProvider(
        api_key="synthetic-secret",
        post_json=lambda *args: (_ for _ in ()).throw(TimeoutError("token=synthetic-secret")),
    )

    with pytest.raises(TavilyProviderError, match="temporarily unavailable") as error:
        provider.search("query", 2)
    assert error.value.category == "unavailable"
    assert "synthetic-secret" not in str(error.value)

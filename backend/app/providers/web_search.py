import json
from typing import Any, Callable, Protocol
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from app.schemas.agentic_discovery import SearchResult
from app.agents.openai_client import create_traced_openai_client
from openai import OpenAI


class WebSearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[SearchResult]: ...


class OpenAIWebSearchProvider:
    """OpenAI Responses built-in web search adapter with citation-only provenance."""

    def __init__(self, *, api_key: str, model: str, client: OpenAI | None = None) -> None:
        if not api_key and client is None:
            raise ValueError("An OpenAI API key is required for OpenAI web search.")
        self._client = client or create_traced_openai_client(
            api_key=api_key,
            trace_name="agentic_web_search",
        )
        self._model = model

    def search(self, query: str, limit: int) -> list[SearchResult]:
        response = self._client.responses.create(
            model=self._model,
            tools=[{"type": "web_search"}],
            input=(
                "Search the public web for current job vacancy pages relevant to this query. "
                "Return a concise cited result for each useful page; do not invent URLs.\n\n"
                f"QUERY: {query}"
            ),
        )
        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for citation in self._url_citations(response):
            title = self._text(self._value(citation, "title"))
            url = self._text(self._value(citation, "url"))
            if not title or not url or url in seen_urls:
                continue
            domain = (urlsplit(url).hostname or "").casefold()
            if not domain:
                continue
            seen_urls.add(url)
            results.append(
                SearchResult(
                    title=title,
                    snippet="",
                    url=url,
                    domain=domain,
                    rank=len(results) + 1,
                )
            )
            if len(results) == limit:
                break
        return results

    @classmethod
    def _url_citations(cls, response: object) -> list[object]:
        citations: list[object] = []
        for output in cls._value(response, "output", []) or []:
            if cls._value(output, "type") != "message":
                continue
            for content in cls._value(output, "content", []) or []:
                if cls._value(content, "type") != "output_text":
                    continue
                for annotation in cls._value(content, "annotations", []) or []:
                    if cls._value(annotation, "type") == "url_citation":
                        citations.append(annotation)
        return citations

    @staticmethod
    def _value(value: object, name: str, default: object = None) -> object:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    @staticmethod
    def _text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None


JsonFetcher = Callable[[str, dict[str, str]], dict[str, Any]]


class BraveWebSearchProvider:
    """Adapter for Brave's documented Web Search API; no result-page scraping."""

    _endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, *, api_key: str, fetch_json: JsonFetcher | None = None) -> None:
        self._api_key = api_key
        self._fetch_json = fetch_json or self._fetch_public_json

    def search(self, query: str, limit: int) -> list[SearchResult]:
        payload = self._fetch_json(
            f"{self._endpoint}?{urlencode({'q': query, 'count': limit, 'search_lang': 'en'})}",
            {"X-Subscription-Token": self._api_key, "Accept": "application/json"},
        )
        web = payload.get("web", {}) if isinstance(payload, dict) else {}
        raw_results = web.get("results", []) if isinstance(web, dict) else []
        results: list[SearchResult] = []
        for position, result in enumerate(raw_results if isinstance(raw_results, list) else [], start=1):
            if not isinstance(result, dict):
                continue
            title = self._text(result.get("title"))
            url = self._text(result.get("url"))
            if not title or not url:
                continue
            domain = (urlsplit(url).hostname or "").casefold()
            if not domain:
                continue
            results.append(
                SearchResult(
                    title=title,
                    snippet=self._text(result.get("description")) or "",
                    url=url,
                    domain=domain,
                    rank=position,
                )
            )
        return results[:limit]

    @staticmethod
    def _fetch_public_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed Brave API host
            payload = json.load(response)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

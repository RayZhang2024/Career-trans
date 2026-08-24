import json
from typing import Any, Callable, Protocol
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from app.schemas.agentic_discovery import SearchResult


class WebSearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[SearchResult]: ...


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

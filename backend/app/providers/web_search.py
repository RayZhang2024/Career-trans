import json
from urllib.error import HTTPError, URLError
from typing import Any, Callable, Protocol
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

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


class TavilyProviderError(RuntimeError):
    """Safe, bounded Tavily failure category; never contains provider response text."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


TavilyJsonPoster = Callable[[str, dict[str, str], dict[str, object], float], dict[str, Any]]


class TavilyWebSearchProvider:
    """Tavily Basic Search adapter normalized to the existing SearchResult contract."""

    _endpoint = "https://api.tavily.com/search"
    _timeout_seconds = 10.0

    def __init__(
        self,
        *,
        api_key: str,
        post_json: TavilyJsonPoster | None = None,
        timeout_seconds: float = _timeout_seconds,
    ) -> None:
        if not api_key:
            raise ValueError("A Tavily API key is required for Tavily web search.")
        self._api_key = api_key
        self._post_json = post_json or self._post_public_json
        self._timeout_seconds = max(0.1, min(float(timeout_seconds), 30.0))

    def search(self, query: str, limit: int) -> list[SearchResult]:
        bounded_limit = max(0, min(int(limit), 20))
        if not query.strip() or bounded_limit == 0:
            return []
        payload: dict[str, object] = {
            "query": query,
            "search_depth": "basic",
            "max_results": bounded_limit,
            "include_answer": False,
            "include_raw_content": False,
        }
        try:
            response = self._post_json(
                self._endpoint,
                {
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                payload,
                self._timeout_seconds,
            )
        except TavilyProviderError:
            raise
        except Exception as exc:
            raise TavilyProviderError(
                "Tavily search is temporarily unavailable.", category="unavailable"
            ) from exc
        raw_results = response.get("results") if isinstance(response, dict) else None
        if not isinstance(raw_results, list):
            raise TavilyProviderError(
                "Tavily returned an invalid search response.", category="invalid_response"
            )
        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for position, result in enumerate(raw_results, start=1):
            if not isinstance(result, dict):
                continue
            title = self._text(result.get("title"))
            url = self._text(result.get("url"))
            if not title or not url or url in seen_urls:
                continue
            parsed_url = urlsplit(url)
            domain = (parsed_url.hostname or "").casefold()
            if parsed_url.scheme not in {"http", "https"} or not domain:
                continue
            seen_urls.add(url)
            results.append(
                SearchResult(
                    title=title,
                    snippet=self._text(result.get("content")) or "",
                    url=url,
                    domain=domain,
                    rank=position,
                )
            )
            if len(results) >= bounded_limit:
                break
        return results

    @classmethod
    def _post_public_json(
        cls,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            opener = build_opener(_NoTavilyRedirectHandler())
            with opener.open(request, timeout=timeout) as response:  # noqa: S310 - fixed Tavily API host
                decoded = json.load(response)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise TavilyProviderError(
                    "Tavily rejected the configured credential.", category="authentication"
                ) from exc
            raise TavilyProviderError(
                "Tavily search is temporarily unavailable.", category="unavailable"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TavilyProviderError(
                "Tavily search is temporarily unavailable.", category="unavailable"
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TavilyProviderError(
                "Tavily returned an invalid search response.", category="invalid_response"
            ) from exc
        if not isinstance(decoded, dict):
            raise TavilyProviderError(
                "Tavily returned an invalid search response.", category="invalid_response"
            )
        return decoded

    @staticmethod
    def _text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None


class _NoTavilyRedirectHandler(HTTPRedirectHandler):
    """Do not forward the Authorization header across redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

"""Local Codex-backed implementation of the existing browser web-search contract."""

from __future__ import annotations

import json
import subprocess
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.providers.openai_structured_output import strict_schema_from_pydantic_model
from app.schemas.agentic_discovery import SearchResult
from app.services.codex_runtime import CodexRuntimeAdapter


class _CodexSearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2048)
    snippet: str | None = Field(max_length=2000)


class _CodexSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[_CodexSearchItem] = Field(max_length=20)


class LocalCodexSearchError(RuntimeError):
    """Bounded browser-safe failure from Local Codex search."""


class LocalCodexWebSearchProvider:
    """Return only factual web-search result fields; downstream Career-trans owns pages."""

    def __init__(
        self,
        *,
        runtime: CodexRuntimeAdapter,
        model: str,
        timeout_seconds: int = 45,
    ) -> None:
        self._runtime = runtime
        self._model = model
        self._timeout_seconds = max(1, min(int(timeout_seconds), 90))

    def search(self, query: str, limit: int) -> list[SearchResult]:
        bounded_limit = max(0, min(int(limit), 20))
        query = query.strip()
        if not query or bounded_limit == 0:
            return []
        prompt = (
            "Use live public web search to find current job vacancy pages relevant to this search query. "
            "Return only public result titles, absolute page URLs, and short factual snippets. "
            "Do not open pages, extract vacancy details, invent URLs, include reasoning, or return extra fields. "
            f"Return at most {bounded_limit} results. Search query: {query}"
        )
        try:
            invocation = self._runtime.invoke_structured(
                prompt=prompt,
                schema=strict_schema_from_pydantic_model(_CodexSearchOutput),
                model=self._model,
                schema_filename="local-codex-search-schema.json",
                output_filename="local-codex-search-results.json",
                timeout_seconds=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise LocalCodexSearchError("Local Codex search timed out. Refresh status and retry.") from exc
        except FileNotFoundError as exc:
            raise LocalCodexSearchError("Codex CLI is not installed on the Career-trans backend host.") from exc
        except OSError as exc:
            raise LocalCodexSearchError("Local Codex could not be started on the backend host.") from exc
        except Exception as exc:
            raise LocalCodexSearchError("Local Codex search could not be completed. Refresh status and retry.") from exc

        if invocation.returncode != 0:
            raise LocalCodexSearchError(
                "Local Codex search failed. Check backend-host sign-in, network access, model access, and quota."
            )
        if invocation.output_text is None:
            raise LocalCodexSearchError("Local Codex returned invalid structured search results.")
        try:
            output = _CodexSearchOutput.model_validate_json(invocation.output_text)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise LocalCodexSearchError("Local Codex returned invalid structured search results.") from exc

        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for item in output.results:
            url = item.url.strip()
            try:
                parsed = urlsplit(url)
                hostname = parsed.hostname
                _ = parsed.port
            except ValueError:
                raise LocalCodexSearchError("Local Codex returned invalid structured search results.")
            if (
                parsed.scheme.casefold() not in {"http", "https"}
                or not hostname
                or not parsed.netloc
                or any(character.isspace() for character in parsed.netloc)
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise LocalCodexSearchError("Local Codex returned invalid structured search results.")
            dedupe_key = parsed._replace(
                scheme=parsed.scheme.casefold(),
                netloc=parsed.netloc.casefold(),
                fragment="",
            ).geturl()
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            results.append(SearchResult(
                title=item.title,
                snippet=(item.snippet or "").strip(),
                url=url,
                domain=hostname.casefold(),
                rank=len(results) + 1,
            ))
            if len(results) >= bounded_limit:
                break
        return results

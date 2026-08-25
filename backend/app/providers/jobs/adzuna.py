"""Bounded adapter for Adzuna's documented public Jobs Search API."""

import json
from collections.abc import Callable
from datetime import datetime
from math import ceil
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.schemas.broad_job_discovery import BroadJobSearchQuery
from app.schemas.discovery import JobListing, JobProvenance

JsonFetcher = Callable[[str], dict[str, Any]]


class AdzunaJobSourceError(RuntimeError):
    """Safe provider error that never includes credential-bearing request URLs."""


class BroadJobSourceResult:
    def __init__(self, *, listings: list[JobListing], raw_count: int) -> None:
        self.listings = listings
        self.raw_count = raw_count


class AdzunaJobSource:
    """Search jobs by criteria without requiring a known employer or ATS token."""

    name = "adzuna"
    _MAX_PAGE_SIZE = 50

    def __init__(
        self,
        *,
        app_id: str,
        app_key: str,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        if not app_id.strip() or not app_key.strip():
            raise ValueError("Adzuna credentials must be configured.")
        self._app_id = app_id
        self._app_key = app_key
        self._fetch_json = fetch_json or self._fetch_public_json

    @classmethod
    def search_url(cls, query: BroadJobSearchQuery, page: int, results_per_page: int) -> str:
        params: dict[str, str | int] = {
            "app_id": "{app_id}",
            "app_key": "{app_key}",
            "what": " ".join(term.strip() for term in query.keywords if term.strip()),
            "results_per_page": results_per_page,
        }
        if query.locations:
            params["where"] = " ".join(location.strip() for location in query.locations if location.strip())
        if query.posted_within_days is not None:
            params["max_days_old"] = query.posted_within_days
        if query.salary_min is not None:
            params["salary_min"] = query.salary_min
        return (
            f"https://api.adzuna.com/v1/api/jobs/{query.country.casefold()}/search/{page}?"
            + urlencode(params)
        )

    def search(self, query: BroadJobSearchQuery) -> BroadJobSourceResult:
        listings: list[JobListing] = []
        raw_count = 0
        page_count = ceil(query.max_results / self._MAX_PAGE_SIZE)
        for page in range(1, page_count + 1):
            result_offset = (page - 1) * self._MAX_PAGE_SIZE
            results_per_page = min(self._MAX_PAGE_SIZE, query.max_results - result_offset)
            if results_per_page <= 0:
                break
            url = self.search_url(query, page, results_per_page)
            url = url.replace("%7Bapp_id%7D", self._app_id).replace("%7Bapp_key%7D", self._app_key)
            try:
                payload = self._fetch_json(url)
            except Exception as exc:
                raise AdzunaJobSourceError("Adzuna provider request failed.") from exc
            results = payload.get("results", []) if isinstance(payload, dict) else []
            if not isinstance(results, list):
                raise AdzunaJobSourceError("Adzuna provider returned an invalid response.")
            raw_count += len(results)
            for result in results:
                if not isinstance(result, dict):
                    continue
                listing = self._normalize(result, query.country)
                if listing is not None:
                    listings.append(listing)
            if len(results) < results_per_page:
                break
        return BroadJobSourceResult(listings=listings[: query.max_results], raw_count=raw_count)

    @classmethod
    def _normalize(cls, result: dict[str, Any], country: str) -> JobListing | None:
        title = cls._text(result.get("title"))
        url = cls._text(result.get("redirect_url"))
        if not title or not url:
            return None
        company = result.get("company")
        location = result.get("location")
        return JobListing(
            source=cls.name,
            source_token=country.casefold(),
            external_id=cls._text(result.get("id")),
            title=title,
            company=cls._display_name(company),
            location=cls._display_name(location),
            url=url,
            description=cls._text(result.get("description")),
            posted_at=cls._parse_datetime(result.get("created")),
            employment_type=cls._employment_type(result),
            provenance=JobProvenance(
                runtime="career-trans",
                source_ref="https://api.adzuna.com/v1/api/jobs",
                discovered_via="adzuna",
            ),
        )

    @staticmethod
    def _text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @classmethod
    def _display_name(cls, value: object) -> str | None:
        if not isinstance(value, dict):
            return None
        return cls._text(value.get("display_name"))

    @classmethod
    def _employment_type(cls, result: dict[str, Any]) -> str | None:
        labels = {
            "contract_type": {"permanent": "Permanent", "contract": "Contract"},
            "contract_time": {"full_time": "Full time", "part_time": "Part time"},
        }
        values = [labels[key].get(value) for key, mapping in labels.items() if isinstance((value := result.get(key)), str)]
        return ", ".join(value for value in values if value) or None

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _fetch_public_json(url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "Career-trans Job Discovery/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed documented API host
            payload = json.load(response)
        return payload if isinstance(payload, dict) else {}

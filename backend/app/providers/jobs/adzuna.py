from datetime import datetime
from math import ceil
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.schemas.discovery import JobListing, JobSearchQuery

JsonFetcher = Callable[[str], dict[str, Any]]


class AdzunaJobSource:
    """Bounded adapter for Adzuna's documented public Jobs Search API."""

    name = "adzuna"
    lifecycle_authoritative = False
    _results_per_page = 50

    def __init__(
        self,
        *,
        app_id: str,
        app_key: str,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_key = app_key
        self._fetch_json = fetch_json or self._fetch_public_json

    @property
    def source_keys(self) -> list[str]:
        return [self.name]

    def search(self, query: JobSearchQuery) -> list[JobListing]:
        listings: list[JobListing] = []
        results_per_page = min(query.max_results, self._results_per_page)
        max_pages = ceil(query.max_results / results_per_page)
        for page in range(1, max_pages + 1):
            payload = self._fetch_json(self.search_url(query, page, results_per_page))
            results = payload.get("results", []) if isinstance(payload, dict) else []
            if not isinstance(results, list):
                break
            for result in results:
                if not isinstance(result, dict):
                    continue
                listing = self._normalize(result, query.country.casefold())
                if listing is not None:
                    listings.append(listing)
                    if len(listings) >= query.max_results:
                        return listings
            if len(results) < results_per_page:
                break
        return listings

    def search_url(self, query: JobSearchQuery, page: int, results_per_page: int) -> str:
        country = query.country.casefold()
        parameters: dict[str, str | int] = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "what": " ".join(keyword.strip() for keyword in query.keywords if keyword.strip()),
            "results_per_page": results_per_page,
        }
        if query.locations:
            parameters["where"] = query.locations[0]
        if query.salary_min is not None:
            parameters["salary_min"] = query.salary_min
        if query.posted_within_days is not None:
            parameters["max_days_old"] = query.posted_within_days
        normalized_employment_types = {
            employment_type.casefold().replace("-", "_").replace(" ", "_")
            for employment_type in query.employment_types
        }
        for supported_type in ("full_time", "part_time", "permanent", "contract"):
            if supported_type in normalized_employment_types:
                parameters[supported_type] = 1
        return (
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}?"
            f"{urlencode(parameters)}"
        )

    @classmethod
    def _normalize(cls, result: dict[str, Any], country: str) -> JobListing | None:
        title = cls._text(result.get("title"))
        url = cls._text(result.get("redirect_url"))
        if not title or not url:
            return None
        company_data = result.get("company")
        location_data = result.get("location")
        return JobListing(
            source=cls.name,
            source_token=country,
            external_id=str(result["id"]) if result.get("id") is not None else None,
            title=title,
            company=cls._text(company_data.get("display_name")) if isinstance(company_data, dict) else None,
            location=cls._text(location_data.get("display_name")) if isinstance(location_data, dict) else None,
            url=url,
            description=cls._text(result.get("description")),
            posted_at=cls._parse_datetime(result.get("created")),
            employment_type=cls._employment_type(result),
        )

    @classmethod
    def _employment_type(cls, result: dict[str, Any]) -> str | None:
        return cls._text(result.get("contract_time")) or cls._text(result.get("contract_type"))

    @staticmethod
    def _fetch_public_json(url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "Career-trans Job Discovery/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed Adzuna API host
            import json

            payload = json.load(response)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _text(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

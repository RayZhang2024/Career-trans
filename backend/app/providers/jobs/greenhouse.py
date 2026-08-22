from datetime import datetime
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.schemas.discovery import JobListing, JobSearchQuery

JsonFetcher = Callable[[str], dict[str, Any]]


class GreenhouseJobSource:
    """Public Greenhouse board adapter, configured with board tokens."""

    name = "greenhouse"

    def __init__(
        self,
        board_tokens: list[str],
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self._board_tokens = board_tokens
        self._fetch_json = fetch_json or self._fetch_public_json

    def search(self, query: JobSearchQuery) -> list[JobListing]:
        listings: list[JobListing] = []
        for board_token in self._board_tokens:
            payload = self._fetch_json(
                f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
            )
            for job in payload.get("jobs", []):
                listing = self._normalize(job)
                if listing is not None:
                    listings.append(listing)
        return listings

    @classmethod
    def _normalize(cls, job: dict[str, Any]) -> JobListing | None:
        title = cls._text(job.get("title"))
        url = cls._text(job.get("absolute_url"))
        if not title or not url:
            return None

        location = job.get("location")
        return JobListing(
            source=cls.name,
            external_id=str(job["id"]) if job.get("id") is not None else None,
            title=title,
            location=cls._text(location.get("name")) if isinstance(location, dict) else None,
            url=url,
            description=cls._text(job.get("content")),
            posted_at=cls._parse_datetime(job.get("updated_at")),
        )

    @staticmethod
    def _fetch_public_json(url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "Career-trans Job Discovery/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed public ATS URL
            import json

            return json.load(response)

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

from datetime import datetime, timezone
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.schemas.discovery import JobListing, JobSearchQuery

JsonFetcher = Callable[[str], list[dict[str, Any]]]


class LeverJobSource:
    """Public Lever postings adapter, configured with site tokens."""

    name = "lever"

    def __init__(
        self,
        site_tokens: list[str],
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self._site_tokens = site_tokens
        self._fetch_json = fetch_json or self._fetch_public_json

    def search(self, query: JobSearchQuery) -> list[JobListing]:
        listings: list[JobListing] = []
        for site_token in self._site_tokens:
            payload = self._fetch_json(
                f"https://api.lever.co/v0/postings/{site_token}?mode=json"
            )
            for job in payload:
                listing = self._normalize(job)
                if listing is not None:
                    listings.append(listing)
        return listings

    @classmethod
    def _normalize(cls, job: dict[str, Any]) -> JobListing | None:
        title = cls._text(job.get("text"))
        url = cls._text(job.get("hostedUrl"))
        if not title or not url:
            return None

        categories = job.get("categories")
        categories = categories if isinstance(categories, dict) else {}
        location = cls._text(categories.get("location"))
        commitment = cls._text(categories.get("commitment"))
        return JobListing(
            source=cls.name,
            external_id=cls._text(job.get("id")),
            title=title,
            location=location,
            url=url,
            description=cls._text(job.get("descriptionPlain")),
            posted_at=cls._parse_timestamp(job.get("createdAt")),
            work_arrangement="remote" if location and "remote" in location.lower() else None,
            employment_type=commitment,
        )

    @staticmethod
    def _fetch_public_json(url: str) -> list[dict[str, Any]]:
        request = Request(url, headers={"User-Agent": "Career-trans Job Discovery/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed public ATS URL
            import json

            payload = json.load(response)
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _text(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not isinstance(value, int | float):
            return None
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)

from datetime import datetime
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.schemas.discovery import JobListing, JobSearchQuery

JsonFetcher = Callable[[str], dict[str, Any]]


class AshbyJobSource:
    """Public Ashby job-board adapter, configured with board tokens."""

    name = "ashby"

    def __init__(
        self,
        board_tokens: list[str],
        fetch_json: JsonFetcher | None = None,
        company: str | None = None,
    ) -> None:
        self._board_tokens = board_tokens
        self._fetch_json = fetch_json or self._fetch_public_json
        self._company = company

    @staticmethod
    def jobs_url(board_token: str) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{board_token}"

    @staticmethod
    def careers_url(board_token: str) -> str:
        return f"https://jobs.ashbyhq.com/{board_token}"

    @property
    def source_keys(self) -> list[str]:
        return [f"{self.name}:{token}" for token in self._board_tokens]

    def search(self, query: JobSearchQuery) -> list[JobListing]:
        listings: list[JobListing] = []
        for board_token in self._board_tokens:
            payload = self._fetch_json(self.jobs_url(board_token))
            for job in payload.get("jobs", []):
                listing = self._normalize(job, self._company, board_token)
                if listing is not None:
                    listings.append(listing)
        return listings

    @classmethod
    def _normalize(
        cls,
        job: dict[str, Any],
        company: str | None = None,
        source_token: str | None = None,
    ) -> JobListing | None:
        title = cls._text(job.get("title"))
        url = cls._text(job.get("jobUrl"))
        if not title or not url:
            return None
        location = job.get("location")
        location = location if isinstance(location, str) else None
        return JobListing(
            source=cls.name,
            source_token=source_token,
            external_id=cls._text(job.get("id")),
            title=title,
            company=company,
            location=cls._text(location),
            url=url,
            description=cls._text(job.get("descriptionHtml")),
            posted_at=cls._parse_datetime(job.get("publishedAt")),
            employment_type=cls._text(job.get("employmentType")),
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

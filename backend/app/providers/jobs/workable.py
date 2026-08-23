from datetime import datetime
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.schemas.discovery import JobListing, JobSearchQuery

JsonFetcher = Callable[[str], dict[str, Any]]


class WorkableJobSource:
    """Public Workable careers adapter, configured with account subdomains."""

    name = "workable"

    def __init__(
        self,
        account_subdomains: list[str],
        fetch_json: JsonFetcher | None = None,
        company: str | None = None,
    ) -> None:
        self._account_subdomains = account_subdomains
        self._fetch_json = fetch_json or self._fetch_public_json
        self._company = company

    @staticmethod
    def jobs_url(account_subdomain: str) -> str:
        return f"https://www.workable.com/api/accounts/{account_subdomain}?details=true"

    @staticmethod
    def careers_url(account_subdomain: str) -> str:
        return f"https://apply.workable.com/{account_subdomain}"

    @property
    def source_keys(self) -> list[str]:
        return [f"{self.name}:{account}" for account in self._account_subdomains]

    def search(self, query: JobSearchQuery) -> list[JobListing]:
        listings: list[JobListing] = []
        for account in self._account_subdomains:
            payload = self._fetch_json(self.jobs_url(account))
            jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                listing = self._normalize(job, self._company, account)
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
        url = cls._text(job.get("url")) or cls._text(job.get("shortlink"))
        if not title or not url:
            return None
        location = job.get("location")
        location = location if isinstance(location, dict) else {}
        return JobListing(
            source=cls.name,
            source_token=source_token,
            external_id=cls._text(job.get("id")) or cls._text(job.get("shortcode")),
            title=title,
            company=company,
            location=cls._text(location.get("location_str")),
            url=url,
            description=cls._text(job.get("description")) or cls._text(job.get("description_html")),
            posted_at=cls._parse_datetime(job.get("created_at")),
            work_arrangement=cls._text(location.get("workplace_type")),
            employment_type=cls._text(job.get("employment_type")),
        )

    @staticmethod
    def _fetch_public_json(url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "Career-trans Job Discovery/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed public ATS URL
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

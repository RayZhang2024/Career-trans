from datetime import datetime
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.schemas.discovery import JobListing, JobSearchQuery

JsonFetcher = Callable[[str], dict[str, Any]]


class SmartRecruitersJobSource:
    """Public SmartRecruiters Posting API adapter, configured with company IDs."""

    name = "smartrecruiters"

    def __init__(
        self,
        company_ids: list[str],
        fetch_json: JsonFetcher | None = None,
        company: str | None = None,
    ) -> None:
        self._company_ids = company_ids
        self._fetch_json = fetch_json or self._fetch_public_json
        self._company = company

    @staticmethod
    def jobs_url(company_id: str) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"

    @staticmethod
    def careers_url(company_id: str) -> str:
        return f"https://careers.smartrecruiters.com/{company_id}"

    @property
    def source_keys(self) -> list[str]:
        return [f"{self.name}:{company_id}" for company_id in self._company_ids]

    def search(self, query: JobSearchQuery) -> list[JobListing]:
        listings: list[JobListing] = []
        for company_id in self._company_ids:
            payload = self._fetch_json(self.jobs_url(company_id))
            postings = payload.get("content", []) if isinstance(payload, dict) else []
            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                listing = self._normalize(posting, self._company, company_id)
                if listing is not None:
                    listings.append(listing)
        return listings

    @classmethod
    def _normalize(
        cls,
        posting: dict[str, Any],
        company: str | None = None,
        source_token: str | None = None,
    ) -> JobListing | None:
        title = cls._text(posting.get("name"))
        url = cls._text(posting.get("applyUrl")) or cls._text(posting.get("ref"))
        if not title or not url:
            return None

        location_data = posting.get("location")
        location = cls._location(location_data)
        employment = posting.get("typeOfEmployment")
        employment_type = cls._text(employment.get("label")) if isinstance(employment, dict) else None
        return JobListing(
            source=cls.name,
            source_token=source_token,
            external_id=cls._text(posting.get("id")) or cls._text(posting.get("uuid")),
            title=title,
            company=company,
            location=location,
            url=url,
            description=cls._description(posting),
            posted_at=cls._parse_datetime(posting.get("releasedDate")),
            work_arrangement="remote" if isinstance(location_data, dict) and location_data.get("remote") is True else None,
            employment_type=employment_type,
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

    @classmethod
    def _location(cls, value: object) -> str | None:
        if not isinstance(value, dict):
            return None
        parts = [cls._text(value.get(name)) for name in ("city", "region", "country")]
        return ", ".join(part for part in parts if part) or None

    @classmethod
    def _description(cls, posting: dict[str, Any]) -> str | None:
        job_ad = posting.get("jobAd")
        if not isinstance(job_ad, dict):
            return cls._text(posting.get("description"))
        sections = job_ad.get("sections")
        if not isinstance(sections, list):
            return cls._text(job_ad.get("description"))
        text = [cls._text(section.get("text")) for section in sections if isinstance(section, dict)]
        return "\n".join(part for part in text if part) or None

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

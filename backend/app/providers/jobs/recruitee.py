from datetime import datetime
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.schemas.discovery import JobListing, JobSearchQuery

JsonFetcher = Callable[[str], dict[str, Any] | list[dict[str, Any]]]


class RecruiteeJobSource:
    """Public Recruitee careers-site adapter, configured with company tokens."""

    name = "recruitee"

    def __init__(
        self,
        company_tokens: list[str],
        fetch_json: JsonFetcher | None = None,
        company: str | None = None,
    ) -> None:
        self._company_tokens = company_tokens
        self._fetch_json = fetch_json or self._fetch_public_json
        self._company = company

    @staticmethod
    def jobs_url(company_token: str) -> str:
        return f"https://{company_token}.recruitee.com/api/offers"

    @staticmethod
    def careers_url(company_token: str) -> str:
        return f"https://{company_token}.recruitee.com"

    @property
    def source_keys(self) -> list[str]:
        return [f"{self.name}:{token}" for token in self._company_tokens]

    def search(self, query: JobSearchQuery) -> list[JobListing]:
        listings: list[JobListing] = []
        for token in self._company_tokens:
            payload = self._fetch_json(self.jobs_url(token))
            for offer in self._offers(payload):
                listing = self._normalize(offer, self._company, token)
                if listing is not None:
                    listings.append(listing)
        return listings

    @staticmethod
    def _offers(payload: object) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [offer for offer in payload if isinstance(offer, dict)]
        if isinstance(payload, dict) and isinstance(payload.get("offers"), list):
            return [offer for offer in payload["offers"] if isinstance(offer, dict)]
        return []

    @classmethod
    def _normalize(
        cls,
        offer: dict[str, Any],
        company: str | None = None,
        source_token: str | None = None,
    ) -> JobListing | None:
        title = cls._text(offer.get("title"))
        url = cls._text(offer.get("careers_url")) or cls._text(offer.get("url"))
        if not title or not url:
            return None
        location = offer.get("location")
        location_text = cls._text(location) if isinstance(location, str) else None
        if isinstance(location, dict):
            location_text = cls._text(location.get("name")) or cls._text(location.get("city"))
        return JobListing(
            source=cls.name,
            source_token=source_token,
            external_id=cls._text(offer.get("id")) or cls._text(offer.get("slug")),
            title=title,
            company=company,
            location=location_text,
            url=url,
            description=cls._text(offer.get("description")) or cls._text(offer.get("description_html")),
            posted_at=cls._parse_datetime(offer.get("published_at")) or cls._parse_datetime(offer.get("created_at")),
            employment_type=cls._text(offer.get("employment_type")),
        )

    @staticmethod
    def _fetch_public_json(url: str) -> dict[str, Any] | list[dict[str, Any]]:
        request = Request(url, headers={"User-Agent": "Career-trans Job Discovery/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed public ATS URL
            import json

            payload = json.load(response)
        return payload if isinstance(payload, dict | list) else {}

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

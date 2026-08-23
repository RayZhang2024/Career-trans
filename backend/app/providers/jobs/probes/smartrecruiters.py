from typing import Any, Callable
from urllib.error import HTTPError, URLError

from app.providers.jobs.probes.base import JobSourceProbeError, derive_slug, require_safe_slug
from app.providers.jobs.smartrecruiters import SmartRecruitersJobSource
from app.schemas.job_sources import CompanyTarget, ResolvedJobSource

JsonFetcher = Callable[[str], dict[str, Any]]


class SmartRecruitersJobSourceProbe:
    name = "smartrecruiters"

    def __init__(self, fetch_json: JsonFetcher | None = None) -> None:
        self._fetch_json = fetch_json or SmartRecruitersJobSource._fetch_public_json

    def probe(self, company: CompanyTarget, slug: str) -> ResolvedJobSource | None:
        require_safe_slug(slug)
        try:
            payload = self._fetch_json(SmartRecruitersJobSource.jobs_url(slug))
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise JobSourceProbeError from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise JobSourceProbeError from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
            return None
        postings = payload["content"]
        if postings and not self._has_matching_company(postings, company, slug):
            return None
        return ResolvedJobSource(company=company.name, provider=self.name, source_token=slug, careers_url=SmartRecruitersJobSource.careers_url(slug))

    @staticmethod
    def _has_matching_company(
        postings: list[object], company: CompanyTarget, slug: str
    ) -> bool:
        for posting in postings:
            if not isinstance(posting, dict):
                continue
            provider_company = posting.get("company")
            if not isinstance(provider_company, dict):
                continue
            if provider_company.get("identifier") != slug:
                continue
            name = provider_company.get("name")
            if not isinstance(name, str):
                return True
            try:
                if name == company.name or name.casefold() == company.name.casefold():
                    return True
                if derive_slug(name) == derive_slug(company.name):
                    return True
            except ValueError:
                continue
        return False

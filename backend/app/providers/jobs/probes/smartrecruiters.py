from typing import Any, Callable
from urllib.error import HTTPError, URLError

from app.providers.jobs.probes.base import JobSourceProbeError, require_safe_slug
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
        return ResolvedJobSource(company=company.name, provider=self.name, source_token=slug, careers_url=SmartRecruitersJobSource.careers_url(slug))

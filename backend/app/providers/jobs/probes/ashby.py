from typing import Any, Callable
from urllib.error import HTTPError, URLError

from app.providers.jobs.ashby import AshbyJobSource
from app.providers.jobs.probes.base import JobSourceProbeError, require_safe_slug
from app.schemas.job_sources import CompanyTarget, ResolvedJobSource

JsonFetcher = Callable[[str], dict[str, Any]]


class AshbyJobSourceProbe:
    name = "ashby"

    def __init__(self, fetch_json: JsonFetcher | None = None) -> None:
        self._fetch_json = fetch_json or AshbyJobSource._fetch_public_json

    def probe(self, company: CompanyTarget, slug: str) -> ResolvedJobSource | None:
        require_safe_slug(slug)
        try:
            payload = self._fetch_json(AshbyJobSource.jobs_url(slug))
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise JobSourceProbeError from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise JobSourceProbeError from exc

        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list) or not jobs:
            return None
        return ResolvedJobSource(
            company=company.name,
            provider=self.name,
            source_token=slug,
            careers_url=AshbyJobSource.careers_url(slug),
        )

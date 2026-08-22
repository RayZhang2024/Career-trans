from typing import Any, Callable
from urllib.error import HTTPError, URLError

from app.providers.jobs.lever import LeverJobSource
from app.providers.jobs.probes.base import JobSourceProbeError, require_safe_slug
from app.schemas.job_sources import CompanyTarget, ResolvedJobSource

JsonFetcher = Callable[[str], list[dict[str, Any]]]


class LeverJobSourceProbe:
    name = "lever"

    def __init__(self, fetch_json: JsonFetcher | None = None) -> None:
        self._fetch_json = fetch_json or LeverJobSource._fetch_public_json

    def probe(self, company: CompanyTarget, slug: str) -> ResolvedJobSource | None:
        require_safe_slug(slug)
        try:
            jobs = self._fetch_json(LeverJobSource.jobs_url(slug))
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise JobSourceProbeError from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise JobSourceProbeError from exc

        if not isinstance(jobs, list) or not jobs:
            return None
        return ResolvedJobSource(
            company=company.name,
            provider=self.name,
            source_token=slug,
            careers_url=LeverJobSource.careers_url(slug),
        )

from typing import Any, Callable
from urllib.error import HTTPError, URLError

from app.providers.jobs.probes.base import JobSourceProbeError, has_expected_host, require_safe_slug
from app.providers.jobs.recruitee import RecruiteeJobSource
from app.schemas.job_sources import CompanyTarget, ResolvedJobSource

JsonFetcher = Callable[[str], dict[str, Any] | list[dict[str, Any]]]


class RecruiteeJobSourceProbe:
    name = "recruitee"

    def __init__(self, fetch_json: JsonFetcher | None = None) -> None:
        self._fetch_json = fetch_json or RecruiteeJobSource._fetch_public_json

    def probe(self, company: CompanyTarget, slug: str) -> ResolvedJobSource | None:
        require_safe_slug(slug)
        try:
            payload = self._fetch_json(RecruiteeJobSource.jobs_url(slug))
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise JobSourceProbeError from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise JobSourceProbeError from exc
        if not isinstance(payload, list) and not (isinstance(payload, dict) and isinstance(payload.get("offers"), list)):
            return None
        offers = payload if isinstance(payload, list) else payload["offers"]
        if offers and not self._has_matching_tenant(offers, slug):
            return None
        return ResolvedJobSource(company=company.name, provider=self.name, source_token=slug, careers_url=RecruiteeJobSource.careers_url(slug))

    @staticmethod
    def _has_matching_tenant(offers: list[object], slug: str) -> bool:
        host = f"{slug}.recruitee.com"
        return any(
            isinstance(offer, dict)
            and has_expected_host(
                offer.get("careers_url") or offer.get("url"), host
            )
            for offer in offers
        )

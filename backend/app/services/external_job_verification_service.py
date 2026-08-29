"""Deterministic ATS verification for externally discovered public-job leads."""

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlsplit

from app.providers.jobs.ashby import AshbyJobSource
from app.providers.jobs.greenhouse import GreenhouseJobSource
from app.providers.jobs.lever import LeverJobSource
from app.providers.jobs.workday import WorkdayJobDetailExtractor
from app.providers.page_fetch import PageFetcher
from app.schemas.discovery import (
    JobDetailAuthority,
    JobListing,
    JobProvenance,
    JobVerificationStatus,
)
from app.services.job_deduplication_service import JobDeduplicationService


JsonFetcher = Callable[[str], object]


@dataclass(frozen=True)
class ExternalJobVerification:
    """A provider-verified actionable listing or a safely retained lead."""

    listing: JobListing
    actionable: bool
    reason: str | None = None


class ExternalJobVerificationService:
    """Resolve recognized public ATS URLs without web search or semantic calls."""

    MIN_DETAIL_CHARACTERS = 500
    _workday_host = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$", re.IGNORECASE)

    def __init__(
        self,
        *,
        fetch_json: JsonFetcher | None = None,
        page_fetcher: PageFetcher | None = None,
        workday_detail_extractor: WorkdayJobDetailExtractor | None = None,
    ) -> None:
        self._fetch_json = fetch_json or self._fetch_public_json
        self._page_fetcher = page_fetcher
        self._workday_detail_extractor = workday_detail_extractor

    def verify(self, lead: JobListing) -> ExternalJobVerification:
        parts = urlsplit(lead.url)
        host = (parts.hostname or "").casefold()
        path = [segment for segment in parts.path.split("/") if segment]
        try:
            if host == "job-boards.greenhouse.io":
                return self._greenhouse(lead, path)
            if host == "jobs.lever.co":
                return self._lever(lead, path)
            if host == "jobs.ashbyhq.com":
                return self._ashby(lead, path)
            if self._workday_host.fullmatch(host):
                return self._workday(lead, host)
        except Exception:
            return self._unverified(lead, "provider_detail_unavailable")
        return self._unverified(lead, "unsupported_provider_url")

    def _greenhouse(self, lead: JobListing, path: list[str]) -> ExternalJobVerification:
        if len(path) != 3 or path[1] != "jobs":
            return self._unverified(lead, "provider_identity_unresolved")
        board, job_id = path[0], path[2]
        payload = self._fetch_json(GreenhouseJobSource.jobs_url(board))
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        job = next((item for item in jobs if isinstance(item, dict) and str(item.get("id")) == job_id), None)
        if job is None:
            return self._unverified(lead, "provider_vacancy_not_current")
        listing = GreenhouseJobSource._normalize(job, lead.company, board)
        return self._verified(lead, listing)

    def _lever(self, lead: JobListing, path: list[str]) -> ExternalJobVerification:
        if len(path) != 2:
            return self._unverified(lead, "provider_identity_unresolved")
        site, job_id = path
        payload = self._fetch_json(LeverJobSource.jobs_url(site))
        jobs = payload if isinstance(payload, list) else []
        job = next((item for item in jobs if isinstance(item, dict) and str(item.get("id")) == job_id), None)
        if job is None:
            return self._unverified(lead, "provider_vacancy_not_current")
        listing = LeverJobSource._normalize(job, lead.company, site)
        return self._verified(lead, listing)

    def _ashby(self, lead: JobListing, path: list[str]) -> ExternalJobVerification:
        if len(path) != 2:
            return self._unverified(lead, "provider_identity_unresolved")
        board, job_id = path
        payload = self._fetch_json(AshbyJobSource.jobs_url(board))
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        job = next((item for item in jobs if isinstance(item, dict) and item.get("id") == job_id), None)
        if job is None:
            return self._unverified(lead, "provider_vacancy_not_current")
        listing = AshbyJobSource._normalize(job, lead.company, board)
        return self._verified(lead, listing)

    def _workday(self, lead: JobListing, host: str) -> ExternalJobVerification:
        if self._page_fetcher is None or self._workday_detail_extractor is None:
            return self._unverified(lead, "provider_detail_unavailable")
        try:
            page = self._page_fetcher.fetch(lead.url)
            vacancy = self._workday_detail_extractor.extract(page)
        except Exception:
            return self._unverified(lead, "provider_detail_unavailable")
        if vacancy is None:
            return self._unverified(lead, "provider_detail_unavailable")
        external_id = self._workday_external_id(page.final_url)
        if external_id is None:
            return self._unverified(lead, "provider_identity_unresolved")
        tenant = self._workday_host.fullmatch(host)
        listing = JobListing(
            source="workday",
            source_token=tenant.group("tenant").casefold() if tenant else None,
            external_id=external_id,
            title=vacancy.title or lead.title,
            company=vacancy.company or lead.company,
            location=vacancy.location or lead.location,
            url=JobDeduplicationService._canonical_url(page.final_url),
            description=vacancy.description,
            posted_at=vacancy.posted_at or lead.posted_at,
            employment_type=vacancy.employment_type or lead.employment_type,
            work_arrangement=vacancy.work_arrangement or lead.work_arrangement,
        )
        return self._verified(lead, listing)

    def _verified(self, lead: JobListing, listing: JobListing | None) -> ExternalJobVerification:
        if listing is None or not self._has_usable_provider_detail(listing.description):
            return self._unverified(lead, "provider_detail_unavailable")
        return ExternalJobVerification(
            listing=listing.model_copy(
                update={
                    "url": JobDeduplicationService._canonical_url(listing.url),
                    "provenance": lead.provenance,
                    "detail_authority": JobDetailAuthority.VERIFIED_EMPLOYER_DETAIL,
                    "verification_status": JobVerificationStatus.VERIFIED,
                    "verification_reason": None,
                }
            ),
            actionable=True,
        )

    def _unverified(self, lead: JobListing, reason: str) -> ExternalJobVerification:
        return ExternalJobVerification(
            listing=lead.model_copy(
                update={
                    "detail_authority": JobDetailAuthority.EXTERNAL_SUMMARY,
                    "verification_status": JobVerificationStatus.UNVERIFIED,
                    "verification_reason": reason,
                }
            ),
            actionable=False,
            reason=reason,
        )

    @classmethod
    def _has_usable_provider_detail(cls, detail: str | None) -> bool:
        """Require current provider identity plus substantive employer-supplied text."""
        if not detail:
            return False
        text = " ".join(detail.split())
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+#./-]*", text)
        return len(text) >= cls.MIN_DETAIL_CHARACTERS and len(set(word.casefold() for word in words)) >= 8

    @staticmethod
    def _workday_external_id(url: str) -> str | None:
        segment = (urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] or "")
        if "_" not in segment:
            return None
        value = segment.rsplit("_", 1)[-1].strip()
        return value or None

    @staticmethod
    def _fetch_public_json(url: str) -> object:
        if "greenhouse" in url:
            return GreenhouseJobSource._fetch_public_json(url)
        if "lever" in url:
            return LeverJobSource._fetch_public_json(url)
        return AshbyJobSource._fetch_public_json(url)

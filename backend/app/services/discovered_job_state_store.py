from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.discovered_job import DiscoveredJob
from app.models.discovered_job_provenance import DiscoveredJobProvenance
from app.schemas.discovery import (
    DiscoveredJobState,
    JobDetailAuthority,
    JobListing,
    JobProvenance,
    JobVerificationStatus,
)
from app.services.job_deduplication_service import JobDeduplicationService


class DiscoveredJobStateStore(Protocol):
    def persist(self, listings: list[JobListing]) -> dict[str, DiscoveredJobState]: ...

    def synchronize(
        self,
        listings: list[JobListing],
        successful_source_keys: set[str],
    ) -> dict[str, DiscoveredJobState]: ...


class SqlAlchemyDiscoveredJobStateStore:
    """Persist public listing lifecycle without treating failed source reads as absence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def synchronize(
        self,
        listings: list[JobListing],
        successful_source_keys: set[str],
    ) -> dict[str, DiscoveredJobState]:
        now = datetime.now(timezone.utc)
        observed_keys, transitions = self._persist_listings(listings, now)

        # A source must complete successfully before an unseen record can be inactive.
        active_records = self._session.scalars(
            select(DiscoveredJob).where(DiscoveredJob.state != DiscoveredJobState.INACTIVE)
        ).all()
        for record in active_records:
            if self.source_key(record.source, record.source_token) not in successful_source_keys:
                continue
            if record.identity_key in observed_keys:
                continue
            record.state = DiscoveredJobState.INACTIVE
            record.last_changed_at = now
            transitions[record.identity_key] = DiscoveredJobState.INACTIVE

        self._session.commit()
        return transitions

    def persist(self, listings: list[JobListing]) -> dict[str, DiscoveredJobState]:
        """Persist bounded accepted listings without deriving source absence/inactivity."""
        _, transitions = self._persist_listings(listings, datetime.now(timezone.utc))
        self._session.commit()
        return transitions

    def _persist_listings(
        self,
        listings: list[JobListing],
        now: datetime,
    ) -> tuple[set[str], dict[str, DiscoveredJobState]]:
        observed_keys: set[str] = set()
        transitions: dict[str, DiscoveredJobState] = {}

        for listing in listings:
            identity_key = self.identity_key(listing)
            if identity_key in observed_keys:
                continue
            observed_keys.add(identity_key)
            record = self._session.scalar(
                select(DiscoveredJob).where(DiscoveredJob.identity_key == identity_key)
            )
            # External leads historically used canonical URL identity. A later
            # provider verification may add a stronger external ID for the same
            # canonical vacancy; promote that record instead of duplicating it.
            if record is None:
                record = self._session.scalar(
                    select(DiscoveredJob).where(DiscoveredJob.url == listing.url)
                )
            effective_listing = listing
            if record is None:
                content_hash = self.content_hash(effective_listing)
                record = DiscoveredJob(
                    identity_key=identity_key,
                    source=effective_listing.source,
                    source_token=effective_listing.source_token,
                    company=effective_listing.company,
                    external_id=effective_listing.external_id,
                    title=effective_listing.title,
                    location=effective_listing.location,
                    url=effective_listing.url,
                    description=effective_listing.description,
                    posted_at=effective_listing.posted_at,
                    work_arrangement=effective_listing.work_arrangement,
                    employment_type=effective_listing.employment_type,
                    detail_authority=effective_listing.detail_authority,
                    verification_status=effective_listing.verification_status,
                    verification_reason=effective_listing.verification_reason,
                    content_hash=content_hash,
                    state=DiscoveredJobState.NEW,
                    last_seen_at=now,
                    last_changed_at=now,
                )
                self._session.add(record)
                self._session.flush()
                self._record_provenance(record, listing.provenance, now)
                transitions[identity_key] = DiscoveredJobState.NEW
                continue

            # A URL-only external lead can later be verified with a stronger
            # provider/external-ID identity.  The reverse must not happen: a
            # fresh but lower-authority verification failure should update
            # freshness while retaining the established provider identity.
            if self._authority(listing.detail_authority) >= self._authority(record.detail_authority):
                record.identity_key = identity_key
            effective_listing = self._effective_listing(record, listing)
            content_hash = self.content_hash(effective_listing)
            changed = record.content_hash != content_hash
            self._apply_listing(record, effective_listing, content_hash, now)
            self._record_provenance(record, listing.provenance, now)
            record.state = DiscoveredJobState.UPDATED if changed else DiscoveredJobState.UNCHANGED
            if changed:
                record.last_changed_at = now
            transitions[identity_key] = DiscoveredJobState(record.state)

        return observed_keys, transitions

    @staticmethod
    def source_key(source: str, source_token: str | None) -> str:
        return f"{source}:{source_token}" if source_token else source

    @staticmethod
    def identity_key(listing: JobListing) -> str:
        if listing.external_id:
            token = listing.source_token or ""
            return f"external:{listing.source.casefold()}:{token.casefold()}:{listing.external_id.casefold()}"
        return f"url:{JobDeduplicationService._canonical_url(listing.url)}"

    @staticmethod
    def content_hash(listing: JobListing) -> str:
        content = "\x1f".join(
            value or ""
            for value in (
                listing.title,
                listing.company,
                listing.location,
                listing.url,
                listing.description,
                listing.posted_at.isoformat() if listing.posted_at else None,
                listing.work_arrangement,
                listing.employment_type,
                JobDetailAuthority(listing.detail_authority).value,
                JobVerificationStatus(listing.verification_status).value,
                listing.verification_reason,
            )
        )
        return sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _apply_listing(
        record: DiscoveredJob,
        listing: JobListing,
        content_hash: str,
        now: datetime,
    ) -> None:
        record.source = listing.source
        record.source_token = listing.source_token
        record.company = listing.company
        record.external_id = listing.external_id
        record.title = listing.title
        record.location = listing.location
        record.url = listing.url
        record.description = listing.description
        record.posted_at = listing.posted_at
        record.work_arrangement = listing.work_arrangement
        record.employment_type = listing.employment_type
        record.detail_authority = listing.detail_authority
        record.verification_status = listing.verification_status
        record.verification_reason = listing.verification_reason
        record.content_hash = content_hash
        record.last_seen_at = now

    @staticmethod
    def _effective_listing(record: DiscoveredJob, incoming: JobListing) -> JobListing:
        """Never let lower-authority discovery text replace verified source detail."""
        if SqlAlchemyDiscoveredJobStateStore._authority(incoming.detail_authority) >= SqlAlchemyDiscoveredJobStateStore._authority(
            record.detail_authority
        ):
            return incoming
        return JobListing(
            source=record.source,
            source_token=record.source_token,
            external_id=record.external_id,
            title=record.title,
            company=record.company,
            location=record.location,
            url=record.url,
            description=record.description,
            posted_at=record.posted_at,
            work_arrangement=record.work_arrangement,
            employment_type=record.employment_type,
            provenance=incoming.provenance,
            detail_authority=JobDetailAuthority(record.detail_authority),
            # Detail provenance is monotonic, but verification describes the
            # latest deterministic source check.  Retain rich content while
            # making a failed current check non-actionable.
            verification_status=incoming.verification_status,
            verification_reason=incoming.verification_reason,
        )

    @staticmethod
    def _authority(value: str | JobDetailAuthority) -> int:
        return {
            JobDetailAuthority.EXTERNAL_SUMMARY: 0,
            JobDetailAuthority.PROVIDER_DETAIL: 1,
            JobDetailAuthority.VERIFIED_EMPLOYER_DETAIL: 2,
        }.get(JobDetailAuthority(value), 0)

    def _record_provenance(
        self,
        record: DiscoveredJob,
        provenance: JobProvenance | None,
        now: datetime,
    ) -> None:
        if provenance is None:
            return
        fingerprint = sha256(
            "\x1f".join(
                value or ""
                for value in (provenance.runtime, provenance.source_ref, provenance.discovered_via)
            ).encode("utf-8")
        ).hexdigest()
        existing = self._session.scalar(
            select(DiscoveredJobProvenance).where(
                DiscoveredJobProvenance.job_id == record.id,
                DiscoveredJobProvenance.fingerprint == fingerprint,
            )
        )
        if existing is None:
            self._session.add(
                DiscoveredJobProvenance(
                    job_id=record.id,
                    runtime=provenance.runtime,
                    source_ref=provenance.source_ref,
                    discovered_via=provenance.discovered_via,
                    fingerprint=fingerprint,
                    imported_at=now,
                )
            )

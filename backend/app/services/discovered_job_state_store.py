from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.discovered_job import DiscoveredJob
from app.schemas.discovery import DiscoveredJobState, JobListing
from app.services.job_deduplication_service import JobDeduplicationService


class DiscoveredJobStateStore(Protocol):
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
            content_hash = self.content_hash(listing)
            if record is None:
                record = DiscoveredJob(
                    identity_key=identity_key,
                    source=listing.source,
                    source_token=listing.source_token,
                    company=listing.company,
                    external_id=listing.external_id,
                    title=listing.title,
                    location=listing.location,
                    url=listing.url,
                    description=listing.description,
                    posted_at=listing.posted_at,
                    work_arrangement=listing.work_arrangement,
                    employment_type=listing.employment_type,
                    content_hash=content_hash,
                    state=DiscoveredJobState.NEW,
                    last_seen_at=now,
                    last_changed_at=now,
                )
                self._session.add(record)
                transitions[identity_key] = DiscoveredJobState.NEW
                continue

            changed = record.content_hash != content_hash
            self._apply_listing(record, listing, content_hash, now)
            record.state = DiscoveredJobState.UPDATED if changed else DiscoveredJobState.UNCHANGED
            if changed:
                record.last_changed_at = now
            transitions[identity_key] = DiscoveredJobState(record.state)

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
        record.content_hash = content_hash
        record.last_seen_at = now

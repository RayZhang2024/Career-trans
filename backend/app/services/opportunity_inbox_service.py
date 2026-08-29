"""Bounded read access to persisted external-discovery jobs."""

from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.discovered_job import DiscoveredJob
from app.models.discovered_job_provenance import DiscoveredJobProvenance
from app.schemas.discovery import DiscoveredJobState, JobListing, JobProvenance, JobVerificationStatus
from app.schemas.opportunity_inbox import (
    OpportunityInboxItem,
    OpportunityInboxResponse,
    PersistedJobProvenance,
)


class OpportunityInboxService:
    """Read recent agent-runtime discoveries without invoking discovery or ranking."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_recent(self, *, limit: int) -> OpportunityInboxResponse:
        records = self._session.scalars(
            select(DiscoveredJob)
            .where(
                or_(
                    DiscoveredJob.source == "agent_runtime",
                    DiscoveredJob.id.in_(
                        select(DiscoveredJobProvenance.job_id).where(
                            DiscoveredJobProvenance.runtime == "codex"
                        )
                    ),
                )
            )
            .order_by(DiscoveredJob.last_seen_at.desc(), DiscoveredJob.id.asc())
            .limit(limit)
        ).all()
        record_ids = [record.id for record in records]
        provenance_by_job: dict[str, list[PersistedJobProvenance]] = defaultdict(list)
        if record_ids:
            provenance_records = self._session.scalars(
                select(DiscoveredJobProvenance)
                .where(DiscoveredJobProvenance.job_id.in_(record_ids))
                .order_by(DiscoveredJobProvenance.imported_at.desc(), DiscoveredJobProvenance.id.asc())
            ).all()
            for provenance in provenance_records:
                provenance_by_job[provenance.job_id].append(
                    PersistedJobProvenance(
                        runtime=provenance.runtime,
                        source_ref=provenance.source_ref,
                        discovered_via=provenance.discovered_via,
                        imported_at=provenance.imported_at,
                    )
                )

        return OpportunityInboxResponse(
            limit=limit,
            jobs=[
                OpportunityInboxItem(
                    id=record.id,
                    job=JobListing(
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
                        detail_authority=record.detail_authority,
                        verification_status=record.verification_status,
                        verification_reason=record.verification_reason,
                        provenance=self._ranking_provenance(provenance_by_job[record.id]),
                    ),
                    state=DiscoveredJobState(record.state),
                    first_seen_at=record.first_seen_at,
                    last_seen_at=record.last_seen_at,
                    actionable=record.verification_status == JobVerificationStatus.VERIFIED,
                    verification_status=JobVerificationStatus(record.verification_status),
                    verification_reason=record.verification_reason,
                    provenance=provenance_by_job[record.id],
                )
                for record in records
            ],
        )

    @staticmethod
    def _ranking_provenance(provenance: list[PersistedJobProvenance]) -> JobProvenance | None:
        if not provenance:
            return None
        latest = provenance[0]
        return JobProvenance(
            runtime=latest.runtime,
            source_ref=latest.source_ref,
            discovered_via=latest.discovered_via,
        )

"""Deterministic shared-universe ingestion for external coding-agent discoveries."""

from sqlalchemy.orm import Session

from app.schemas.candidate import CandidateContext
from app.schemas.discovery import (
    DiscoveredJobLifecycleItem,
    DiscoveredJobState,
    JobDetailAuthority,
    JobListing,
    JobProvenance,
    JobVerificationStatus,
)
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts
from app.schemas.external_discovery import (
    ExternalDiscoveredJob,
    ExternalDiscoveryLeadDiagnostic,
    ExternalDiscoveryImportRequest,
    ExternalDiscoveryImportResponse,
    ExternalDiscoverySearchContextRequest,
    ExternalDiscoverySearchContextResponse,
)
from app.services.candidate_profile_compaction import candidate_search_profile
from app.services.discovered_job_state_store import DiscoveredJobStateStore, SqlAlchemyDiscoveredJobStateStore
from app.services.job_deduplication_service import JobDeduplicationService
from app.services.job_screening_service import JobScreeningService
from app.services.external_job_verification_service import ExternalJobVerificationService


class ExternalDiscoveryImportService:
    """Validate external evidence without invoking search or language-model services."""

    def __init__(
        self,
        *,
        session: Session,
        state_store: DiscoveredJobStateStore,
        deduplicator: JobDeduplicationService | None = None,
        screening: JobScreeningService | None = None,
        verifier: ExternalJobVerificationService | None = None,
    ) -> None:
        self._session = session
        self._state_store = state_store
        self._deduplicator = deduplicator or JobDeduplicationService()
        self._screening = screening or JobScreeningService()
        self._verifier = verifier or ExternalJobVerificationService()

    def import_jobs(self, request: ExternalDiscoveryImportRequest) -> ExternalDiscoveryImportResponse:
        normalized = [self._normalize(job, request.runtime) for job in request.jobs]
        accepted_before_dedup = [
            listing
            for listing in normalized
            if self._screening.matches_hard_constraints(listing, request.query)
        ]
        deduplicated, duplicate_count = self._deduplicator.deduplicate(accepted_before_dedup)
        bounded = deduplicated[: request.query.max_results]
        verifications = [self._verifier.verify(listing) for listing in bounded]
        persisted = [verification.listing for verification in verifications]
        accepted = [verification.listing for verification in verifications if verification.actionable]
        # External/Codex discovery has bounded coverage. Persist matches, but never
        # interpret a later omission as authoritative evidence that a job is inactive.
        states = self._state_store.persist(persisted)
        unverified_leads = [
            ExternalDiscoveryLeadDiagnostic(
                job=verification.listing,
                reason=verification.reason or "provider_detail_unavailable",
                state=states[SqlAlchemyDiscoveredJobStateStore.identity_key(verification.listing)],
            )
            for verification in verifications
            if not verification.actionable
            and SqlAlchemyDiscoveredJobStateStore.identity_key(verification.listing) in states
        ]
        return ExternalDiscoveryImportResponse(
            runtime=request.runtime,
            accepted_jobs=accepted,
            rejected_count=len(normalized) - len(accepted_before_dedup),
            deduplicated_count=duplicate_count,
            unverified_leads=unverified_leads,
            bounded_out_count=len(deduplicated) - len(bounded),
            job_states=states,
            lifecycle_jobs=[
                DiscoveredJobLifecycleItem(
                    job=listing,
                    state=states[SqlAlchemyDiscoveredJobStateStore.identity_key(listing)],
                )
                for listing in accepted
                if SqlAlchemyDiscoveredJobStateStore.identity_key(listing) in states
            ],
            lifecycle_counts=DiscoveryLifecycleCounts(
                new=sum(state is DiscoveredJobState.NEW for state in states.values()),
                updated=sum(state is DiscoveredJobState.UPDATED for state in states.values()),
                unchanged=sum(state is DiscoveredJobState.UNCHANGED for state in states.values()),
                inactive=sum(state is DiscoveredJobState.INACTIVE for state in states.values()),
            ),
        )

    def search_context(
        self,
        candidate_context: CandidateContext,
        request: ExternalDiscoverySearchContextRequest,
    ) -> ExternalDiscoverySearchContextResponse:
        return ExternalDiscoverySearchContextResponse(
            search_profile=candidate_search_profile(candidate_context),
            query=request.query,
            runtime_guidance=(
                "Use this compact context only to find public vacancy pages with your own tools. "
                "Submit factual supported evidence to the authenticated import endpoint; "
                "do not submit prompts, hidden reasoning, or credentials."
            ),
        )

    @staticmethod
    def _normalize(job: ExternalDiscoveredJob, runtime: str) -> JobListing:
        canonical_url = JobDeduplicationService._canonical_url(job.url)
        return JobListing(
            source="agent_runtime",
            source_token=runtime.casefold(),
            # URL identity keeps the shared universe independent of runtime identity.
            external_id=None,
            title=job.title,
            company=job.company,
            location=job.location,
            url=canonical_url,
            description=job.description,
            posted_at=job.posted_at,
            employment_type=job.employment_type,
            work_arrangement=job.work_arrangement,
            provenance=JobProvenance(
                runtime=runtime.casefold(),
                source_ref=job.provenance.source_ref,
                discovered_via=job.provenance.discovered_via,
            ),
            detail_authority=JobDetailAuthority.EXTERNAL_SUMMARY,
            verification_status=JobVerificationStatus.UNVERIFIED,
            verification_reason="pending_provider_verification",
        )

"""Deterministic shared-universe ingestion for external coding-agent discoveries."""

from sqlalchemy.orm import Session

from app.models.candidate_profile import CandidateProfile
from app.schemas.candidate import CandidateContext
from app.schemas.discovery import DiscoveredJobState, JobListing
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts
from app.schemas.external_discovery import (
    ExternalDiscoveredJob,
    ExternalDiscoveryImportRequest,
    ExternalDiscoveryImportResponse,
    ExternalDiscoverySearchContextRequest,
    ExternalDiscoverySearchContextResponse,
)
from app.services.candidate_profile_compaction import candidate_search_profile
from app.services.discovered_job_state_store import DiscoveredJobStateStore
from app.services.job_deduplication_service import JobDeduplicationService
from app.services.job_screening_service import JobScreeningService
from app.services.profile_service import get_profile_for_user


class ExternalDiscoveryImportService:
    """Validate external evidence without invoking search or language-model services."""

    def __init__(
        self,
        *,
        session: Session,
        state_store: DiscoveredJobStateStore,
        deduplicator: JobDeduplicationService | None = None,
        screening: JobScreeningService | None = None,
    ) -> None:
        self._session = session
        self._state_store = state_store
        self._deduplicator = deduplicator or JobDeduplicationService()
        self._screening = screening or JobScreeningService()

    def import_jobs(self, request: ExternalDiscoveryImportRequest) -> ExternalDiscoveryImportResponse:
        normalized = [self._normalize(job, request.runtime) for job in request.jobs]
        accepted_before_dedup = [
            listing
            for listing in normalized
            if self._screening.matches_hard_constraints(listing, request.query)
        ]
        deduplicated, duplicate_count = self._deduplicator.deduplicate(accepted_before_dedup)
        accepted = deduplicated[: request.query.max_results]
        states = self._state_store.synchronize(accepted, set())
        return ExternalDiscoveryImportResponse(
            runtime=request.runtime,
            accepted_jobs=accepted,
            rejected_count=len(normalized) - len(accepted_before_dedup),
            deduplicated_count=duplicate_count,
            job_states=states,
            lifecycle_counts=DiscoveryLifecycleCounts(
                new=sum(state is DiscoveredJobState.NEW for state in states.values()),
                updated=sum(state is DiscoveredJobState.UPDATED for state in states.values()),
                unchanged=sum(state is DiscoveredJobState.UNCHANGED for state in states.values()),
                inactive=sum(state is DiscoveredJobState.INACTIVE for state in states.values()),
            ),
        )

    def search_context(
        self,
        user_id: str,
        request: ExternalDiscoverySearchContextRequest,
    ) -> ExternalDiscoverySearchContextResponse | None:
        profile = get_profile_for_user(self._session, user_id)
        if profile is None:
            return None
        return ExternalDiscoverySearchContextResponse(
            search_profile=candidate_search_profile(self._candidate_context(profile)),
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
        )

    @staticmethod
    def _candidate_context(profile: CandidateProfile) -> CandidateContext:
        profile_text = " ".join(
            value
            for value in (profile.headline, profile.current_role, profile.summary)
            if value
        )
        criteria = " ".join(value for value in (profile.location,) if value)
        return CandidateContext(
            profile_text=profile_text,
            career_strategy_text=profile.career_goal or "",
            job_search_criteria_text=criteria,
        )

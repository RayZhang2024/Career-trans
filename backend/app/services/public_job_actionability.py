"""One deterministic authority for actionable public jobs."""

from app.models.discovered_job import DiscoveredJob
from app.schemas.discovery import DiscoveredJobState, JobVerificationStatus


def is_public_job_actionable(job: DiscoveredJob) -> bool:
    return job.verification_status == JobVerificationStatus.VERIFIED.value and job.state != DiscoveredJobState.INACTIVE.value

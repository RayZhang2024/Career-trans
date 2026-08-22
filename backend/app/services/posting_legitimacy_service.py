from datetime import datetime, timezone

from app.schemas.discovery import JobListing
from app.schemas.job_ranking import PostingLegitimacy, PostingLegitimacyAssessment


class PostingLegitimacyService:
    def assess(self, job: JobListing, now: datetime | None = None) -> PostingLegitimacyAssessment:
        if job.posted_at is None:
            return PostingLegitimacyAssessment(legitimacy=PostingLegitimacy.UNKNOWN, reasoning="No structured posting date is available.")
        current = now or datetime.now(timezone.utc)
        posted_at = job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=timezone.utc)
        age_days = (current - posted_at).days
        if 0 <= age_days <= 30:
            return PostingLegitimacyAssessment(legitimacy=PostingLegitimacy.HIGH_CONFIDENCE, reasoning="The structured posting date is recent.")
        if age_days > 180:
            return PostingLegitimacyAssessment(legitimacy=PostingLegitimacy.PROCEED_WITH_CAUTION, reasoning="The structured posting date is old.")
        return PostingLegitimacyAssessment(legitimacy=PostingLegitimacy.UNKNOWN, reasoning="The structured posting date is neither recent nor clearly stale.")

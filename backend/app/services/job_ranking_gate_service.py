from urllib.parse import urlsplit

from app.schemas.discovery import JobListing
from app.services.job_deduplication_service import JobDeduplicationService


class JobRankingGateService:
    """URL validation and deduplication before bounded semantic screening."""

    def gate(self, jobs: list[JobListing]) -> tuple[list[tuple[int, JobListing]], int]:
        valid = [(index, job) for index, job in enumerate(jobs) if self._is_analysable(job)]
        unique, removed = JobDeduplicationService().deduplicate([job for _, job in valid])
        allowed = {id(job) for job in unique}
        return [(index, job) for index, job in valid if id(job) in allowed], len(jobs) - len(valid) + removed

    @staticmethod
    def _is_analysable(job: JobListing) -> bool:
        parts = urlsplit(job.url)
        # Description completeness is assessed after relevance/archetype screening.
        # Missing public-job detail is not a reason to hide a potentially relevant role.
        return bool(parts.scheme in {"http", "https"} and parts.netloc)

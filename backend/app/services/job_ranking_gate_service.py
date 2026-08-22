from urllib.parse import urlsplit

from app.schemas.discovery import JobListing
from app.services.job_deduplication_service import JobDeduplicationService


class JobRankingGateService:
    """Conservative validation and deduplication before LLM screening."""

    def gate(self, jobs: list[JobListing]) -> tuple[list[tuple[int, JobListing]], int]:
        valid = [(index, job) for index, job in enumerate(jobs) if self._is_analysable(job)]
        unique, removed = JobDeduplicationService().deduplicate([job for _, job in valid])
        allowed = {id(job) for job in unique}
        return [(index, job) for index, job in valid if id(job) in allowed], len(jobs) - len(valid) + removed

    @staticmethod
    def _is_analysable(job: JobListing) -> bool:
        parts = urlsplit(job.url)
        return bool(job.description and job.description.strip() and parts.scheme in {"http", "https"} and parts.netloc)

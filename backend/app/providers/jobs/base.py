from typing import Protocol

from app.schemas.discovery import JobListing, JobSearchQuery


class JobSource(Protocol):
    """Replaceable source of already-normalized public job listings."""

    name: str

    def search(self, query: JobSearchQuery) -> list[JobListing]: ...

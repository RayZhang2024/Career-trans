"""Typed, bounded evidence exchanged with external discovery runtimes."""

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.candidate import CandidateSearchProfile
from app.schemas.discovery import DiscoveredJobLifecycleItem, DiscoveredJobState, JobListing, JobSearchQuery
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts


_MAX_IMPORTED_JOBS = 25


class ExternalDiscoveryProvenance(BaseModel):
    """Small factual provenance only; no prompts, reasoning, or scratchpad content."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_ref: str | None = Field(default=None, max_length=300)
    discovered_via: str | None = Field(default=None, max_length=120)


class ExternalDiscoveredJob(BaseModel):
    """Untrusted public vacancy facts submitted by an external runtime."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    company: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=500)
    url: str = Field(min_length=1, max_length=2048)
    description: str | None = Field(default=None, max_length=100_000)
    posted_at: datetime | None = None
    employment_type: str | None = Field(default=None, max_length=100)
    work_arrangement: str | None = Field(default=None, max_length=100)
    provenance: ExternalDiscoveryProvenance = Field(default_factory=ExternalDiscoveryProvenance)

    @field_validator("url")
    @classmethod
    def validate_public_web_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme.casefold() not in {"http", "https"} or not parts.netloc:
            raise ValueError("url must be an absolute HTTP(S) URL with a hostname.")
        return value


class CodexExternalDiscoveryOutput(BaseModel):
    """Strict final-message contract for the local Codex discovery runtime."""

    model_config = ConfigDict(extra="forbid")

    jobs: list[ExternalDiscoveredJob] = Field(min_length=1, max_length=_MAX_IMPORTED_JOBS)


class ExternalDiscoveryImportRequest(BaseModel):
    """Bounded, provider-neutral import request for an external agent runtime."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    runtime: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    jobs: list[ExternalDiscoveredJob] = Field(min_length=1, max_length=_MAX_IMPORTED_JOBS)
    query: JobSearchQuery


class ExternalDiscoveryImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: str
    authoritative: bool = False
    accepted_jobs: list[JobListing] = Field(default_factory=list)
    rejected_count: int = 0
    deduplicated_count: int = 0
    bounded_out_count: int = 0
    job_states: dict[str, DiscoveredJobState] = Field(default_factory=dict)
    lifecycle_jobs: list[DiscoveredJobLifecycleItem] = Field(default_factory=list)
    lifecycle_counts: DiscoveryLifecycleCounts = Field(default_factory=DiscoveryLifecycleCounts)


class ExternalDiscoverySearchContextRequest(BaseModel):
    """Read-only caller constraints paired with the authenticated user's compact profile."""

    model_config = ConfigDict(extra="forbid")

    query: JobSearchQuery


class ExternalDiscoverySearchContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_profile: CandidateSearchProfile
    query: JobSearchQuery
    runtime_guidance: str

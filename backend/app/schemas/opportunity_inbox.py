"""Typed read models for the persisted external-discovery opportunity inbox."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.discovery import DiscoveredJobState, JobListing, JobProvenance, JobVerificationStatus


class PersistedJobProvenance(BaseModel):
    """Bounded provenance already retained on a shared discovered-job record."""

    model_config = ConfigDict(extra="forbid")

    runtime: str
    source_ref: str | None = None
    discovered_via: str | None = None
    imported_at: datetime


class OpportunityInboxItem(BaseModel):
    """A recent external discovery with its stable persistence identity and evidence origin."""

    model_config = ConfigDict(extra="forbid")

    id: str
    job: JobListing
    state: DiscoveredJobState
    first_seen_at: datetime
    last_seen_at: datetime
    actionable: bool
    verification_status: JobVerificationStatus
    verification_reason: str | None = None
    provenance: list[PersistedJobProvenance] = Field(default_factory=list)


class OpportunityInboxResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int
    jobs: list[OpportunityInboxItem] = Field(default_factory=list)


class OpportunityInboxSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    discovered_job_id: str
    title: str
    company: str | None = None
    location: str | None = None
    work_arrangement: str | None = None
    employment_type: str | None = None
    url: str
    state: DiscoveredJobState
    verification_status: JobVerificationStatus
    verification_reason: str | None = None
    actionable: bool
    first_seen_at: datetime
    last_seen_at: datetime
    provenance: list[PersistedJobProvenance] = Field(default_factory=list)
    provenance_count: int


class OpportunityInboxSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[OpportunityInboxSummary] = Field(default_factory=list)
    limit: int
    truncated: bool

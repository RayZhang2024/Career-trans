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

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.discovery import DiscoveredJobState, JobListing, JobSearchQuery
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts


class BroadJobSearchQuery(JobSearchQuery):
    """Provider-neutral job-first search criteria for structured aggregators."""

    country: str = Field(default="gb", pattern=r"^[A-Za-z]{2}$")
    posted_within_days: int | None = Field(default=None, ge=1, le=90)
    salary_min: int | None = Field(default=None, ge=0)


class BroadJobSourceDiagnostic(BaseModel):
    """Safe, bounded acquisition facts for one structured broad source."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    succeeded: bool
    raw_count: int = Field(ge=0)
    normalized_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    deduplicated_count: int = Field(ge=0)
    bounded_out_count: int = Field(ge=0)
    imported_count: int = Field(ge=0)
    updated_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    failure: str | None = None


class BroadJobDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listings: list[JobListing] = Field(default_factory=list)
    provider_counts: dict[str, int] = Field(default_factory=dict)
    source_diagnostics: list[BroadJobSourceDiagnostic] = Field(default_factory=list)
    raw_count: int = Field(ge=0)
    normalized_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    deduplicated_count: int = Field(ge=0)
    bounded_count: int = Field(ge=0)
    bounded_out_count: int = Field(ge=0)
    job_states: dict[str, DiscoveredJobState] = Field(default_factory=dict)
    lifecycle_counts: DiscoveryLifecycleCounts = Field(default_factory=DiscoveryLifecycleCounts)

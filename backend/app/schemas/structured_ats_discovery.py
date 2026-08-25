from pydantic import BaseModel, ConfigDict, Field

from app.schemas.discovery import DiscoveredJobState, JobListing
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts


class StructuredAtsDiscoveryRequest(BaseModel):
    """Bounded deterministic scan of already-resolved public ATS sources."""

    model_config = ConfigDict(extra="forbid")

    providers: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    excluded_title_terms: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool | None = None
    employment_types: list[str] = Field(default_factory=list)
    max_sources: int = Field(default=20, ge=1, le=100)
    max_results: int = Field(default=100, ge=1, le=100)


class StructuredAtsSourceDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    provider: str
    source_token: str
    succeeded: bool
    discovered_count: int = Field(ge=0)
    imported_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    updated_count: int = Field(ge=0)
    deduplicated_count: int = Field(ge=0)
    bounded_out_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    failure: str | None = None


class StructuredAtsDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listings: list[JobListing] = Field(default_factory=list)
    source_diagnostics: list[StructuredAtsSourceDiagnostic] = Field(default_factory=list)
    raw_count: int = Field(ge=0)
    deduplicated_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    job_states: dict[str, DiscoveredJobState] = Field(default_factory=dict)
    lifecycle_counts: DiscoveryLifecycleCounts = Field(default_factory=DiscoveryLifecycleCounts)

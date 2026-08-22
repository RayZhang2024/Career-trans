from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class JobListing(BaseModel):
    """A provider-neutral job listing suitable for inexpensive discovery."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    external_id: str | None = None
    title: str = Field(min_length=1)
    company: str | None = None
    location: str | None = None
    url: str = Field(min_length=1)
    description: str | None = None
    posted_at: datetime | None = None
    work_arrangement: str | None = None
    employment_type: str | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class JobSearchQuery(BaseModel):
    """User-supplied discovery criteria; it contains no candidate profile data."""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(min_length=1)
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool | None = None
    companies: list[str] = Field(default_factory=list)
    max_results: int = Field(default=50, ge=1, le=100)


class JobDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listings: list[JobListing]
    provider_counts: dict[str, int]
    raw_count: int
    deduplicated_count: int
    screened_out_count: int
    provider_errors: dict[str, str] = Field(default_factory=dict)

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CompanyTarget(BaseModel):
    """A company to resolve against supported public ATS providers."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    slug: str | None = None
    website: str | None = None
    priority: int | None = Field(default=None, ge=0)
    enabled: bool = True


class ResolvedJobSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    provider: str
    source_token: str
    careers_url: str
    verified: bool = True


class CompanySourceResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    resolved: ResolvedJobSource | None = None
    attempted_providers: list[str] = Field(default_factory=list)
    error: str | None = None


class AtsResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies: list[CompanyTarget] = Field(min_length=1)


class AtsResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[CompanySourceResolution]


class CompanySourceStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    UNSUPPORTED = "unsupported"
    TEMPORARILY_FAILED = "temporarily_failed"


class CompanySourceProvenance(StrEnum):
    REGISTRY_REUSE = "registry_reuse"
    NEW_RESOLUTION = "new_resolution"
    REFRESHED_RESOLUTION = "refreshed_resolution"


class CompanySourceDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies: list[CompanyTarget] = Field(min_length=1)
    refresh: bool = False
    stale_after_days: int = Field(default=30, ge=1, le=365)


class CompanySourceDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    canonical_company_key: str
    status: CompanySourceStatus
    provenance: CompanySourceProvenance
    provider: str | None = None
    source_token: str | None = None
    careers_url: str | None = None
    diagnostic: str | None = None
    first_seen_at: datetime
    last_checked_at: datetime
    last_successful_resolution_at: datetime | None = None
    last_successful_fetch_at: datetime | None = None


class CompanySourceDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[CompanySourceDiscoveryResult]

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.job_sources import CompanyTarget


class EmployerUniverseEntry(BaseModel):
    """A candidate-agnostic employer record from a structured input source."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    website: str | None = None
    slug: str | None = None
    priority: int | None = Field(default=None, ge=0)
    enabled: bool = True
    source: str = Field(min_length=1)
    source_ref: str | None = None
    tags: list[str] = Field(default_factory=list)


class EmployerUniverseProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_ref: str | None = None


class EmployerUniverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watched_companies: list[CompanyTarget] = Field(default_factory=list)
    imported_entries: list[EmployerUniverseEntry] = Field(default_factory=list)
    include_registry: bool = False
    excluded_companies: list[str] = Field(default_factory=list)
    max_companies: int = Field(default=100, ge=1, le=1_000)


class EmployerUniverseCompany(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: CompanyTarget
    canonical_company_key: str
    provenance: list[EmployerUniverseProvenance] = Field(default_factory=list)


class EmployerUniverseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies: list[EmployerUniverseCompany]
    input_count: int
    disabled_count: int
    deduplicated_count: int
    excluded_count: int
    output_count: int
    source_counts: dict[str, int]

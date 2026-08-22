from pydantic import BaseModel, ConfigDict, Field


class CompanyTarget(BaseModel):
    """A company to resolve against supported public ATS providers."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    slug: str | None = None
    website: str | None = None


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

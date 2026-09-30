from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints


class JobDiscoveryProvider(StrEnum):
    TAVILY = "tavily"
    OPENAI = "openai"
    DISABLED = "disabled"


class EffectiveJobDiscoveryProvider(StrEnum):
    TAVILY = "tavily"
    OPENAI = "openai"
    BRAVE = "brave"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"


class JobDiscoveryCredentialSource(StrEnum):
    USER = "user"
    DEPLOYMENT = "deployment"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JobDiscoverySettingsRead(StrictModel):
    revision: int = Field(ge=0)
    provider_override: JobDiscoveryProvider | None
    deployment_provider: EffectiveJobDiscoveryProvider
    effective_provider: EffectiveJobDiscoveryProvider
    tavily_credential_configured: bool
    tavily_credential_source: JobDiscoveryCredentialSource | None
    tavily_user_credential_storage_available: bool
    tavily_credential_usable: bool


class JobDiscoverySettingsReplace(StrictModel):
    expected_revision: int = Field(ge=0)
    provider_override: JobDiscoveryProvider | None


class TavilyCredentialWrite(StrictModel):
    expected_revision: int = Field(ge=0)
    api_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]


class TavilyConnectionTestRead(StrictModel):
    success: bool = True
    credential_source: JobDiscoveryCredentialSource
    message: str

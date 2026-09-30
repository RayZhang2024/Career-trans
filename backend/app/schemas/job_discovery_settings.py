from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints


class JobDiscoveryProvider(StrEnum):
    TAVILY = "tavily"
    OPENAI = "openai"
    LOCAL_CODEX = "local_codex"
    DISABLED = "disabled"


class EffectiveJobDiscoveryProvider(StrEnum):
    TAVILY = "tavily"
    OPENAI = "openai"
    LOCAL_CODEX = "local_codex"
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


class LocalCodexCapabilityStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class LocalCodexAuthenticationStatus(StrEnum):
    SIGNED_IN = "signed_in"
    SIGNED_OUT = "signed_out"
    UNKNOWN = "unknown"


class LocalCodexDiscoveryStatus(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


class LocalCodexScheduledStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"


class LocalCodexStatusRead(StrictModel):
    enabled_by_deployment: bool
    cli_installed: bool
    version: str | None
    authentication_status: LocalCodexAuthenticationStatus
    structured_invocation_status: LocalCodexCapabilityStatus
    search_capability_status: LocalCodexCapabilityStatus
    manual_discovery_status: LocalCodexDiscoveryStatus
    scheduled_discovery_status: LocalCodexScheduledStatus
    message: str
    setup_guidance: str


class LocalCodexTestRead(StrictModel):
    success: bool
    result_count: int = Field(ge=0, le=1)
    message: str

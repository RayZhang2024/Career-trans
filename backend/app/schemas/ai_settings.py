"""Typed, provider-neutral AI settings and runtime preference contracts."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class SemanticOperation(StrEnum):
    CV_SEMANTIC_EXTRACTION = "cv_semantic_extraction"
    CANDIDATE_ADVISER = "candidate_adviser"
    JOB_EXTRACTION = "job_extraction"
    REQUIREMENT_MATCHING = "requirement_matching"
    CAREER_ALIGNMENT = "career_alignment"
    JOB_RELEVANCE = "job_relevance"
    JOB_ARCHETYPE = "job_archetype"
    AGENTIC_DISCOVERY = "agentic_discovery"
    APPLICATION_DRAFTING = "application_drafting"


class ReasoningEffort(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class PreferenceActivity(StrEnum):
    INHERITED = "inherited"
    ACTIVE = "active"
    INACTIVE_PROVIDER_MISMATCH = "inactive_provider_mismatch"
    INACTIVE_INVALID = "inactive_invalid"
    UNSUPPORTED = "unsupported"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


NonEmptyModelId = Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]


class OperationOverride(StrictModel):
    model: NonEmptyModelId | None = None
    reasoning_effort: ReasoningEffort | None = None

    @model_validator(mode="after")
    def require_override(self) -> "OperationOverride":
        if self.model is None and self.reasoning_effort is None:
            raise ValueError("An operation override must set a model or reasoning effort; omit it to inherit.")
        return self


class UserAiPreferences(StrictModel):
    """A complete preference document. Omitted override fields mean inherit."""

    default_model: NonEmptyModelId | None = None
    default_reasoning_effort: ReasoningEffort | None = None
    operation_overrides: dict[SemanticOperation, OperationOverride] = Field(default_factory=dict)


class UserAiSettingsReplace(UserAiPreferences):
    expected_revision: int = Field(ge=0)


class ModelCapabilityRead(StrictModel):
    id: str
    label: str
    structured_output: bool
    reasoning_efforts: tuple[ReasoningEffort, ...]


class AiModelCatalogRead(StrictModel):
    provider: str
    user_overrides_supported: bool
    models: tuple[ModelCapabilityRead, ...]


class EffectiveOperationRead(StrictModel):
    model: str
    reasoning_effort: ReasoningEffort | None
    inherited_model: bool
    inherited_reasoning_effort: bool


class AiSettingsRead(StrictModel):
    revision: int
    provider: str
    user_overrides_supported: bool
    persisted_override_provider: str | None
    overrides_active: bool
    preference_activity: PreferenceActivity
    preferences: UserAiPreferences
    effective: dict[SemanticOperation, EffectiveOperationRead]


class SemanticCredentialWrite(StrictModel):
    expected_revision: int = Field(ge=0)
    api_key: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class SemanticCredentialRead(StrictModel):
    policy: str
    storage_available: bool
    user_credential_configured: bool
    credential_revision: int
    effective_source: str

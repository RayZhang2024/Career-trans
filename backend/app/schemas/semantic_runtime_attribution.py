"""Strict historical projection of semantic runtime actually used."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.ai_settings import ReasoningEffort, SemanticOperation


class SemanticRuntimeAttributionStatus(StrEnum):
    AVAILABLE = "available"
    NOT_USED = "not_used"
    LEGACY_UNAVAILABLE = "legacy_unavailable"


NonEmptyRuntimeValue = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]


class SemanticRuntimeOperationAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: NonEmptyRuntimeValue
    reasoning_effort: ReasoningEffort | None


class SemanticRuntimeAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SemanticRuntimeAttributionStatus
    provider: NonEmptyRuntimeValue | None
    operations: dict[SemanticOperation, SemanticRuntimeOperationAttribution] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_status_shape(self) -> "SemanticRuntimeAttribution":
        if self.status == SemanticRuntimeAttributionStatus.AVAILABLE:
            if self.provider is None or not self.operations:
                raise ValueError("Available runtime attribution requires provider and operations.")
        elif self.provider is not None or self.operations:
            raise ValueError("Unavailable runtime attribution cannot contain provider or operations.")
        return self

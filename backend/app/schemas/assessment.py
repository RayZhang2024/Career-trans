from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.job import JobRequirement


class GapType(StrEnum):
    HARD_BLOCKER = "hard_blocker"
    MEANINGFUL_CAPABILITY_GAP = "meaningful_capability_gap"
    LEARNABLE_GAP = "learnable_gap"
    EVIDENCE_GAP = "evidence_gap"
    POSITIONING_GAP = "positioning_gap"


class GapSeverity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Gap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_index: int = Field(ge=0)
    requirement: JobRequirement
    gap_type: GapType
    severity: GapSeverity
    reason: str = Field(min_length=1)


class FitAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fit_score: float = Field(ge=0.0, le=100.0)
    essential_score: float = Field(ge=0.0, le=100.0)
    desirable_score: float | None = Field(default=None, ge=0.0, le=100.0)

    strengths: list[int] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    hard_blockers: list[int] = Field(default_factory=list)
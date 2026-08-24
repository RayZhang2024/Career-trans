from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JobEnrichmentStatus(StrEnum):
    ENRICHED = "enriched"
    STILL_UNASSESSED = "still_unassessed"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobEnrichmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)


class JobEnrichmentOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    title: str
    company: str | None = None
    status: JobEnrichmentStatus
    reason: str | None = None


class JobEnrichmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcomes: list[JobEnrichmentOutcome] = Field(default_factory=list)

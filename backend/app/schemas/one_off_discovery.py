from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.discovery import JobSearchQuery


class OneOffReadiness(StrEnum):
    CONFIGURED_FOR_LAUNCH = "configured_for_launch"
    NOT_CONFIGURED = "not_configured"
    UNSUPPORTED = "unsupported"
    MANUAL_RUNTIME_NOT_READY = "manual_runtime_not_ready"
    CANDIDATE_NOT_READY = "candidate_not_ready"


class OneOffStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"


class OneOffPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = "v2"
    country: str = "gb"
    max_search_queries: int = Field(default=6, ge=1, le=6)
    max_search_results_per_query: int = Field(default=10, ge=1, le=10)
    max_pages_to_open: int = Field(default=20, ge=1, le=20)
    max_discovered_jobs: int = Field(default=20, ge=1, le=20)
    max_semantic_candidates: int = Field(default=10, ge=1, le=10)
    max_full_analyses: int = Field(default=5, ge=1, le=5)
    min_relevance_score: float = Field(default=0.5, ge=0.5, le=0.5)


class OneOffPreflightRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effective_provider: str
    readiness: OneOffReadiness
    available: bool
    reason: str | None = None
    policy: OneOffPolicy
    provider_settings_revision: int
    launch_fingerprint: str


class OneOffLaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID
    expected_launch_fingerprint: str = Field(min_length=64, max_length=64)
    query: JobSearchQuery


class OneOffExecutionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    client_request_id: UUID
    query: JobSearchQuery
    policy: OneOffPolicy
    provider: dict[str, str]
    status: OneOffStatus
    started_at: datetime
    completed_at: datetime | None
    acquisition_summary: dict[str, int]
    failure_summary: dict[str, int]
    discovery_run_id: str | None

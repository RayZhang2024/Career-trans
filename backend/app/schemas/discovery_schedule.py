"""Typed, deliberately small V1 contracts for saved discovery schedules."""

from datetime import datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.discovery import JobSearchQuery


class ScheduleCadence(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class ExecutionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TriggerKind(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadence: ScheduleCadence
    timezone: str = Field(min_length=1, max_length=64)
    local_time: time
    weekdays: list[int] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def validate_timezone(self) -> "ScheduleSpec":
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return self

    @model_validator(mode="after")
    def validate_weekdays(self) -> "ScheduleSpec":
        if len(set(self.weekdays)) != len(self.weekdays) or any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("Weekdays must be distinct indexes from 0 through 6.")
        if self.cadence is ScheduleCadence.WEEKLY and not self.weekdays:
            raise ValueError("Weekly schedules require at least one weekday.")
        if self.cadence is ScheduleCadence.DAILY and self.weekdays:
            raise ValueError("Daily schedules must not specify weekdays.")
        return self


class StructuredAtsScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    companies: list[str] = Field(default_factory=list, max_length=100)
    providers: list[str] = Field(default_factory=list, max_length=20)
    all_resolved_sources: bool = False
    max_sources: int = Field(default=20, ge=1, le=100)
    max_results: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def require_explicit_scope(self) -> "StructuredAtsScheduleConfig":
        if self.enabled and not (self.companies or self.providers or self.all_resolved_sources):
            raise ValueError("Structured ATS schedules require an explicit source scope.")
        return self


class AgenticWebScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    country: str = Field(default="gb", min_length=2, max_length=2)
    max_search_queries: int = Field(default=6, ge=1, le=100)
    max_search_results_per_query: int = Field(default=10, ge=1, le=50)
    max_pages_to_open: int = Field(default=20, ge=1, le=100)
    max_discovered_jobs: int = Field(default=20, ge=1, le=100)


class AcquisitionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    structured_ats: StructuredAtsScheduleConfig = Field(default_factory=StructuredAtsScheduleConfig)
    agentic_web: AgenticWebScheduleConfig = Field(default_factory=AgenticWebScheduleConfig)

    @model_validator(mode="after")
    def require_channel(self) -> "AcquisitionConfig":
        if not (self.structured_ats.enabled or self.agentic_web.enabled):
            raise ValueError("At least one acquisition channel is required.")
        return self


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_semantic_candidates: int = Field(default=10, ge=1, le=100)
    max_full_analyses: int = Field(default=5, ge=1, le=30)
    min_relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)


class DiscoveryScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    schedule: ScheduleSpec
    query: JobSearchQuery
    acquisition: AcquisitionConfig
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)


class DiscoverySchedulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    schedule: ScheduleSpec | None = None
    query: JobSearchQuery | None = None
    acquisition: AcquisitionConfig | None = None
    evaluation: EvaluationConfig | None = None


class DiscoveryScheduleRead(DiscoveryScheduleCreate):
    id: str
    next_run_at: datetime | None
    last_execution_at: datetime | None


class ScheduledExecutionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    trigger_kind: TriggerKind
    scheduled_for: datetime | None
    status: ExecutionStatus
    config_snapshot: dict[str, object]
    web_search: dict[str, str | None] | None = None
    discovery_run_id: str | None
    acquisition_summary: dict[str, int]
    failure_summary: dict[str, int]
    started_at: datetime
    completed_at: datetime | None

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.discovery import DiscoveredJobState, JobVerificationStatus


class UserJobDecisionValue(StrEnum):
    UNDECIDED = "undecided"
    SHORTLISTED = "shortlisted"
    DISMISSED = "dismissed"


class UserJobDecisionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    discovered_job_id: str
    decision: UserJobDecisionValue
    revision: int | None = Field(default=None, ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserJobDecisionMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: UserJobDecisionValue
    expected_revision: int | None = Field(default=None, ge=1)


class UserJobDecisionListItem(UserJobDecisionRead):
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    posted_at: datetime | None = None
    work_arrangement: str | None = None
    employment_type: str | None = None
    state: DiscoveredJobState
    verification_status: JobVerificationStatus
    verification_reason: str | None = None
    actionable: bool
    first_seen_at: datetime
    last_seen_at: datetime


class UserJobDecisionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[UserJobDecisionListItem] = Field(default_factory=list)
    limit: int
    truncated: bool

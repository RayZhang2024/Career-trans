from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ApplicationTrackingStatus(StrEnum):
    PREPARED = "prepared"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    WITHDRAWN = "withdrawn"


class ApplicationTrackingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preparation_id: str = Field(min_length=1, max_length=36)
    status: ApplicationTrackingStatus


class ApplicationTrackingStatusEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ApplicationTrackingStatus
    expected_revision: int = Field(ge=1)


class ApplicationTrackingTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preparation_id: str
    preparation_created_at: datetime
    source_kind: str
    title: str
    company: str | None = None
    location: str | None = None
    public_url: str | None = None


class ApplicationTrackingEventRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int
    from_status: ApplicationTrackingStatus | None
    to_status: ApplicationTrackingStatus
    recorded_at: datetime


class ApplicationTrackingRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    preparation_id: str
    target: ApplicationTrackingTarget
    current_status: ApplicationTrackingStatus
    revision: int
    created_at: datetime
    updated_at: datetime
    events: list[ApplicationTrackingEventRead]


class ApplicationTrackingListItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    preparation_id: str
    target: ApplicationTrackingTarget
    current_status: ApplicationTrackingStatus
    revision: int
    created_at: datetime
    updated_at: datetime

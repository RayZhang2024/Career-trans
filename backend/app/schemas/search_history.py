from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.one_off_discovery import OneOffExecutionRead
from app.schemas.user_job_discovery import DiscoveryRunSummaryRead


class OneOffSearchHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["one_off"] = "one_off"
    id: str
    started_at: datetime
    execution: OneOffExecutionRead


class DiscoveryRunSearchHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["discovery_run"] = "discovery_run"
    id: str
    started_at: datetime
    run: DiscoveryRunSummaryRead


SearchHistoryItem = OneOffSearchHistoryItem | DiscoveryRunSearchHistoryItem


class SearchHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[SearchHistoryItem]
    limit: int
    truncated: bool

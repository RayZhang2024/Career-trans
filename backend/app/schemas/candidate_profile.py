from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CandidateProfileBase(BaseModel):
    headline: str | None = Field(default=None, max_length=200)
    summary: str | None = None
    current_role: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    career_goal: str | None = None


class CandidateProfileCreate(CandidateProfileBase):
    pass


class CandidateProfileUpdate(CandidateProfileBase):
    pass


class CandidateProfileRead(CandidateProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CandidateProfileBase(BaseModel):
    headline: str | None = Field(default=None, max_length=200)
    summary: str | None = None
    current_role: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    career_goal: str | None = None
    job_search_criteria: str | None = None
    # Application identity is renderer-owned contact data.  It deliberately is
    # not part of CandidateContext or CareerEvidence.
    display_name: str | None = Field(default=None, max_length=200)
    preferred_email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=100)
    linkedin_url: str | None = Field(default=None, max_length=500)
    github_url: str | None = Field(default=None, max_length=500)
    portfolio_url: str | None = Field(default=None, max_length=500)


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

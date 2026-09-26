from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.schemas.cv_overlap_review import StructuredProfileChangeComparison

from app.schemas.candidate_profile import CandidateProfileBase
from app.schemas.cv_ingestion import (
    Achievement,
    Credential,
    Education,
    Employment,
    Project,
    Skill,
)


class EditableCandidateProfileData(CandidateProfileBase):
    """CandidateProfile scalar proposal, excluding persistence metadata."""

    model_config = ConfigDict(extra="forbid")


class EditableCandidateStructuredData(BaseModel):
    """Manual career fields only; CV semantic evidence/provenance is immutable here."""

    model_config = ConfigDict(extra="forbid")

    employment: list[Employment] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    credentials: list[Credential] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)


RevisionAuthority = Literal["profile", "structured"]


class CandidateProfileRevisionState(StrEnum):
    DRAFT = "draft"
    REVIEW_READY = "review_ready"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class CandidateProfileRevisionRead(BaseModel):
    id: str
    state: Literal["draft", "review_ready", "confirmed", "discarded"]
    revision: int
    proposed_profile: EditableCandidateProfileData | None
    proposed_structured: EditableCandidateStructuredData | None
    changed_authorities: list[RevisionAuthority]
    stale_authorities: list[RevisionAuthority]
    structured_comparisons: list[StructuredProfileChangeComparison] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    discarded_at: datetime | None


class CandidateProfileRevisionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    proposed_profile: EditableCandidateProfileData | None = None
    proposed_structured: EditableCandidateStructuredData | None = None


class CandidateProfileRevisionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)

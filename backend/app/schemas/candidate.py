import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CareerEvidenceProvenance(BaseModel):
    """Canonical application provenance for CV and future confirmed factual sources."""

    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["cv", "confirmed_profile", "user_confirmed"] = "cv"
    document_sha256: str | None = None
    segment_ids: list[str] = Field(default_factory=list)
    source_ref: str | None = None

    @model_validator(mode="after")
    def validate_source_shape(self) -> "CareerEvidenceProvenance":
        if self.source_kind == "cv" and not self.document_sha256:
            raise ValueError("CV career-evidence provenance requires a document SHA.")
        if self.source_kind == "confirmed_profile" and not self.source_ref:
            raise ValueError("Confirmed-profile career-evidence provenance requires a source reference.")
        if self.source_kind == "user_confirmed":
            if not self.source_ref or not re.fullmatch(r"clarification:[0-9a-f]{64}", self.source_ref):
                raise ValueError("User-confirmed career-evidence provenance requires a clarification source reference.")
            if self.document_sha256 or self.segment_ids:
                raise ValueError("User-confirmed career-evidence provenance cannot use CV document provenance.")
        return self


class CareerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    evidence_type: str = "other"
    provenance: list[CareerEvidenceProvenance] = Field(default_factory=list)


class CandidateMatchingEvidence(BaseModel):
    """Provider-safe view of factual evidence for semantic matching."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    evidence_type: str = "other"
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)

class CandidateEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_authorisation: list[str] = Field(default_factory=list)
    security_clearances: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)


class CandidateEvidenceMaterializationStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"

class CandidateContext(BaseModel):
    """User-agnostic candidate context consumed by matching workflows.

    Production versions of this schema will be populated from authenticated user data.
    Repository demo profiles may be loaded into the same schema for development/tests.
    """

    model_config = ConfigDict(extra="forbid")

    source_name: str | None = None
    profile_text: str = ""
    skills_text: str = ""
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)
    evidence: list[CareerEvidence] = Field(default_factory=list)


class CandidateContextSummary(BaseModel):
    """Safe readiness diagnostic for confirmed persisted candidate context."""

    model_config = ConfigDict(extra="forbid")

    ready: bool
    employment_count: int = Field(ge=0)
    education_count: int = Field(ge=0)
    skill_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    structured_profile_available: bool = False
    evidence_materialization_status: CandidateEvidenceMaterializationStatus = CandidateEvidenceMaterializationStatus.NOT_APPLICABLE
    expected_evidence_count: int = Field(default=0, ge=0)
    missing_evidence_count: int = Field(default=0, ge=0)
    stale_evidence_count: int = Field(default=0, ge=0)
    career_strategy_configured: bool = False
    job_search_criteria_configured: bool = False


class CandidateSearchProfile(BaseModel):
    """Small, purpose-built candidate context for job relevance screening."""

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    skills: list[str] = Field(default_factory=list)
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""


class CandidateCareerProfile(BaseModel):
    """Small, purpose-built candidate context for career-alignment assessment."""

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)


class CandidateMatchingProfile(BaseModel):
    """Evidence-limited candidate context for semantic requirement matching."""

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    skills: list[str] = Field(default_factory=list)
    evidence: list[CandidateMatchingEvidence] = Field(default_factory=list)

    @field_validator("evidence", mode="before")
    @classmethod
    def project_legacy_runtime_evidence(cls, value: object) -> object:
        """Keep explicit-context callers compatible while never serializing provenance."""
        if not isinstance(value, list):
            return value
        return [
            {
                "evidence_id": item.evidence_id,
                "evidence_type": item.evidence_type,
                "title": item.title,
                "text": item.text,
                "skills": item.skills,
            }
            if isinstance(item, CareerEvidence)
            else item
            for item in value
        ]

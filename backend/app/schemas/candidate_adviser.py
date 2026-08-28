from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import CandidateEligibility
from app.schemas.cv_ingestion import CandidateCVData


class CandidateAdviserIntake(BaseModel):
    """Candidate-authored structured input; it is not CV-derived evidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    career_direction: str = ""
    work_preferences: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    self_assessment: list[str] = Field(default_factory=list)
    motivations: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)


class CandidateAdviserIntakeRead(CandidateAdviserIntake):
    updated_at: datetime


class CandidateAdviserEvidenceInput(BaseModel):
    """Bounded semantic projection of one confirmed CareerEvidence record."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    title: str = ""
    text: str = ""
    skills: list[str] = Field(default_factory=list)


class CandidateAdviserSemanticInput(BaseModel):
    """The complete bounded input used for adviser synthesis and fingerprints."""

    model_config = ConfigDict(extra="forbid")

    intake: CandidateAdviserIntake
    structured_cv: CandidateCVData
    career_evidence: list[CandidateAdviserEvidenceInput] = Field(default_factory=list)


class AdviserSourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(pattern="^(career_evidence|intake)$")
    reference: str = Field(min_length=1, max_length=200)


class AdviserInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1200)
    source_references: list[AdviserSourceReference] = Field(min_length=1, max_length=8)


class CandidateAdviserAssessmentContent(BaseModel):
    """Reviewable semantic interpretation with explicit supporting references."""

    model_config = ConfigDict(extra="forbid")

    professional_positioning: AdviserInsight
    transferable_strengths: list[AdviserInsight] = Field(default_factory=list, max_length=12)
    development_gaps: list[AdviserInsight] = Field(default_factory=list, max_length=12)
    role_hypotheses: list[AdviserInsight] = Field(default_factory=list, max_length=12)
    transition_assessment: AdviserInsight
    open_questions: list[AdviserInsight] = Field(default_factory=list, max_length=12)
    career_strategy_summary: AdviserInsight
    job_search_strategy_summary: AdviserInsight


class CandidateAdviserAssessmentStatus(StrEnum):
    REVIEW_READY = "review_ready"
    CONFIRMED = "confirmed"
    STALE = "stale"


class CandidateAdviserAssessmentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_fingerprint: str = Field(min_length=64, max_length=64)
    status: CandidateAdviserAssessmentStatus
    content: CandidateAdviserAssessmentContent
    created_at: datetime
    updated_at: datetime

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CVDocumentProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    media_type: str
    document_sha256: str
    segment_ids: list[str] = Field(default_factory=list)


class ExtractedCVSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    text: str
    page_number: int | None = None
    heading: str | None = None


class ExtractedCVDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provenance: CVDocumentProvenance
    segments: list[ExtractedCVSegment]


class Employment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employer: str
    title: str
    start_date: str | None = None
    end_date: str | None = None
    location: str | None = None
    description: str = ""


class Education(BaseModel):
    model_config = ConfigDict(extra="forbid")
    institution: str
    qualification: str
    field_of_study: str | None = None
    description: str = ""


class CredentialType(StrEnum):
    CERTIFICATION = "certification"
    PROFESSIONAL_QUALIFICATION = "professional_qualification"
    PROFESSIONAL_REGISTRATION = "professional_registration"
    FORMAL_TRAINING = "formal_training"
    PROFESSIONAL_MEMBERSHIP = "professional_membership"
    OTHER = "other"


class Credential(BaseModel):
    """A source-supported professional credential, separate from education."""

    model_config = ConfigDict(extra="forbid")

    name: str
    credential_type: CredentialType
    issuer: str | None = None
    issued_date: str | None = None
    expiry_date: str | None = None
    status: str | None = None
    description: str = ""


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    category: str | None = None


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    skills: list[str] = Field(default_factory=list)


class Achievement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class EvidenceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_sha256: str | None = None
    segment_ids: list[str] = Field(default_factory=list)
    source_kind: str = "cv"
    source_ref: str | None = None

    @model_validator(mode="after")
    def validate_source_shape(self) -> "EvidenceProvenance":
        if self.source_kind == "cv" and not self.document_sha256:
            raise ValueError("CV evidence provenance requires a document SHA.")
        if self.source_kind == "confirmed_profile" and not self.source_ref:
            raise ValueError("Confirmed-profile provenance requires a source reference.")
        return self


class CareerEvidenceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_type: str
    title: str
    text: str
    skills: list[str] = Field(default_factory=list)
    provenance: list[EvidenceProvenance] = Field(default_factory=list)


class CandidateCVData(BaseModel):
    """Reviewable facts only; deliberately excludes goals and job-search preferences."""

    model_config = ConfigDict(extra="forbid")
    employment: list[Employment] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    credentials: list[Credential] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    evidence: list[CareerEvidenceDraft] = Field(default_factory=list)


class CVIngestionState(StrEnum):
    UPLOADED = "uploaded"
    REVIEW_READY = "review_ready"
    CONFIRMED = "confirmed"


class CVIngestionDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    state: CVIngestionState
    documents: list[ExtractedCVDocument]
    merged: CandidateCVData | None = None
    created_at: datetime
    updated_at: datetime


class CVIngestionConfirmResponse(BaseModel):
    draft_id: str
    confirmed_evidence_count: int

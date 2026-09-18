"""Typed, immutable application-package contracts.

The LLM writes wording and cites these references.  Python owns all factual
career structure and rejects anything outside the bounded preparation context.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.schemas.job import JobProfile
from app.schemas.matching import EvidenceRef, EvidenceSourceType, RequirementMatch


class ApplicationTargetKind(StrEnum):
    DISCOVERED_JOB = "discovered_job"
    JOB_TEXT = "job_text"
    JOB_URL = "job_url"


class ApplicationLayoutStatus(StrEnum):
    FIT = "fit"
    OVERFLOW = "overflow"


class ApplicationAnswerStatus(StrEnum):
    DRAFTED = "drafted"
    UNSUPPORTED = "unsupported"


class ApplicationSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: EvidenceSourceType
    source_ref: str = Field(min_length=1)


class ApplicationTargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovered_job_id: str | None = None
    job_text: str | None = Field(default=None, min_length=100, max_length=200_000)
    job_url: HttpUrl | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> "ApplicationTargetInput":
        if sum(value is not None for value in (self.discovered_job_id, self.job_text, self.job_url)) != 1:
            raise ValueError("Exactly one application target source is required.")
        return self

    @property
    def kind(self) -> ApplicationTargetKind:
        if self.discovered_job_id is not None:
            return ApplicationTargetKind.DISCOVERED_JOB
        if self.job_text is not None:
            return ApplicationTargetKind.JOB_TEXT
        return ApplicationTargetKind.JOB_URL


class ApplicationPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: ApplicationTargetInput
    target_pages: int = Field(default=2, ge=1, le=3)
    include_cover_letter: bool = True
    application_questions: list[str] = Field(default_factory=list, max_length=8)


class ApplicationTargetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: ApplicationTargetKind
    canonical_discovered_job_id: str | None = None
    public_url: str | None = None
    title: str
    company: str | None = None
    location: str | None = None
    work_arrangement: str | None = None
    employment_type: str | None = None
    job_profile: JobProfile
    requirement_matches: list[RequirementMatch] = Field(default_factory=list)
    job_content_hash: str


class ApplicationIdentitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None


class TailoredBullet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=600)
    source_refs: list[ApplicationSourceRef] = Field(min_length=1, max_length=4)
    priority: int = Field(ge=1, le=100)


class TailoredRoleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employment_index: int = Field(ge=0)
    bullets: list[TailoredBullet] = Field(default_factory=list, max_length=6)


class TailoredRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employer: str
    title: str
    start_date: str | None = None
    end_date: str | None = None
    location: str | None = None
    bullets: list[TailoredBullet] = Field(default_factory=list)


class TailoredCVContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    professional_summary: str = Field(min_length=1, max_length=1600)
    summary_source_refs: list[ApplicationSourceRef] = Field(min_length=1, max_length=5)
    key_skills: list[str] = Field(default_factory=list, max_length=20)
    roles: list[TailoredRole] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    credentials: list[str] = Field(default_factory=list)


class CVWritingDraft(BaseModel):
    """The semantic component is intentionally unable to create role identity."""
    model_config = ConfigDict(extra="forbid")
    professional_summary: str = Field(min_length=1, max_length=1600)
    summary_source_refs: list[ApplicationSourceRef] = Field(min_length=1, max_length=5)
    key_skills: list[str] = Field(default_factory=list, max_length=20)
    role_drafts: list[TailoredRoleDraft] = Field(default_factory=list, max_length=20)


class CoverLetterContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=5000)
    source_refs: list[ApplicationSourceRef] = Field(min_length=1, max_length=10)


class ApplicationQuestionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    status: ApplicationAnswerStatus
    answer: str | None = None
    source_refs: list[ApplicationSourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def answer_is_consistent(self) -> "ApplicationQuestionAnswer":
        if self.status == ApplicationAnswerStatus.UNSUPPORTED and (self.answer is not None or self.source_refs):
            raise ValueError("Unsupported application answers cannot contain drafted content or citations.")
        if self.status == ApplicationAnswerStatus.DRAFTED and (not self.answer or not self.source_refs):
            raise ValueError("Drafted application answers require text and source references.")
        return self


class ApplicationQuestionAnswerSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[ApplicationQuestionAnswer] = Field(default_factory=list, max_length=8)


class ApplicationPreparationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cv: TailoredCVContent
    cover_letter: CoverLetterContent | None = None
    answers: list[ApplicationQuestionAnswer] = Field(default_factory=list)
    layout_status: ApplicationLayoutStatus = ApplicationLayoutStatus.FIT
    target_pages: int = Field(ge=1, le=3)
    actual_pdf_pages: int = Field(default=0, ge=0)


class ApplicationPreparationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    target: ApplicationTargetSnapshot
    identity: ApplicationIdentitySnapshot
    preparation_input_fingerprint: str
    preparation_contract_fingerprint: str
    result: ApplicationPreparationResult
    created_at: datetime


class ApplicationInsufficientDetailError(RuntimeError):
    """Safe typed boundary for a URL that cannot yield a usable vacancy."""

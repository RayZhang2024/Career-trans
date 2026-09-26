from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.cv_ingestion import Achievement, Credential, Education, Employment, Project, Skill
from app.schemas.candidate_adviser import ClarificationProposedEvidence
from app.schemas.profile_revision import CandidateProfileRevisionRead
from app.schemas.structured_profile import StructuredProfileSection
from app.schemas.structured_profile import StructuredProfileComparisonResult


Fingerprint = str | None
ProposalOperation = Literal["add", "replace_exact"]


class _ProposalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation: ProposalOperation
    target_fingerprint: Annotated[
        Fingerprint,
        Field(default=None, pattern=r"^[0-9a-f]{64}$"),
    ]

    @model_validator(mode="after")
    def validate_target(self):
        if self.operation == "add" and self.target_fingerprint is not None:
            raise ValueError("An add proposal cannot have a target fingerprint.")
        if self.operation == "replace_exact" and self.target_fingerprint is None:
            raise ValueError("A replace_exact proposal requires a target fingerprint.")
        return self


class EmploymentProposalUpdate(_ProposalUpdate):
    section: Literal["employment"]
    item: Employment


class EducationProposalUpdate(_ProposalUpdate):
    section: Literal["education"]
    item: Education


class CredentialProposalUpdate(_ProposalUpdate):
    section: Literal["credentials"]
    item: Credential


class SkillProposalUpdate(_ProposalUpdate):
    section: Literal["skills"]
    item: Skill


class ProjectProposalUpdate(_ProposalUpdate):
    section: Literal["projects"]
    item: Project


class AchievementProposalUpdate(_ProposalUpdate):
    section: Literal["achievements"]
    item: Achievement


CandidateAdviserProfileProposalUpdate: TypeAlias = Annotated[
    EmploymentProposalUpdate
    | EducationProposalUpdate
    | CredentialProposalUpdate
    | SkillProposalUpdate
    | ProjectProposalUpdate
    | AchievementProposalUpdate,
    Field(discriminator="section"),
]


class ConfirmedClarificationProposalSource(BaseModel):
    """The only facts permitted as the generator's new-claim basis."""

    model_config = ConfigDict(extra="forbid")

    clarification_id: str
    question_text: str = Field(max_length=1_200)
    confirmed_context_summary: str = Field(max_length=1_200)
    proposed_evidence: list[ClarificationProposedEvidence] = Field(max_length=3)


class EmploymentProposalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    item: Employment


class EducationProposalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    item: Education


class CredentialProposalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    item: Credential


class SkillProposalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    item: Skill


class ProjectProposalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    item: Project


class AchievementProposalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    item: Achievement


class StructuredProfileProposalTargetCatalogue(BaseModel):
    """First ten current items per section, in canonical source order."""

    model_config = ConfigDict(extra="forbid")

    employment: list[EmploymentProposalTarget] = Field(max_length=10)
    education: list[EducationProposalTarget] = Field(max_length=10)
    credentials: list[CredentialProposalTarget] = Field(max_length=10)
    skills: list[SkillProposalTarget] = Field(max_length=10)
    projects: list[ProjectProposalTarget] = Field(max_length=10)
    achievements: list[AchievementProposalTarget] = Field(max_length=10)
    truncated_sections: list[StructuredProfileSection]


class CandidateAdviserProfileProposalGenerationInput(BaseModel):
    """Bounded generation context; current Profile data is target-only."""

    model_config = ConfigDict(extra="forbid")

    source: ConfirmedClarificationProposalSource
    target_catalogue: StructuredProfileProposalTargetCatalogue


class CandidateAdviserProfileProposalGeneration(BaseModel):
    """Strict semantic output, canonically revalidated before persistence."""

    model_config = ConfigDict(extra="forbid")

    proposals: list[CandidateAdviserProfileProposalUpdate] = Field(max_length=6)


class CandidateAdviserProfileProposalState(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    TRANSFERRED = "transferred"


class CandidateAdviserProfileProposalOverlapAction(StrEnum):
    ADD_AS_NEW = "add_as_new"


class CandidateAdviserProfileProposalOverlapResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: CandidateAdviserProfileProposalOverlapAction
    base_structured_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    incoming_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_fingerprints: list[str]
    resolved_at: datetime


class CandidateAdviserProfileProposalOverlapResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    expected_comparison_base_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: CandidateAdviserProfileProposalOverlapAction


class CandidateAdviserProfileProposalRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    state: CandidateAdviserProfileProposalState
    revision: int
    source_clarification_id: str
    source_assessment_fingerprint: str
    original_update: CandidateAdviserProfileProposalUpdate
    proposed_update: CandidateAdviserProfileProposalUpdate
    created_at: datetime
    updated_at: datetime
    rejected_at: datetime | None
    transferred_at: datetime | None = None
    transferred_profile_revision_id: str | None = None
    comparison: StructuredProfileComparisonResult | None = None
    comparison_base_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    overlap_resolution: CandidateAdviserProfileProposalOverlapResolution | None = None
    overlap_resolution_stale: bool | None = None


class CandidateAdviserProfileProposalGenerationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[CandidateAdviserProfileProposalRead] = Field(max_length=6)


class CandidateAdviserProfileProposalTransferRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal: CandidateAdviserProfileProposalRead
    profile_revision: CandidateProfileRevisionRead


class CandidateAdviserProfileProposalPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    proposed_update: CandidateAdviserProfileProposalUpdate


class CandidateAdviserProfileProposalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)

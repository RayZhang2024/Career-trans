from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.cv_ingestion import Achievement, Credential, Education, Employment, Project, Skill


Fingerprint = str | None
ProposalOperation = Literal["add", "replace_exact"]


class StructuredProfileSection(StrEnum):
    EMPLOYMENT = "employment"
    EDUCATION = "education"
    CREDENTIALS = "credentials"
    SKILLS = "skills"
    PROJECTS = "projects"
    ACHIEVEMENTS = "achievements"


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


class CandidateAdviserProfileProposalState(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"


class CandidateAdviserProfileProposalRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    state: Literal["pending", "rejected"]
    revision: int
    source_clarification_id: str
    source_assessment_fingerprint: str
    original_update: CandidateAdviserProfileProposalUpdate
    proposed_update: CandidateAdviserProfileProposalUpdate
    created_at: datetime
    updated_at: datetime
    rejected_at: datetime | None


class CandidateAdviserProfileProposalPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    proposed_update: CandidateAdviserProfileProposalUpdate


class CandidateAdviserProfileProposalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)

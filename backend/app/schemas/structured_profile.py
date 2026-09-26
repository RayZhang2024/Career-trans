from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.cv_ingestion import (
    Achievement,
    CandidateCVData,
    Credential,
    Education,
    Employment,
    Project,
    Skill,
)


class StructuredProfileSection(StrEnum):
    EMPLOYMENT = "employment"
    EDUCATION = "education"
    CREDENTIALS = "credentials"
    SKILLS = "skills"
    PROJECTS = "projects"
    ACHIEVEMENTS = "achievements"


class StructuredItemSourceKind(StrEnum):
    CV = "cv"
    MANUAL_PROFILE = "manual_profile"
    CANDIDATE_ADVISER = "candidate_adviser"


class StructuredItemRelationship(StrEnum):
    NEW = "new"
    REINFORCEMENT = "reinforcement"
    REFINEMENT = "refinement"
    CONFLICT = "conflict"
    AMBIGUOUS = "ambiguous"


StructuredProfileItem: TypeAlias = Employment | Education | Credential | Skill | Project | Achievement

_SECTION_ITEM_TYPES: dict[StructuredProfileSection, type[BaseModel]] = {
    StructuredProfileSection.EMPLOYMENT: Employment,
    StructuredProfileSection.EDUCATION: Education,
    StructuredProfileSection.CREDENTIALS: Credential,
    StructuredProfileSection.SKILLS: Skill,
    StructuredProfileSection.PROJECTS: Project,
    StructuredProfileSection.ACHIEVEMENTS: Achievement,
}


def typed_structured_item(section: StructuredProfileSection | str, item: object) -> StructuredProfileItem:
    """Validate an item against exactly the model belonging to its section."""
    canonical_section = StructuredProfileSection(section)
    item_type = _SECTION_ITEM_TYPES[canonical_section]
    if isinstance(item, item_type):
        return item
    if isinstance(item, BaseModel):
        raise ValueError(f"{type(item).__name__} is not a {canonical_section.value} item.")
    return item_type.model_validate(item)


def current_items(data: CandidateCVData, section: StructuredProfileSection | str) -> list[StructuredProfileItem]:
    canonical_section = StructuredProfileSection(section)
    return list(getattr(data, canonical_section.value))


class StructuredProfileItemLineageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: StructuredProfileSection
    item: StructuredProfileItem
    source_kind: StructuredItemSourceKind
    source_ref: str = Field(min_length=1, max_length=256)
    relationship: StructuredItemRelationship
    predecessor_item: StructuredProfileItem | None = None

    @model_validator(mode="after")
    def validate_item_sections(self):
        typed_structured_item(self.section, self.item)
        if self.predecessor_item is not None:
            typed_structured_item(self.section, self.predecessor_item)
        if self.source_ref != self.source_ref.strip():
            raise ValueError("source_ref must be stored without surrounding whitespace.")
        if self.relationship is StructuredItemRelationship.NEW and self.predecessor_item is not None:
            raise ValueError("A new structured item cannot have a predecessor.")
        if self.relationship is StructuredItemRelationship.REFINEMENT and self.predecessor_item is None:
            raise ValueError("A refinement requires a predecessor item.")
        if self.relationship is StructuredItemRelationship.CONFLICT and self.predecessor_item is None:
            raise ValueError("A conflict requires its prior item snapshot.")
        # Phase 3 can preserve the exact target explicitly chosen by the user
        # after an ambiguous source review. The relationship still records the
        # original comparison result; the optional predecessor records that
        # selected target. Unresolved ambiguous comparisons still omit it.
        return self


class StructuredProfileLineageRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    user_id: str
    lineage_key: str
    section: StructuredProfileSection
    item_fingerprint: str
    item: StructuredProfileItem
    source_kind: StructuredItemSourceKind
    source_ref: str
    relationship: StructuredItemRelationship
    predecessor_fingerprint: str | None
    predecessor_item: StructuredProfileItem | None
    created_at: datetime


class StructuredProfileItemMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fingerprint: str
    item: StructuredProfileItem


class StructuredProfileComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: StructuredProfileSection
    relationship: StructuredItemRelationship
    incoming_item: StructuredProfileItem
    incoming_fingerprint: str
    candidate_matches: list[StructuredProfileItemMatch]
    target_fingerprint: str | None
    current_item: StructuredProfileItem | None

    @model_validator(mode="after")
    def validate_section_item_types(self):
        typed_structured_item(self.section, self.incoming_item)
        if self.current_item is not None:
            typed_structured_item(self.section, self.current_item)
        for match in self.candidate_matches:
            typed_structured_item(self.section, match.item)
        if self.relationship in {StructuredItemRelationship.NEW, StructuredItemRelationship.AMBIGUOUS}:
            if self.target_fingerprint is not None or self.current_item is not None:
                raise ValueError("New and ambiguous comparisons cannot select a unique target.")
        elif self.target_fingerprint is None or self.current_item is None:
            raise ValueError("A resolved comparison must retain its unique current target.")
        return self

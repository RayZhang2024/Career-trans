"""Typed explanatory provenance projections for current structured Profile items."""

from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredItemSourceKind,
    StructuredProfileItem,
    StructuredProfileSection,
    typed_structured_item,
)


class CVStructuredProfileSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["cv"]
    source_id: str
    filenames: list[str]
    source_state: str | None
    source_created_at: datetime | None
    source_updated_at: datetime | None
    available: bool


class ManualProfileStructuredSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["manual_profile"]
    source_id: str
    confirmed_at: datetime | None
    available: bool


class CandidateAdviserStructuredSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["candidate_adviser"]
    source_id: str
    source_clarification_id: str | None
    clarification_question: str | None
    proposal_item: StructuredProfileItem | None
    transferred_at: datetime | None
    available: bool


StructuredProfileResolvedSource: TypeAlias = Annotated[
    CVStructuredProfileSource | ManualProfileStructuredSource | CandidateAdviserStructuredSource,
    Field(discriminator="kind"),
]


class StructuredProfileLineageEventRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    section: StructuredProfileSection
    item_fingerprint: str
    item: StructuredProfileItem
    source_kind: StructuredItemSourceKind
    relationship: StructuredItemRelationship
    predecessor_fingerprint: str | None
    predecessor_item: StructuredProfileItem | None
    created_at: datetime
    source: StructuredProfileResolvedSource

    @model_validator(mode="after")
    def item_sections_match(self):
        typed_structured_item(self.section, self.item)
        if self.predecessor_item is not None:
            typed_structured_item(self.section, self.predecessor_item)
        if isinstance(self.source, CandidateAdviserStructuredSource) and self.source.proposal_item is not None:
            typed_structured_item(self.section, self.source.proposal_item)
        return self


class StructuredProfileHistoricalLineageEventRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    depth: int = Field(ge=1, le=20)
    lineage_event: StructuredProfileLineageEventRead


class StructuredProfileCurrentItemProvenanceRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: StructuredProfileSection
    item_index: int = Field(ge=0)
    item: StructuredProfileItem
    item_fingerprint: str
    direct_events: list[StructuredProfileLineageEventRead] = Field(max_length=100)
    history: list[StructuredProfileHistoricalLineageEventRead] = Field(max_length=100)
    source_history_available: bool

    @model_validator(mode="after")
    def item_section_matches(self):
        typed_structured_item(self.section, self.item)
        return self


class StructuredProfileProvenanceRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[StructuredProfileCurrentItemProvenanceRead]

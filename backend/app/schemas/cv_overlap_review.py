from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredProfileComparisonResult,
    StructuredProfileItem,
    StructuredProfileItemMatch,
    StructuredProfileSection,
)


class CVOverlapResolutionAction(StrEnum):
    REPLACE_CURRENT = "replace_current"
    KEEP_CURRENT = "keep_current"
    ADD_AS_NEW = "add_as_new"
    SKIP_INCOMING = "skip_incoming"


class CVOverlapResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: CVOverlapResolutionAction
    target_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_target(self):
        if self.action is CVOverlapResolutionAction.REPLACE_CURRENT:
            if self.target_fingerprint is None:
                raise ValueError("replace_current requires an exact target fingerprint.")
        elif self.target_fingerprint is not None:
            raise ValueError("Only replace_current accepts a target fingerprint.")
        return self


class CVOverlapReviewPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_review_revision: int = Field(ge=0)
    expected_base_structured_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_draft_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolutions: list[CVOverlapResolution] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def unique_item_keys(self):
        keys = [value.item_key for value in self.resolutions]
        if len(set(keys)) != len(keys):
            raise ValueError("Each overlap item may be resolved only once per request.")
        return self


class CVOverlapReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_key: str
    section: StructuredProfileSection
    incoming_item: StructuredProfileItem
    incoming_fingerprint: str
    relationship: StructuredItemRelationship
    candidate_matches: list[StructuredProfileItemMatch]
    target_fingerprint: str | None
    current_item: StructuredProfileItem | None
    saved_resolution: CVOverlapResolution | None = None
    resolution_required: bool


class CVOverlapIncomingDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: StructuredProfileSection
    first_item_key: str
    duplicate_item_key: str
    first_item: StructuredProfileItem
    duplicate_item: StructuredProfileItem


class CVOverlapReviewRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str
    revision: int
    base_structured_fingerprint: str
    draft_fingerprint: str
    stale: bool
    items: list[CVOverlapReviewItem]
    incoming_duplicates: list[CVOverlapIncomingDuplicate]


class StructuredProfileChangeComparison(BaseModel):
    """Typed, noncanonical projection of a proposed structured item comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_key: str
    comparison: StructuredProfileComparisonResult

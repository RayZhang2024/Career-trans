from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class CandidateSchemaStatus(StrEnum):
    COMPATIBLE = "compatible"
    ADDITIVE_REPAIR_AVAILABLE = "additive_repair_available"
    UNSUPPORTED_DRIFT = "unsupported_drift"


class CandidateCompatibilityStatus(StrEnum):
    ALREADY_COMPATIBLE = "already_compatible"
    REPAIRABLE = "repairable"
    UNRESOLVED = "unresolved"
    NOT_YET_CONFIRMED = "not_yet_confirmed"


class CandidateCompatibilityAction(StrEnum):
    ADD_MISSING_SCHEMA_COLUMN = "add_missing_schema_column"
    CREATE_MISSING_SCHEMA_TABLE = "create_missing_schema_table"
    ADD_MISSING_SCHEMA_INDEX = "add_missing_schema_index"
    PRESERVE_CURRENT_STRUCTURED = "preserve_current_structured"
    RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV = "reconstruct_structured_from_confirmed_cv"
    RECONCILE_ACTIVE_EVIDENCE = "reconcile_active_evidence"
    NO_ACTION = "no_action"
    MANUAL_RESOLUTION_REQUIRED = "manual_resolution_required"


class CandidateColumnCompatibility(StrictRead):
    name: str
    present: bool
    expected_type: str
    actual_type: str | None = None
    expected_nullable: bool
    actual_nullable: bool | None = None


class CandidateTableCompatibility(StrictRead):
    table: str
    table_exists: bool
    missing_columns: list[CandidateColumnCompatibility] = Field(default_factory=list)
    missing_indexes: list[str] = Field(default_factory=list)
    missing_constraints: list[str] = Field(default_factory=list)
    status: CandidateSchemaStatus
    planned_actions: list[CandidateCompatibilityAction] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class CandidateSchemaCompatibilityRead(StrictRead):
    dialect: str
    tables: list[CandidateTableCompatibility]
    status: CandidateSchemaStatus
    planned_actions: list[CandidateCompatibilityAction] = Field(default_factory=list)


class CandidateSchemaRepairAction(StrEnum):
    CREATE_TABLE = "create_table"
    ADD_COLUMN = "add_column"
    CREATE_INDEX = "create_index"


class CandidateSchemaAppliedRepair(StrictRead):
    action: CandidateSchemaRepairAction
    table: str
    column: str | None = None
    index: str | None = None


class CandidateSchemaRepairRead(StrictRead):
    changed: bool
    before: CandidateSchemaCompatibilityRead
    after: CandidateSchemaCompatibilityRead
    applied_repairs: list[CandidateSchemaAppliedRepair] = Field(default_factory=list)


class CandidateDataReconciliationAction(StrEnum):
    RECONSTRUCTED_STRUCTURED_PROFILE = "reconstructed_structured_profile"
    RECONCILED_ACTIVE_EVIDENCE = "reconciled_active_evidence"
    NO_ACTION = "no_action"
    UNRESOLVED = "unresolved"


class CandidateUserReconciliationRead(StrictRead):
    user_id: str
    changed: bool
    status_before: CandidateCompatibilityStatus | None = None
    status_after: CandidateCompatibilityStatus | None = None
    actions: list[CandidateDataReconciliationAction] = Field(default_factory=list)
    structured_reconstructed: bool = False
    evidence_reconciled: bool = False
    evidence_count_before: int = Field(default=0, ge=0)
    evidence_count_after: int = Field(default=0, ge=0)
    blocking_issues: list[str] = Field(default_factory=list)
    source_cv_draft_id: str | None = None


class CandidateBatchReconciliationRead(StrictRead):
    users: list[CandidateUserReconciliationRead] = Field(default_factory=list)
    user_count: int = 0
    changed_count: int = 0
    unresolved_count: int = 0


class CandidateLegacyIssueCode(StrEnum):
    PROFILE_ONLY_USER = "profile_only_user"
    UNCONFIRMED_CV_PRESENT = "unconfirmed_cv_present"
    INVALID_CURRENT_STRUCTURED_PROFILE = "unresolved_invalid_current_structured_profile"
    INVALID_CONFIRMED_CV = "unresolved_invalid_confirmed_cv"
    AMBIGUOUS_CONFIRMED_CVS = "unresolved_ambiguous_confirmed_sources"
    HISTORICAL_SOURCE_DIFFERS = "historical_source_differs_from_current_authority"
    EVIDENCE_INCOMPLETE = "active_evidence_incomplete"
    LEGACY_EVIDENCE_REUSABLE_BUT_STALE = "legacy_evidence_reusable_but_stale"
    CONFIRMED_FACTUAL_CLARIFICATIONS = "confirmed_factual_clarifications"
    UNCONFIRMED_CLARIFICATIONS_IGNORED = "unconfirmed_clarifications_ignored"
    PENDING_PROFILE_REVISIONS = "pending_profile_revisions_preserved"
    HISTORICAL_PROFILE_REVISIONS = "historical_profile_revisions_preserved"
    ADVISER_PROPOSALS_PRESERVED = "adviser_proposals_preserved"
    ADVISER_ASSESSMENT_STATUS = "adviser_assessment_status"
    LINEAGE_UNAVAILABLE = "legacy_lineage_unavailable"
    DUPLICATE_CURRENT_FACT = "legacy_duplicate_current_fact"
    HISTORICAL_ARTIFACTS_PRESERVED = "historical_artifacts_preserved"
    INSPECTION_FAILED = "user_inspection_failed"


class CandidateLegacyIssue(StrictRead):
    code: CandidateLegacyIssueCode
    detail: str
    count: int = 0
    related_ids: list[str] = Field(default_factory=list)
    blocking: bool = False


class CandidateStructuredAuthorityStatus(StrEnum):
    PRESERVE = "preserve"
    RECONSTRUCTABLE_FROM_CONFIRMED_CV = "reconstructable_from_confirmed_cv"
    MISSING_UNCONFIRMED = "missing_unconfirmed"
    UNRESOLVED = "unresolved"


class CandidateEvidenceCompatibilityStatus(StrEnum):
    COMPLETE = "complete"
    MISSING = "missing"
    STALE = "stale"
    MISSING_AND_STALE = "missing_and_stale"
    UNAVAILABLE = "unavailable"


class CandidateCompatibilitySummary(StrictRead):
    user_count: int
    already_compatible: int
    repairable: int
    unresolved: int
    not_yet_confirmed: int


class CandidateUserCompatibilityRead(StrictRead):
    user_id: str
    status: CandidateCompatibilityStatus
    profile_exists: bool
    structured_authority: CandidateStructuredAuthorityStatus
    confirmed_cv_count: int = 0
    latest_confirmed_cv_id: str | None = None
    equivalent_latest_confirmed_source_count: int = 0
    unconfirmed_cv_count: int = 0
    evidence_status: CandidateEvidenceCompatibilityStatus
    expected_evidence_count: int = 0
    active_evidence_count: int = 0
    missing_evidence_count: int = 0
    stale_evidence_count: int = 0
    confirmed_factual_clarification_count: int = 0
    unconfirmed_clarification_count: int = 0
    adviser_assessment_status: str = "unavailable"
    pending_profile_revision_count: int = 0
    historical_profile_revision_count: int = 0
    pending_adviser_proposal_count: int = 0
    historical_adviser_proposal_count: int = 0
    lineage_event_count: int = 0
    duplicate_current_fact_count: int = 0
    discovery_artifact_count: int = 0
    application_artifact_count: int = 0
    issues: list[CandidateLegacyIssue] = Field(default_factory=list)
    planned_actions: list[CandidateCompatibilityAction] = Field(default_factory=list)


class CandidateCompatibilityPlan(StrictRead):
    schema_report: CandidateSchemaCompatibilityRead = Field(alias="schema")
    users: list[CandidateUserCompatibilityRead]
    summary: CandidateCompatibilitySummary

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import CandidateContext, CareerEvidence
from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)


REQUIREMENT_EVIDENCE_PROVIDER_LIMIT = 8
REQUIREMENT_EVIDENCE_PER_REQUIREMENT_LIMIT = 3


class MatchType(StrEnum):
    DEMONSTRATED = "demonstrated"
    TRANSFERABLE = "transferable"
    INFERRED = "inferred"
    MISSING = "missing"
    UNKNOWN = "unknown"
    INCOMPATIBLE = "incompatible"

class EvidenceSourceType(StrEnum):
    CAREER_EVIDENCE = "career_evidence"
    CANDIDATE_PROFILE = "candidate_profile"
    CANDIDATE_ELIGIBILITY = "candidate_eligibility"
    SKILLS = "skills"
    EDUCATION = "education"
    EMPLOYMENT = "employment"
    CREDENTIAL = "credential"
    PROJECT = "project"
    APPLICATION_IDENTITY = "application_identity"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: EvidenceSourceType
    source_ref: str = Field(min_length=1)
    value: str | None = None

class RequirementMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_index: int = Field(ge=0)
    requirement: JobRequirement
    match_type: MatchType
    score: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    reasoning: str = Field(min_length=1)


class RequirementMatchSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[RequirementMatch] = Field(default_factory=list)


class SemanticRequirementMatch(BaseModel):
    """Model-owned judgement fields for one canonical job requirement.

    This is deliberately separate from ``RequirementMatch``: the semantic model
    selects a match for a requirement index, while application code owns and
    reattaches the canonical ``JobRequirement``.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_index: int = Field(ge=0)
    match_type: MatchType
    score: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)


class SemanticRequirementMatchSet(BaseModel):
    """Internal Structured Outputs contract for semantic requirement matching."""

    model_config = ConfigDict(extra="forbid")

    matches: list[SemanticRequirementMatch] = Field(default_factory=list)


class RequirementEvidenceScope(BaseModel):
    """Request-owned citation permissions for one local semantic requirement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_index: int = Field(ge=0)
    allowed_evidence_ids: tuple[str, ...] = ()


class RequirementEvidencePlan(BaseModel):
    """Deterministic bounded evidence retrieval for one semantic match request.

    This is deliberately not candidate state: it records the evidence union and
    citation scopes selected for one set of local semantic requirements.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_evidence: tuple[CareerEvidence, ...] = ()
    scopes: tuple[RequirementEvidenceScope, ...] = ()

    def validate_for(
        self,
        *,
        requirement_count: int,
        provider_limit: int,
        per_requirement_limit: int,
    ) -> None:
        expected_indexes = tuple(range(requirement_count))
        scope_indexes = tuple(scope.requirement_index for scope in self.scopes)
        if scope_indexes != expected_indexes:
            raise ValueError("Requirement evidence scopes must cover each local index exactly once.")

        provider_ids = tuple(item.evidence_id for item in self.provider_evidence)
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("Requirement evidence provider union contains duplicate IDs.")
        if len(provider_ids) > provider_limit:
            raise ValueError("Requirement evidence provider union exceeds its configured bound.")

        provider_id_set = set(provider_ids)
        for scope in self.scopes:
            if len(scope.allowed_evidence_ids) > per_requirement_limit:
                raise ValueError("Requirement evidence scope exceeds its configured bound.")
            if len(scope.allowed_evidence_ids) != len(set(scope.allowed_evidence_ids)):
                raise ValueError("Requirement evidence scope contains duplicate IDs.")
            if not set(scope.allowed_evidence_ids).issubset(provider_id_set):
                raise ValueError("Requirement evidence scope contains an ID outside the provider union.")


class RequirementMatchingRequirement(BaseModel):
    """Canonical requirement facts needed by the semantic matcher.

    ``source_text`` remains on the application-owned ``JobRequirement`` and is
    reattached after validation. It is not needed for the model to classify a
    candidate match and frequently duplicates the requirement wording.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    importance: RequirementImportance
    category: RequirementCategory


class RequirementMatchingJobProfile(BaseModel):
    """Purpose-built semantic matching input, not a replacement JobProfile."""

    model_config = ConfigDict(extra="forbid")

    requirements: list[RequirementMatchingRequirement] = Field(default_factory=list)


class JobMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_profile: JobProfile
    candidate_context: CandidateContext


class JobMatchMeRequest(BaseModel):
    """Authenticated matching input; candidate context comes from confirmed persistence."""

    model_config = ConfigDict(extra="forbid")

    job_profile: JobProfile


class JobMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[RequirementMatch]

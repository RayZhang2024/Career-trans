from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import CandidateEligibility, CareerEvidence


class AdviserConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CandidateIntakeSourceType(StrEnum):
    CAREER_EVIDENCE = "career_evidence"
    CANDIDATE_INTAKE = "candidate_intake"


class CandidateSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: CandidateIntakeSourceType
    source_ref: str = Field(min_length=1)


class CandidateCareerDirection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short_term_goal: str | None = None
    long_term_goal: str | None = None
    target_role_families: list[str] = Field(default_factory=list)
    acceptable_adjacent_roles: list[str] = Field(default_factory=list)
    desired_capabilities: list[str] = Field(default_factory=list)
    transition_preferences: list[str] = Field(default_factory=list)


class CandidateWorkPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_work: list[str] = Field(default_factory=list)
    disliked_work: list[str] = Field(default_factory=list)
    preferred_industries: list[str] = Field(default_factory=list)
    preferred_work_arrangements: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    customer_interaction_preference: str | None = None
    technical_hands_on_preference: str | None = None
    management_preference: str | None = None


class CandidateConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_authorisation: list[str] = Field(default_factory=list)
    security_clearances: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    relocation_preferences: list[str] = Field(default_factory=list)
    travel_preferences: list[str] = Field(default_factory=list)
    compensation_preferences: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)


class CandidateSelfAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    perceived_strengths: list[str] = Field(default_factory=list)
    development_areas: list[str] = Field(default_factory=list)
    capability_notes: list[str] = Field(default_factory=list)


class CandidateMotivations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasons_for_change: list[str] = Field(default_factory=list)
    priorities: list[str] = Field(default_factory=list)
    important_tradeoffs: list[str] = Field(default_factory=list)


class CandidateIntakeProfileData(BaseModel):
    """Candidate-authored career information that is not treated as CV evidence."""

    model_config = ConfigDict(extra="forbid")

    career_direction: CandidateCareerDirection = Field(default_factory=CandidateCareerDirection)
    work_preferences: CandidateWorkPreferences = Field(default_factory=CandidateWorkPreferences)
    constraints: CandidateConstraints = Field(default_factory=CandidateConstraints)
    self_assessment: CandidateSelfAssessment = Field(default_factory=CandidateSelfAssessment)
    motivations: CandidateMotivations = Field(default_factory=CandidateMotivations)


class CandidateAdviserSourceContext(BaseModel):
    """Bounded factual/source context supplied to the semantic career adviser."""

    model_config = ConfigDict(extra="forbid")

    profile_summary: str = ""
    skills: list[str] = Field(default_factory=list)
    career_strategy_text: str = ""
    job_search_criteria_text: str = ""
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)
    evidence: list[CareerEvidence] = Field(default_factory=list)


class RoleHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_family: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    current_fit: AdviserConfidence
    career_value: AdviserConfidence
    transition_risk: AdviserConfidence
    confidence: AdviserConfidence
    supporting_refs: list[CandidateSourceRef] = Field(default_factory=list)


class AdviserFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    confidence: AdviserConfidence
    supporting_refs: list[CandidateSourceRef] = Field(default_factory=list)


class CandidateAdviserAssessment(BaseModel):
    """Reviewable adviser interpretation; never authoritative capability evidence."""

    model_config = ConfigDict(extra="forbid")

    professional_identity: str = Field(min_length=1)
    strengths: list[AdviserFinding] = Field(default_factory=list)
    transferable_capabilities: list[AdviserFinding] = Field(default_factory=list)
    development_gaps: list[AdviserFinding] = Field(default_factory=list)
    role_hypotheses: list[RoleHypothesis] = Field(default_factory=list)
    career_transition_assessment: str = ""
    open_questions: list[str] = Field(default_factory=list)
    career_strategy_summary: str = ""
    job_search_strategy_summary: str = ""


class CandidateAdviserState(StrEnum):
    REVIEW_READY = "review_ready"
    CONFIRMED = "confirmed"
    STALE = "stale"


class CandidateIntakeRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: CandidateIntakeProfileData
    revision: int = Field(ge=0)
    confirmed: bool = False


class CandidateAdviserAssessmentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: CandidateAdviserState
    assessment: CandidateAdviserAssessment
    input_fingerprint: str

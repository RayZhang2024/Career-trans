from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import CandidateEligibility
from app.schemas.cv_ingestion import CandidateCVData


class CandidateAdviserIntake(BaseModel):
    """Candidate-authored structured input; it is not CV-derived evidence."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    career_direction: str = ""
    work_preferences: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    self_assessment: list[str] = Field(default_factory=list)
    motivations: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    eligibility: CandidateEligibility = Field(default_factory=CandidateEligibility)


class CandidateAdviserIntakeRead(CandidateAdviserIntake):
    updated_at: datetime


class CandidateAdviserEvidenceInput(BaseModel):
    """Bounded semantic projection of one confirmed CareerEvidence record."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    evidence_type: str = "other"
    title: str = ""
    text: str = ""
    skills: list[str] = Field(default_factory=list)


class CandidateAdviserClarificationInput(BaseModel):
    """Bounded confirmed context, not canonical evidence or provenance."""

    model_config = ConfigDict(extra="forbid")

    clarification_id: str = Field(min_length=64, max_length=64)
    question: str = Field(min_length=1, max_length=600)
    confirmed_context_summary: str = Field(min_length=1, max_length=1_200)
    answer_kind: str = Field(pattern="^(career_fact|eligibility_fact|preference_intent|mixed|insufficient)$")


class RefinementControlArea(BaseModel):
    """Non-evidentiary workflow metadata for bounded area convergence."""

    model_config = ConfigDict(extra="forbid")

    area_key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    selection_state: Literal["selected", "skipped"]


class RefinementControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    journey_id: str = Field(min_length=1, max_length=36)
    round_number: Literal[1, 2]
    rounds_completed: int = Field(ge=0, le=2)
    areas_allowed: bool
    prior_areas: list[RefinementControlArea] = Field(default_factory=list, max_length=12)


class CandidateAdviserSemanticInput(BaseModel):
    """The complete bounded input used for adviser synthesis and fingerprints."""

    model_config = ConfigDict(extra="forbid")

    intake: CandidateAdviserIntake
    structured_cv: CandidateCVData
    career_evidence: list[CandidateAdviserEvidenceInput] = Field(default_factory=list)
    clarifications: list[CandidateAdviserClarificationInput] = Field(default_factory=list, max_length=72)
    refinement_control: RefinementControlInput | None = None


class AdviserSourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(pattern="^(career_evidence|intake|clarification)$")
    reference: str = Field(min_length=1, max_length=200)


class AdviserInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1200)
    source_references: list[AdviserSourceReference] = Field(min_length=1, max_length=8)


class AdviserOpenQuestion(BaseModel):
    """Canonical open question; empty choices keep historical assessments readable."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1200)
    source_references: list[AdviserSourceReference] = Field(min_length=1, max_length=8)
    suggested_answers: list[str] = Field(default_factory=list, max_length=6)


class AdviserClarificationArea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_key: str = Field(min_length=1, max_length=80, pattern="^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=500)
    source_references: list[AdviserSourceReference] = Field(min_length=1, max_length=8)


class ProviderAdviserClarificationArea(AdviserClarificationArea):
    pass


class ProviderAdviserOpenQuestion(BaseModel):
    """Strict new-generation provider contract, intentionally not persisted."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1200)
    source_references: list[AdviserSourceReference] = Field(min_length=1, max_length=8)
    suggested_answers: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(min_length=3, max_length=6)


class CandidateAdviserAssessmentContent(BaseModel):
    """Reviewable semantic interpretation with explicit supporting references."""

    model_config = ConfigDict(extra="forbid")

    professional_positioning: AdviserInsight
    # Strict Structured Outputs require every object property to be listed in
    # JSON Schema ``required``. These remain allowed to be empty, but the model
    # must explicitly return them rather than omitting them.
    transferable_strengths: list[AdviserInsight] = Field(max_length=12)
    development_gaps: list[AdviserInsight] = Field(max_length=12)
    role_hypotheses: list[AdviserInsight] = Field(max_length=12)
    transition_assessment: AdviserInsight
    open_questions: list[AdviserOpenQuestion] = Field(max_length=12)
    clarification_areas: list[AdviserClarificationArea] = Field(default_factory=list, max_length=6)
    assessment_limitations: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=12)
    career_strategy_summary: AdviserInsight
    job_search_strategy_summary: AdviserInsight


class ProviderCandidateAdviserAssessmentContent(CandidateAdviserAssessmentContent):
    """Provider-only DTO requiring choices for each newly generated question."""

    open_questions: list[ProviderAdviserOpenQuestion] = Field(max_length=12)
    clarification_areas: list[ProviderAdviserClarificationArea] = Field(max_length=6)


class ProviderAreaQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_key: str = Field(min_length=1, max_length=80, pattern="^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    text: str = Field(min_length=1, max_length=1_200)
    source_references: list[AdviserSourceReference] = Field(min_length=1, max_length=8)
    suggested_answers: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(min_length=3, max_length=6)


class ProviderAreaQuestionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_key: str = Field(min_length=1, max_length=80)
    questions: list[ProviderAreaQuestion] = Field(min_length=1, max_length=5)


class ProviderRoundQuestionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area_groups: list[ProviderAreaQuestionGroup] = Field(min_length=1, max_length=6)


class CandidateAdviserQuestionGenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    journey_id: str = Field(min_length=1, max_length=36)
    round_number: Literal[1, 2]
    selected_areas: list[AdviserClarificationArea] = Field(min_length=1, max_length=6)
    intake: CandidateAdviserIntake
    structured_cv: CandidateCVData
    career_evidence: list[CandidateAdviserEvidenceInput] = Field(default_factory=list, max_length=24)
    clarifications: list[CandidateAdviserClarificationInput] = Field(default_factory=list, max_length=72)


class CandidateAdviserAssessmentStatus(StrEnum):
    REVIEW_READY = "review_ready"
    CONFIRMED = "confirmed"
    STALE = "stale"


class CandidateAdviserAssessmentContractVersion(StrEnum):
    LEGACY_QUESTIONS = "legacy_questions"
    CLARIFICATION_AREAS_V1 = "clarification_areas_v1"


class CandidateAdviserAssessmentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_fingerprint: str = Field(min_length=64, max_length=64)
    assessment_authority_token: str = Field(pattern="^[0-9a-f]{64}$")
    contract_version: CandidateAdviserAssessmentContractVersion = CandidateAdviserAssessmentContractVersion.LEGACY_QUESTIONS
    status: CandidateAdviserAssessmentStatus
    content: CandidateAdviserAssessmentContent
    created_at: datetime
    updated_at: datetime


class CandidateAdviserAreaSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_refinement_journey_id: str = Field(min_length=1, max_length=36)
    expected_round_number: Literal[1, 2]
    expected_assessment_fingerprint: str = Field(pattern="^[0-9a-f]{64}$")
    expected_assessment_authority_token: str = Field(pattern="^[0-9a-f]{64}$")
    selected_area_keys: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(max_length=6)


class CandidateAdviserQuestionGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_refinement_journey_id: str = Field(min_length=1, max_length=36)
    expected_round_number: Literal[1, 2]
    expected_assessment_fingerprint: str = Field(pattern="^[0-9a-f]{64}$")
    expected_assessment_authority_token: str = Field(pattern="^[0-9a-f]{64}$")


class ClarificationAnswerKind(StrEnum):
    CAREER_FACT = "career_fact"
    ELIGIBILITY_FACT = "eligibility_fact"
    PREFERENCE_INTENT = "preference_intent"
    MIXED = "mixed"
    INSUFFICIENT = "insufficient"


class ClarificationProposedEvidence(BaseModel):
    """Affirmative career fact proposed from one candidate-authored answer."""

    model_config = ConfigDict(extra="forbid")

    # The clarification interpreter is allowed to propose only career facts.
    # Eligibility remains candidate-authored intake/eligibility context, never
    # matching evidence, including for an otherwise mixed answer.
    fact_domain: Literal["career"]
    evidence_type: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=1_200)
    skills: list[str] = Field(default_factory=list, max_length=16)


class ClarificationInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_kind: ClarificationAnswerKind
    confirmed_context_summary: str = Field(min_length=1, max_length=1_200)
    proposed_evidence: list[ClarificationProposedEvidence] = Field(default_factory=list, max_length=3)


class CandidateAdviserClarificationStatus(StrEnum):
    UNANSWERED = "unanswered"
    REVIEW_READY = "review_ready"
    CONFIRMED = "confirmed"


class CandidateAdviserClarificationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarification_id: str = Field(min_length=64, max_length=64)
    question_text: str = Field(min_length=1, max_length=1_200)
    question_source_references: list[AdviserSourceReference] = Field(default_factory=list)
    suggested_answers: list["CandidateAdviserSuggestedAnswer"] = Field(default_factory=list)
    structured_response: "CandidateAdviserStructuredResponse | None" = None
    priority_index: int = Field(ge=0)
    parent_area_key: str | None = None
    parent_area_title: str | None = None
    round_number: int | None = Field(default=None, ge=1, le=2)
    status: CandidateAdviserClarificationStatus
    answer_text: str | None = None
    interpretation: ClarificationInterpretation | None = None
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    session_active: bool = False


class CandidateAdviserSuggestedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")
    text: str = Field(min_length=1, max_length=240)


class CandidateAdviserStructuredResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_option_ids: list[Annotated[str, Field(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")]] = Field(default_factory=list, max_length=6)
    custom_answer_text: str = Field(default="", max_length=4_000)
    special_selection: Literal["not_sure"] | None = None


class CandidateAdviserClarificationInterpretationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_text: str = Field(min_length=1, max_length=1200)
    selected_answers: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(default_factory=list, max_length=6)
    additional_detail: str = Field(default="", max_length=4_000)


class CandidateAdviserClarificationAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_text: str | None = Field(default=None, min_length=1, max_length=4_000)
    selected_option_ids: list[Annotated[str, Field(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")]] | None = Field(default=None, max_length=6)
    custom_answer_text: str | None = Field(default=None, max_length=4_000)
    special_selection: Literal["not_sure"] | None = None

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import CareerAssessment
from app.schemas.discovery import JobListing
from app.schemas.job import JobProfile
from app.schemas.matching import RequirementMatch
from app.schemas.recommendation import RecommendationAssessment


class JobArchetype(StrEnum):
    AI_FORWARD_DEPLOYED = "ai_forward_deployed"
    AI_SOLUTIONS_ARCHITECT = "ai_solutions_architect"
    AI_PLATFORM_LLMOPS = "ai_platform_llmops"
    AGENTIC_AUTOMATION = "agentic_automation"
    TECHNICAL_AI_PRODUCT = "technical_ai_product"
    AI_TRANSFORMATION = "ai_transformation"
    OTHER = "other"


class PostingLegitimacy(StrEnum):
    HIGH_CONFIDENCE = "high_confidence"
    PROCEED_WITH_CAUTION = "proceed_with_caution"
    UNKNOWN = "unknown"


class JobRelevanceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevant: bool
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)


class JobArchetypeAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archetype: JobArchetype
    reasoning: str = Field(min_length=1)


class PostingLegitimacyAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legitimacy: PostingLegitimacy
    reasoning: str = Field(min_length=1)


class JobRankingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[JobListing] = Field(min_length=1)
    candidate_context: CandidateContext
    max_semantic_candidates: int = Field(default=30, ge=1, le=100)
    max_full_analyses: int = Field(default=10, ge=1, le=30)
    min_relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)


class JobRankingMeRequest(BaseModel):
    """Authenticated ranking input without caller-supplied candidate context."""

    model_config = ConfigDict(extra="forbid")

    jobs: list[JobListing] = Field(min_length=1)
    max_semantic_candidates: int = Field(default=30, ge=1, le=100)
    max_full_analyses: int = Field(default=10, ge=1, le=30)
    min_relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)


class JobRankingFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobListing
    stage: str
    error: str


class SemanticScreeningDiagnostic(BaseModel):
    """Semantic-screening result retained whether or not a job becomes a finalist."""

    model_config = ConfigDict(extra="forbid")

    job: JobListing
    relevance: JobRelevanceAssessment | None = None
    archetype: JobArchetypeAssessment | None = None
    failure_stage: str | None = None
    error: str | None = None


class RankedJobOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobListing
    relevance: JobRelevanceAssessment
    archetype: JobArchetypeAssessment
    fit_assessment: FitAssessment
    career_assessment: CareerAssessment
    recommendation_assessment: RecommendationAssessment
    legitimacy: PostingLegitimacyAssessment
    rank: int
    # These are already produced by CareerAnalysisGraph. They are optional solely
    # for compatibility with legacy/custom graph implementations.
    job_profile: JobProfile | None = None
    requirement_matches: list[RequirementMatch] = Field(default_factory=list)


class JobRankingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovered_count: int
    gated_out_count: int
    relevance_screened_count: int
    finalist_count: int
    analysed_count: int
    # Additive application-owned diagnostics permit run orchestration to give
    # deterministic gate rejections a truthful terminal outcome.
    gated_out_jobs: list[JobListing] = Field(default_factory=list)
    semantic_screening: list[SemanticScreeningDiagnostic] = Field(default_factory=list)
    results: list[RankedJobOpportunity] = Field(default_factory=list)
    failures: list[JobRankingFailure] = Field(default_factory=list)

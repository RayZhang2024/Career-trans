from typing import TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import CareerAssessment
from app.schemas.discovery import JobListing
from app.schemas.job import JobProfile
from app.schemas.matching import RequirementMatch
from app.schemas.recommendation import RecommendationAssessment
from app.services.career_assessment_service import CareerAssessmentService
from app.services.fit_assessment_service import FitAssessmentService
from app.services.job_analysis_service import JobAnalysisService
from app.services.recommendation_service import RecommendationService
from app.services.requirement_matching_service import RequirementMatchingService


class CareerAnalysisState(TypedDict, total=False):
    job_text: str
    job_listing: JobListing | None
    candidate_context: CandidateContext | dict[str, object]
    job_profile: JobProfile
    requirement_matches: list[RequirementMatch]
    fit_assessment: FitAssessment
    career_assessment: CareerAssessment
    recommendation_assessment: RecommendationAssessment
    errors: list[str]


class CareerAnalysisGraph:
    """Linear orchestration over the existing career-analysis services."""

    def __init__(
        self,
        *,
        job_analysis_service: JobAnalysisService,
        requirement_matching_service: RequirementMatchingService,
        fit_assessment_service: FitAssessmentService,
        career_assessment_service: CareerAssessmentService,
        recommendation_service: RecommendationService,
    ) -> None:
        self._job_analysis_service = job_analysis_service
        self._requirement_matching_service = requirement_matching_service
        self._fit_assessment_service = fit_assessment_service
        self._career_assessment_service = career_assessment_service
        self._recommendation_service = recommendation_service
        self._graph = self._build_graph()

    @property
    def compiled_graph(self) -> CompiledStateGraph:
        """Expose the compiled graph for development tooling such as Studio."""
        return self._graph

    def invoke(
        self,
        *,
        job_text: str,
        candidate_context: CandidateContext,
        job_listing: JobListing | None = None,
    ) -> CareerAnalysisState:
        result = self._graph.invoke(
            {
                "job_text": job_text,
                "job_listing": job_listing,
                "candidate_context": candidate_context,
            }
        )
        return cast(CareerAnalysisState, result)

    def _build_graph(self) -> CompiledStateGraph:
        builder = StateGraph(CareerAnalysisState)
        builder.add_node("extract_job", self._extract_job)
        builder.add_node("match_requirements", self._match_requirements)
        builder.add_node("assess_fit", self._assess_fit)
        builder.add_node(
            "assess_career_alignment",
            self._assess_career_alignment,
        )
        builder.add_node("build_recommendation", self._build_recommendation)

        builder.add_edge(START, "extract_job")
        builder.add_conditional_edges(
            "extract_job",
            self._route_after_extraction,
            {"continue": "match_requirements", "insufficient": END},
        )
        builder.add_edge("match_requirements", "assess_fit")
        builder.add_edge("assess_fit", "assess_career_alignment")
        builder.add_edge("assess_career_alignment", "build_recommendation")
        builder.add_edge("build_recommendation", END)
        return builder.compile()

    def _extract_job(self, state: CareerAnalysisState) -> CareerAnalysisState:
        extracted = self._job_analysis_service.analyse_text(state["job_text"])
        return {
            "job_profile": self._merge_known_listing_metadata(
                extracted,
                state.get("job_listing"),
            )
        }

    @staticmethod
    def _merge_known_listing_metadata(
        extracted: JobProfile,
        listing: JobListing | None,
    ) -> JobProfile:
        """Keep explicit discovery facts authoritative over semantic extraction.

        The description remains the sole semantic input for requirements and
        responsibilities. For overlapping factual fields, a non-blank,
        non-placeholder listing value wins deterministically; extraction fills
        only facts absent from discovery. This avoids silently reconciling
        contradictory public-source and description claims.
        """
        if listing is None:
            return extracted

        updates: dict[str, str | None] = {}
        for field, value in {
            "title": listing.title,
            "company": listing.company,
            "location": listing.location,
            "work_arrangement": listing.work_arrangement,
            "employment_type": listing.employment_type,
        }.items():
            updates[field] = CareerAnalysisGraph._known_text(value) or getattr(extracted, field)
        return extracted.model_copy(update=updates)

    @staticmethod
    def _known_text(value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        cleaned = value.strip()
        if cleaned.casefold() in {"unknown", "unspecified", "not specified", "n/a"}:
            return None
        return cleaned

    def _match_requirements(
        self,
        state: CareerAnalysisState,
    ) -> CareerAnalysisState:
        match_set = self._requirement_matching_service.match(
            state["job_profile"],
            self._candidate_context(state),
        )
        return {"requirement_matches": match_set.matches}

    @staticmethod
    def _route_after_extraction(state: CareerAnalysisState) -> str:
        """Do not turn an empty requirement set into a zero-fit assessment."""
        if not state["job_profile"].requirements:
            return "insufficient"
        return "continue"

    def _assess_fit(self, state: CareerAnalysisState) -> CareerAnalysisState:
        return {
            "fit_assessment": self._fit_assessment_service.assess(
                state["requirement_matches"]
            )
        }

    def _assess_career_alignment(
        self,
        state: CareerAnalysisState,
    ) -> CareerAnalysisState:
        return {
            "career_assessment": self._career_assessment_service.assess(
                state["job_profile"],
                self._candidate_context(state),
                state["fit_assessment"],
            )
        }

    def _build_recommendation(
        self,
        state: CareerAnalysisState,
    ) -> CareerAnalysisState:
        return {
            "recommendation_assessment": self._recommendation_service.assess(
                state["fit_assessment"],
                state["career_assessment"],
            )
        }

    @staticmethod
    def _candidate_context(state: CareerAnalysisState) -> CandidateContext:
        candidate_context = state["candidate_context"]
        if isinstance(candidate_context, CandidateContext):
            return candidate_context
        return CandidateContext.model_validate(candidate_context)

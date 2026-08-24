from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.job import JobProfile, JobRequirement
from app.schemas.matching import RequirementMatch, RequirementMatchSet
from app.schemas.recommendation import (
    Recommendation,
    RecommendationAssessment,
)
from app.workflows.career_analysis_graph import CareerAnalysisGraph


def test_graph_runs_existing_services_in_order_and_returns_final_state() -> None:
    calls: list[str] = []
    candidate = CandidateContext(source_name="synthetic_candidate")
    job_profile = JobProfile(title="AI Engineer", requirements=[JobRequirement(text="Python")])
    fit_assessment = FitAssessment(fit_score=82.0, essential_score=82.0)
    career_assessment = CareerAssessment(
        career_alignment_score=78.0,
        confidence=AlignmentConfidence.HIGH,
        dimensions=[],
        reasoning="Synthetic graph smoke test.",
    )
    recommendation = RecommendationAssessment(
        recommendation=Recommendation.APPLY,
        fit_score=82.0,
        career_alignment_score=78.0,
        career_alignment_confidence=AlignmentConfidence.HIGH,
        rule_id="strong_fit_strong_alignment",
        reasoning="Synthetic graph smoke test.",
    )

    class FakeJobAnalysisService:
        def analyse_text(self, job_text: str) -> JobProfile:
            calls.append("extract_job")
            assert job_text == "Synthetic job description"
            return job_profile

    class FakeRequirementMatchingService:
        def match(
            self,
            received_job: JobProfile,
            received_candidate: CandidateContext,
        ) -> RequirementMatchSet:
            calls.append("match_requirements")
            assert received_job is job_profile
            assert received_candidate is candidate
            return RequirementMatchSet(matches=[])

    class FakeFitAssessmentService:
        def assess(self, matches: list[RequirementMatch]) -> FitAssessment:
            calls.append("assess_fit")
            assert matches == []
            return fit_assessment

    class FakeCareerAssessmentService:
        def assess(
            self,
            received_job: JobProfile,
            received_candidate: CandidateContext,
            received_fit: FitAssessment,
        ) -> CareerAssessment:
            calls.append("assess_career_alignment")
            assert received_job is job_profile
            assert received_candidate is candidate
            assert received_fit is fit_assessment
            return career_assessment

    class FakeRecommendationService:
        def assess(
            self,
            received_fit: FitAssessment,
            received_career: CareerAssessment,
        ) -> RecommendationAssessment:
            calls.append("build_recommendation")
            assert received_fit is fit_assessment
            assert received_career is career_assessment
            return recommendation

    graph = CareerAnalysisGraph(
        job_analysis_service=FakeJobAnalysisService(),  # type: ignore[arg-type]
        requirement_matching_service=(
            FakeRequirementMatchingService()  # type: ignore[arg-type]
        ),
        fit_assessment_service=FakeFitAssessmentService(),  # type: ignore[arg-type]
        career_assessment_service=(
            FakeCareerAssessmentService()  # type: ignore[arg-type]
        ),
        recommendation_service=FakeRecommendationService(),  # type: ignore[arg-type]
    )

    state = graph.invoke(
        job_text="Synthetic job description",
        candidate_context=candidate,
    )

    assert calls == [
        "extract_job",
        "match_requirements",
        "assess_fit",
        "assess_career_alignment",
        "build_recommendation",
    ]
    assert state["job_profile"] is job_profile
    assert state["requirement_matches"] == []
    assert state["fit_assessment"] is fit_assessment
    assert state["career_assessment"] is career_assessment
    assert state["recommendation_assessment"] is recommendation


def test_graph_stops_after_zero_requirement_extraction() -> None:
    calls: list[str] = []

    class EmptyExtractor:
        def analyse_text(self, _: str) -> JobProfile:
            calls.append("extract")
            return JobProfile(title="Incomplete", requirements=[])

    class MustNotRun:
        def __getattr__(self, _name: str):
            raise AssertionError("Deep scoring must not run for zero extracted requirements.")

    graph = CareerAnalysisGraph(
        job_analysis_service=EmptyExtractor(),  # type: ignore[arg-type]
        requirement_matching_service=MustNotRun(),  # type: ignore[arg-type]
        fit_assessment_service=MustNotRun(),  # type: ignore[arg-type]
        career_assessment_service=MustNotRun(),  # type: ignore[arg-type]
        recommendation_service=MustNotRun(),  # type: ignore[arg-type]
    )

    state = graph.invoke(job_text="Role text", candidate_context=CandidateContext())
    assert calls == ["extract"]
    assert state["job_profile"].requirements == []
    assert "fit_assessment" not in state

from types import SimpleNamespace
from uuid import uuid4

from langchain_core.callbacks import CallbackManager
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import run_helpers

from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.discovery import JobListing
from app.schemas.job import JobProfile, JobRequirement
from app.schemas.matching import RequirementMatch, RequirementMatchSet
from app.schemas.recommendation import (
    Recommendation,
    RecommendationAssessment,
)
from app.workflows.career_analysis_graph import CareerAnalysisGraph


class _NoopLangChainTracer(LangChainTracer):
    """Keep the callback-boundary test entirely local (no LangSmith network)."""

    def _persist_run_single(self, run) -> None:  # type: ignore[no-untyped-def]
        return None


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


def test_match_node_bridges_langgraph_callback_parent_for_attempt_spans() -> None:
    observed_parent_names: list[str | None] = []
    candidate = CandidateContext(source_name="synthetic_candidate")
    job_profile = JobProfile(title="AI Engineer", requirements=[JobRequirement(text="Python")])

    class MatchingService:
        def match(self, _job: JobProfile, _candidate: CandidateContext) -> RequirementMatchSet:
            parent = run_helpers.get_current_run_tree()
            observed_parent_names.append(parent.name if parent else None)
            return RequirementMatchSet(matches=[])

    graph = CareerAnalysisGraph.__new__(CareerAnalysisGraph)
    graph._requirement_matching_service = MatchingService()  # type: ignore[attr-defined]

    tracer = _NoopLangChainTracer(project_name="synthetic", client=SimpleNamespace())
    parent_id = uuid4()
    tracer.on_chain_start({}, {}, run_id=parent_id, name="match_requirements")
    config = {"callbacks": CallbackManager([tracer], parent_run_id=parent_id)}

    graph._match_requirements(  # type: ignore[attr-defined]
        {"job_profile": job_profile, "candidate_context": candidate},
        config,
    )

    assert observed_parent_names == ["match_requirements"]


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


def test_graph_merges_known_listing_metadata_before_matching_and_career_alignment() -> None:
    received_profiles: list[JobProfile] = []

    class Extractor:
        def analyse_text(self, _: str) -> JobProfile:
            return JobProfile(
                title="Description title",
                company="Description company",
                location=None,
                work_arrangement="unknown",
                employment_type=None,
                seniority="Senior",
                responsibilities=["Build reliable systems."],
                requirements=[JobRequirement(text="Python")],
            )

    class Matching:
        def match(self, profile: JobProfile, _: CandidateContext) -> RequirementMatchSet:
            received_profiles.append(profile)
            return RequirementMatchSet(matches=[])

    class Fit:
        def assess(self, _: list[RequirementMatch]) -> FitAssessment:
            return FitAssessment(fit_score=70.0, essential_score=70.0)

    class Career:
        def assess(self, profile: JobProfile, _: CandidateContext, __: FitAssessment) -> CareerAssessment:
            received_profiles.append(profile)
            return CareerAssessment(career_alignment_score=70.0, confidence=AlignmentConfidence.HIGH, dimensions=[], reasoning="Synthetic.")

    class RecommendationServiceFake:
        def assess(self, fit: FitAssessment, career: CareerAssessment) -> RecommendationAssessment:
            return RecommendationAssessment(
                recommendation=Recommendation.CONSIDER,
                fit_score=fit.fit_score,
                career_alignment_score=career.career_alignment_score,
                career_alignment_confidence=career.confidence,
                rule_id="synthetic",
                reasoning="Synthetic.",
            )

    listing = JobListing(
        source="external",
        title="Known vacancy title",
        company="Known employer",
        location="Remote - UK",
        work_arrangement="Remote",
        employment_type="Permanent",
        url="https://jobs.example.test/known",
        description="Canonical semantic description.",
    )
    graph = CareerAnalysisGraph(
        job_analysis_service=Extractor(),  # type: ignore[arg-type]
        requirement_matching_service=Matching(),  # type: ignore[arg-type]
        fit_assessment_service=Fit(),  # type: ignore[arg-type]
        career_assessment_service=Career(),  # type: ignore[arg-type]
        recommendation_service=RecommendationServiceFake(),  # type: ignore[arg-type]
    )

    state = graph.invoke(job_text=listing.description or "", candidate_context=CandidateContext(), job_listing=listing)

    profile = state["job_profile"]
    assert profile.title == "Known vacancy title"
    assert profile.company == "Known employer"
    assert profile.location == "Remote - UK"
    assert profile.work_arrangement == "Remote"
    assert profile.employment_type == "Permanent"
    assert profile.seniority == "Senior"
    assert profile.requirements == [JobRequirement(text="Python")]
    assert received_profiles == [profile, profile]


def test_metadata_merge_fills_listing_gaps_and_listing_conflicts_win_deterministically() -> None:
    extracted = JobProfile(
        title="Extracted title",
        company="Extracted company",
        location="Edinburgh",
        work_arrangement="Hybrid",
        employment_type="Contract",
    )
    listing = JobListing(
        source="external",
        title="Known title",
        company="Known company",
        location=" ",
        work_arrangement="Unspecified",
        employment_type=None,
        url="https://jobs.example.test/metadata",
    )

    merged = CareerAnalysisGraph._merge_known_listing_metadata(extracted, listing)

    assert merged.title == "Known title"
    assert merged.company == "Known company"
    assert merged.location == "Edinburgh"
    assert merged.work_arrangement == "Hybrid"
    assert merged.employment_type == "Contract"

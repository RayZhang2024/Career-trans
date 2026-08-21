from pathlib import Path

from fastapi.testclient import TestClient

from app.api.deps import get_demo_analysis_workflow
from app.main import app as fastapi_app
from app.schemas.demo import DemoAnalyseAndMatchResponse
from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)
from app.schemas.matching import MatchType, RequirementMatch, RequirementMatchSet
from app.schemas.recommendation import (
    Recommendation,
    RecommendationAssessment,
)
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import (
    AlignmentConfidence,
    CareerAlignmentDimension,
    CareerAssessment,
    CareerDimensionAssessment,
)
from app.workflows.demo_analysis import DemoAnalysisWorkflow


SAMPLE_JOB_TEXT = """
Acme AI is hiring an AI Solutions Engineer in London. The role requires
professional Python experience and experience working directly with customers.
React experience is desirable, and candidates must have the right to work in
the UK.
"""


class FakeDemoAnalysisWorkflow:
    def run(self, job_text: str) -> DemoAnalyseAndMatchResponse:
        assert "AI Solutions Engineer" in job_text
        python_requirement = JobRequirement(
            text="Professional Python experience",
            importance=RequirementImportance.ESSENTIAL,
            category=RequirementCategory.TECHNICAL,
        )
        react_requirement = JobRequirement(
            text="React experience",
            importance=RequirementImportance.DESIRABLE,
            category=RequirementCategory.TECHNICAL,
        )
        job_profile = JobProfile(
            title="AI Solutions Engineer",
            company="Acme AI",
            location="London",
            requirements=[python_requirement, react_requirement],
            technical_skills=["Python", "React"],
        )
        return DemoAnalyseAndMatchResponse(
            candidate_source="ray_demo",
            evidence_count=12,
            job_profile=job_profile,
            matches=[
                RequirementMatch(
                    requirement_index=0,
                    requirement=python_requirement,
                    match_type=MatchType.DEMONSTRATED,
                    score=0.9,
                    evidence_ids=["ISIS-NEAT-001"],
                    reasoning="Direct Python software-development evidence exists.",
                ),
                RequirementMatch(
                    requirement_index=1,
                    requirement=react_requirement,
                    match_type=MatchType.MISSING,
                    score=0.1,
                    evidence_ids=[],
                    reasoning="No demonstrated React project evidence is available.",
                ),
            ],
            fit_assessment=FitAssessment(
                fit_score=75.0,
                essential_score=90.0,
                desirable_score=30.0,
                strengths=[0],
                gaps=[],
                hard_blockers=[],
            ),
            career_assessment=CareerAssessment(
                career_alignment_score=82.0,
                confidence=AlignmentConfidence.MEDIUM,
                dimensions=[
                    CareerDimensionAssessment(
                        dimension=dimension,
                        score=0.82,
                        reasoning="The role supports the supplied career direction.",
                    )
                    for dimension in CareerAlignmentDimension
                ],
                strategic_strengths=["Builds target capabilities."],
                strategic_tradeoffs=["Some preferences are not stated."],
                reasoning="Strategically useful based on supplied goals.",
            ),
            recommendation_assessment=RecommendationAssessment(
                recommendation=Recommendation.CONSIDER,
                fit_score=75.0,
                career_alignment_score=82.0,
                career_alignment_confidence=AlignmentConfidence.MEDIUM,
                rule_id="mixed_fit_alignment",
                reasoning="This mixed case requires human judgement.",
                key_strengths=["Builds target capabilities."],
                key_tradeoffs=["Some preferences are not stated."],
            ),
        )


def test_demo_analyse_and_match_returns_end_to_end_result(
    client: TestClient,
) -> None:
    fake_workflow = FakeDemoAnalysisWorkflow()
    fastapi_app.dependency_overrides[get_demo_analysis_workflow] = lambda: fake_workflow

    try:
        response = client.post(
            "/api/v1/demo/analyse-and-match",
            json={"job_text": SAMPLE_JOB_TEXT},
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_demo_analysis_workflow, None)

    assert response.status_code == 200
    body = response.json()
    assert body["candidate_source"] == "ray_demo"
    assert body["evidence_count"] == 12
    assert body["job_profile"]["title"] == "AI Solutions Engineer"
    assert len(body["matches"]) == 2
    assert body["matches"][0]["match_type"] == "demonstrated"
    assert body["matches"][1]["match_type"] == "missing"

    fit_assessment = body["fit_assessment"]

    assert fit_assessment["fit_score"] >= 0
    assert fit_assessment["fit_score"] <= 100
    assert fit_assessment["essential_score"] >= 0
    assert "gaps" in fit_assessment
    assert "hard_blockers" in fit_assessment

    career_assessment = body["career_assessment"]
    assert career_assessment["career_alignment_score"] == 82.0
    assert career_assessment["confidence"] == "medium"
    assert len(career_assessment["dimensions"]) == 6

    recommendation = body["recommendation_assessment"]
    assert recommendation["recommendation"] == "consider"
    assert recommendation["fit_score"] == 75.0
    assert recommendation["career_alignment_score"] == 82.0
    assert recommendation["rule_id"] == "mixed_fit_alignment"


def test_demo_analyse_and_match_rejects_short_job_text(client: TestClient) -> None:
    response = client.post(
        "/api/v1/demo/analyse-and-match",
        json={"job_text": "Too short"},
    )

    assert response.status_code == 422


def test_demo_workflow_runs_recommendation_after_assessments(tmp_path: Path) -> None:
    candidate = CandidateContext(
        source_name="synthetic_demo",
        career_strategy_text=(
            "Build production software leadership capability through ownership "
            "of customer-facing systems and progressively broader responsibility."
        ),
        job_search_criteria_text=(
            "Seek a permanent hybrid role with learning and progression."
        ),
    )
    job_profile = JobProfile(title="Software Lead")
    fit_assessment = FitAssessment(fit_score=55.0, essential_score=55.0)
    career_assessment = CareerAssessment(
        career_alignment_score=80.0,
        confidence=AlignmentConfidence.MEDIUM,
        dimensions=[
            CareerDimensionAssessment(
                dimension=dimension,
                score=0.8,
                reasoning="Synthetic workflow assertion.",
            )
            for dimension in CareerAlignmentDimension
        ],
        reasoning="Synthetic workflow assertion.",
    )
    recommendation_assessment = RecommendationAssessment(
        recommendation=Recommendation.CONSIDER,
        fit_score=55.0,
        career_alignment_score=80.0,
        career_alignment_confidence=AlignmentConfidence.MEDIUM,
        rule_id="mixed_fit_alignment",
        reasoning="Synthetic workflow assertion.",
    )

    class FakeLoader:
        def load(self, profile_dir: Path) -> CandidateContext:
            assert profile_dir == tmp_path
            return candidate

    class FakeJobService:
        def analyse_text(self, job_text: str) -> JobProfile:
            assert job_text == SAMPLE_JOB_TEXT
            return job_profile

    class FakeMatchingService:
        def match(
            self,
            received_job: JobProfile,
            received_candidate: CandidateContext,
        ) -> RequirementMatchSet:
            assert received_job is job_profile
            assert received_candidate is candidate
            return RequirementMatchSet(matches=[])

    class FakeFitService:
        def assess(self, matches: list[RequirementMatch]) -> FitAssessment:
            assert matches == []
            return fit_assessment

    class FakeCareerService:
        def assess(
            self,
            received_job: JobProfile,
            received_candidate: CandidateContext,
            received_fit: FitAssessment,
        ) -> CareerAssessment:
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
            assert received_fit is fit_assessment
            assert received_career is career_assessment
            return recommendation_assessment

    workflow = DemoAnalysisWorkflow(
        job_analysis_service=FakeJobService(),  # type: ignore[arg-type]
        requirement_matching_service=FakeMatchingService(),  # type: ignore[arg-type]
        fit_assessment_service=FakeFitService(),  # type: ignore[arg-type]
        career_assessment_service=FakeCareerService(),  # type: ignore[arg-type]
        recommendation_service=FakeRecommendationService(),  # type: ignore[arg-type]
        candidate_loader=FakeLoader(),  # type: ignore[arg-type]
        profile_dir=tmp_path,
    )

    response = workflow.run(SAMPLE_JOB_TEXT)

    assert response.fit_assessment is fit_assessment
    assert response.career_assessment is career_assessment
    assert response.recommendation_assessment is recommendation_assessment

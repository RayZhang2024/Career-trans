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
from app.schemas.matching import MatchType, RequirementMatch


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


def test_demo_analyse_and_match_rejects_short_job_text(client: TestClient) -> None:
    response = client.post(
        "/api/v1/demo/analyse-and-match",
        json={"job_text": "Too short"},
    )

    assert response.status_code == 422

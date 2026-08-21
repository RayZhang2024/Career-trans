from fastapi.testclient import TestClient

from app.api.deps import get_job_analysis_service
from app.main import app as fastapi_app
from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)
from app.services.job_analysis_service import JobAnalysisService


SAMPLE_JOB_TEXT = """
Acme AI is hiring an AI Solutions Engineer in London. This hybrid role works
with enterprise customers to design and deploy AI applications.

You will translate customer requirements into technical solutions, build Python
prototypes, and collaborate with product and engineering teams.

You must have professional Python experience and experience working directly
with customers. Experience with LLM applications is required. React experience
is desirable. Candidates must have the right to work in the UK.
"""


class FakeJobExtractor:
    def extract(self, job_text: str) -> JobProfile:
        assert "AI Solutions Engineer" in job_text
        return JobProfile(
            title="AI Solutions Engineer",
            company="Acme AI",
            location="London",
            work_arrangement="hybrid",
            responsibilities=[
                "Translate customer requirements into technical solutions.",
                "Build Python prototypes.",
                "Collaborate with product and engineering teams.",
            ],
            requirements=[
                JobRequirement(
                    text="Professional Python experience",
                    importance=RequirementImportance.ESSENTIAL,
                    category=RequirementCategory.TECHNICAL,
                    source_text="must have professional Python experience",
                ),
                JobRequirement(
                    text="Experience working directly with customers",
                    importance=RequirementImportance.ESSENTIAL,
                    category=RequirementCategory.CUSTOMER,
                ),
                JobRequirement(
                    text="React experience",
                    importance=RequirementImportance.DESIRABLE,
                    category=RequirementCategory.TECHNICAL,
                ),
                JobRequirement(
                    text="Right to work in the UK",
                    importance=RequirementImportance.ESSENTIAL,
                    category=RequirementCategory.WORK_AUTHORIZATION,
                ),
            ],
            technical_skills=["Python", "LLM applications", "React"],
            work_authorization_requirements=["Right to work in the UK"],
        )


def test_analyse_job_returns_structured_profile(client: TestClient) -> None:
    fake_service = JobAnalysisService(extractor=FakeJobExtractor())
    fastapi_app.dependency_overrides[get_job_analysis_service] = lambda: fake_service

    try:
        response = client.post(
            "/api/v1/jobs/analyse",
            json={"job_text": SAMPLE_JOB_TEXT},
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_job_analysis_service, None)

    assert response.status_code == 200
    body = response.json()["job_profile"]

    assert body["title"] == "AI Solutions Engineer"
    assert body["company"] == "Acme AI"
    assert body["location"] == "London"
    assert len(body["requirements"]) == 4
    assert body["requirements"][0]["importance"] == "essential"
    assert body["requirements"][2]["importance"] == "desirable"


def test_analyse_job_rejects_too_short_text(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs/analyse",
        json={"job_text": "Too short"},
    )

    assert response.status_code == 422

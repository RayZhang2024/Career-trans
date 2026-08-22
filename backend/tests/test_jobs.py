from fastapi.testclient import TestClient

from app.api.deps import (
    get_ats_resolver_service,
    get_job_analysis_service,
    get_job_ranking_service,
    get_job_discovery_service,
)
from app.schemas.discovery import JobDiscoveryResponse, JobListing, JobSearchQuery
from app.schemas.job_sources import AtsResolutionResponse, CompanySourceResolution, ResolvedJobSource
from app.schemas.job_ranking import JobRankingResponse
from app.main import app as fastapi_app
from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)
from app.services.job_analysis_service import JobAnalysisService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.job_ranking_service import JobRankingService


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


def test_discover_jobs_returns_normalized_fake_provider_results(client: TestClient) -> None:
    class FakeDiscoveryService:
        def discover(self, query: JobSearchQuery) -> JobDiscoveryResponse:
            assert query.keywords == ["AI Solutions Engineer"]
            return JobDiscoveryResponse(
                listings=[
                    JobListing(
                        source="fake",
                        external_id="123",
                        title="AI Solutions Engineer",
                        company="Acme",
                        location="London",
                        url="https://jobs.example.test/123",
                    )
                ],
                provider_counts={"fake": 1},
                raw_count=1,
                deduplicated_count=0,
                screened_out_count=0,
            )

    fastapi_app.dependency_overrides[get_job_discovery_service] = FakeDiscoveryService
    try:
        response = client.post(
            "/api/v1/jobs/discover",
            json={
                "keywords": ["AI Solutions Engineer"],
                "locations": ["London"],
                "remote_ok": True,
            },
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_job_discovery_service, None)

    assert response.status_code == 200
    assert response.json()["provider_counts"] == {"fake": 1}
    assert response.json()["listings"][0]["url"] == "https://jobs.example.test/123"


def test_resolve_job_sources_returns_structured_fake_results(client: TestClient) -> None:
    class FakeResolverService:
        def resolve(self, companies: list[object]) -> AtsResolutionResponse:
            assert len(companies) == 1
            return AtsResolutionResponse(
                results=[
                    CompanySourceResolution(
                        company="Example",
                        resolved=ResolvedJobSource(
                            company="Example",
                            provider="greenhouse",
                            source_token="example",
                            careers_url="https://job-boards.greenhouse.io/example",
                        ),
                        attempted_providers=["greenhouse"],
                    )
                ]
            )

    fastapi_app.dependency_overrides[get_ats_resolver_service] = FakeResolverService
    try:
        response = client.post(
            "/api/v1/jobs/sources/resolve",
            json={"companies": [{"name": "Example"}]},
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_ats_resolver_service, None)

    assert response.status_code == 200
    assert response.json()["results"][0]["resolved"]["provider"] == "greenhouse"


def test_rank_jobs_returns_fake_structured_results(client: TestClient) -> None:
    class FakeRankingService:
        def rank(self, _: object) -> JobRankingResponse:
            return JobRankingResponse(discovered_count=1, gated_out_count=0, relevance_screened_count=1, finalist_count=0, analysed_count=0)

    fastapi_app.dependency_overrides[get_job_ranking_service] = FakeRankingService
    try:
        response = client.post(
            "/api/v1/jobs/rank",
            json={
                "jobs": [{"source": "fake", "title": "Engineer", "url": "https://jobs.example.test/1", "description": "Role description."}],
                "candidate_context": {},
            },
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_job_ranking_service, None)

    assert response.status_code == 200
    assert response.json()["discovered_count"] == 1

import json

from sqlalchemy import select

from app.api.deps import get_agentic_job_discovery_service, get_job_ranking_service
from app.main import app
from app.models.user import User
from app.schemas.agentic_discovery import (
    AgenticDiscoveryDiagnostics,
    AgenticDiscoveryMeRequest,
    AgenticDiscoveryResponse,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.discovery import JobListing
from app.schemas.job_ranking import JobRankingResponse
from app.services.cv_ingestion_service import CVIngestionService


def _auth(client, email: str) -> tuple[dict[str, str], str]:
    credentials = {"email": email, "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, email


def _confirm_context(db_session, email: str, *, evidence_title: str) -> None:
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    data = CandidateCVData.model_validate(
        {
            "skills": [{"name": "Python"}],
            "evidence": [
                {
                    "evidence_type": "project",
                    "title": evidence_title,
                    "text": "Delivered a reliable software system.",
                    "skills": ["Python"],
                }
            ],
        }
    )
    service = CVIngestionService(db_session)
    draft = service.upload(
        user_id,
        [("cv.json", "application/json", json.dumps(data.model_dump(mode="json")).encode())],
    )
    service.interpret(user_id, draft.id)
    service.confirm(user_id, draft.id)


def _payload() -> dict:
    return {
        "query": {"keywords": ["Engineer"], "locations": ["United Kingdom"]},
        "max_search_queries": 1,
        "max_search_results_per_query": 1,
        "max_pages_to_open": 1,
        "max_discovered_jobs": 1,
    }


def test_authenticated_discovery_request_has_no_candidate_context() -> None:
    assert "candidate_context" not in AgenticDiscoveryMeRequest.model_fields


def test_discover_agentic_me_uses_current_users_context_and_flows_to_rank_me(client, db_session) -> None:
    headers_a, email_a = _auth(client, "discovery-a@example.com")
    headers_b, email_b = _auth(client, "discovery-b@example.com")
    headers_c, _ = _auth(client, "discovery-c@example.com")
    _confirm_context(db_session, email_a, evidence_title="First user's evidence")
    _confirm_context(db_session, email_b, evidence_title="Second user's evidence")
    captured_discovery = []
    captured_ranking = []
    listing = JobListing(
        source="agentic_web",
        title="Generic Engineer",
        company="Example",
        url="https://jobs.example.test/roles/1",
        description="Public role description.",
    )

    class FakeDiscoveryService:
        def discover(self, request):
            captured_discovery.append(request)
            return AgenticDiscoveryResponse(
                listings=[listing],
                diagnostics=AgenticDiscoveryDiagnostics(),
            )

    class FakeRankingService:
        def rank(self, request):
            captured_ranking.append(request)
            return JobRankingResponse(
                discovered_count=len(request.jobs),
                gated_out_count=0,
                relevance_screened_count=0,
                finalist_count=0,
                analysed_count=0,
            )

    app.dependency_overrides[get_agentic_job_discovery_service] = FakeDiscoveryService
    app.dependency_overrides[get_job_ranking_service] = FakeRankingService
    try:
        response_a = client.post("/api/v1/jobs/discover-agentic-me", headers=headers_a, json=_payload())
        response_b = client.post("/api/v1/jobs/discover-agentic-me", headers=headers_b, json=_payload())
        assert response_a.status_code == 200
        assert response_b.status_code == 200
        assert [item.title for item in captured_discovery[0].candidate_context.evidence] == ["First user's evidence"]
        assert [item.title for item in captured_discovery[1].candidate_context.evidence] == ["Second user's evidence"]

        ranking = client.post("/api/v1/jobs/rank-me", headers=headers_a, json={"jobs": response_a.json()["listings"]})
        assert ranking.status_code == 200
        assert [item.title for item in captured_ranking[0].candidate_context.evidence] == ["First user's evidence"]

        missing_context = client.post("/api/v1/jobs/discover-agentic-me", headers=headers_c, json=_payload())
        assert missing_context.status_code == 409
        assert "Upload, review and confirm" in missing_context.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_agentic_job_discovery_service, None)
        app.dependency_overrides.pop(get_job_ranking_service, None)


def test_discover_agentic_me_rejects_caller_supplied_context(client, db_session) -> None:
    headers, email = _auth(client, "discovery-schema@example.com")
    _confirm_context(db_session, email, evidence_title="Evidence")
    response = client.post(
        "/api/v1/jobs/discover-agentic-me",
        headers=headers,
        json={**_payload(), "candidate_context": {"profile_text": "not accepted"}},
    )
    assert response.status_code == 422

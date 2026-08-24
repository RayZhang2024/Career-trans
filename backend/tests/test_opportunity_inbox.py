import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.api.deps import get_job_ranking_service
from app.main import app
from app.models.discovered_job import DiscoveredJob
from app.models.user import User
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.job_ranking import JobRankingResponse
from app.services.cv_ingestion_service import CVIngestionService


def _auth_headers(client, email: str = "inbox-user@example.com") -> dict[str, str]:
    credentials = {"email": email, "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _payload(*jobs):
    return {
        "runtime": "codex",
        "jobs": list(jobs),
        "query": {"keywords": ["Engineer"], "locations": ["London"], "max_results": 10},
    }


def _job(*, title: str, url: str) -> dict:
    return {
        "title": title,
        "company": "Example Systems",
        "location": "London, UK",
        "url": url,
        "description": "Build reliable AI systems.",
        "employment_type": "Permanent",
        "provenance": {"source_ref": "public-search", "discovered_via": "web"},
    }


def _confirm_context(db_session, email: str) -> None:
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    data = CandidateCVData.model_validate(
        {"skills": [{"name": "Python"}], "evidence": [{"evidence_type": "project", "title": "Delivery", "text": "Built systems."}]}
    )
    service = CVIngestionService(db_session)
    draft = service.upload(user_id, [("cv.json", "application/json", json.dumps(data.model_dump(mode="json")).encode())])
    service.interpret(user_id, draft.id)
    service.confirm(user_id, draft.id)


def test_inbox_returns_bounded_recent_external_jobs_with_actionable_provenance(client, db_session) -> None:
    headers = _auth_headers(client)
    assert client.post(
        "/api/v1/jobs/import-discovered",
        headers=headers,
        json=_payload(_job(title="Older Engineer", url="https://jobs.example.test/older")),
    ).status_code == 200
    assert client.post(
        "/api/v1/jobs/import-discovered",
        headers=headers,
        json=_payload(_job(title="Newer Engineer", url="https://jobs.example.test/newer")),
    ).status_code == 200
    records = {record.title: record for record in db_session.scalars(select(DiscoveredJob)).all()}
    records["Older Engineer"].last_seen_at = datetime.now(timezone.utc) - timedelta(days=1)
    records["Newer Engineer"].last_seen_at = datetime.now(timezone.utc)
    db_session.commit()

    response = client.get("/api/v1/jobs/inbox?limit=1", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 1
    assert len(body["jobs"]) == 1
    item = body["jobs"][0]
    assert item["job"]["title"] == "Newer Engineer"
    assert item["job"]["company"] == "Example Systems"
    assert item["job"]["location"] == "London, UK"
    assert item["job"]["url"] == "https://jobs.example.test/newer"
    assert item["provenance"] == [
        {"runtime": "codex", "source_ref": "public-search", "discovered_via": "web", "imported_at": item["provenance"][0]["imported_at"]}
    ]


def test_inbox_requires_authentication_and_never_invokes_external_discovery(client) -> None:
    assert client.get("/api/v1/jobs/inbox").status_code == 401


def test_rank_me_can_rerank_inbox_job_with_confirmed_context(client, db_session) -> None:
    headers = _auth_headers(client, "rank-inbox@example.com")
    _confirm_context(db_session, "rank-inbox@example.com")
    assert client.post(
        "/api/v1/jobs/import-discovered",
        headers=headers,
        json=_payload(_job(title="Engineer", url="https://jobs.example.test/1")),
    ).status_code == 200
    captured = []

    class FakeRankingService:
        def rank(self, request):
            captured.append(request)
            return JobRankingResponse(discovered_count=len(request.jobs), gated_out_count=0, relevance_screened_count=0, finalist_count=0, analysed_count=0)

    app.dependency_overrides[get_job_ranking_service] = FakeRankingService
    try:
        inbox = client.get("/api/v1/jobs/inbox", headers=headers).json()
        response = client.post("/api/v1/jobs/rank-me", headers=headers, json={"jobs": [item["job"] for item in inbox["jobs"]]})
        assert response.status_code == 200
        assert [job.title for job in captured[0].jobs] == ["Engineer"]
        assert [evidence.title for evidence in captured[0].candidate_context.evidence] == ["Delivery"]
    finally:
        app.dependency_overrides.pop(get_job_ranking_service, None)


def test_rank_me_still_requires_confirmed_context_for_persisted_inbox_jobs(client) -> None:
    headers = _auth_headers(client, "missing-inbox-context@example.com")
    response = client.post(
        "/api/v1/jobs/rank-me",
        headers=headers,
        json={"jobs": [{"source": "agent_runtime", "title": "Engineer", "url": "https://jobs.example.test/1"}]},
    )
    assert response.status_code == 409
    assert "Upload, review and confirm" in response.json()["detail"]

import json

from sqlalchemy import select

from app.api.deps import get_user_job_ranking_service, get_user_requirement_matching_service
from app.api import deps
from app.main import app
from app.models.candidate_profile import CandidateProfile
from app.models.user import User
from app.schemas.candidate import CandidateContext
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.job_ranking import JobRankingResponse
from app.schemas.matching import RequirementMatchSet
from app.schemas.ai_settings import UserAiSettingsReplace
from app.services.ai_settings_service import AiSettingsService
from app.services.candidate_profile_compaction import candidate_career_profile, candidate_search_profile
from app.services.cv_ingestion_service import CVIngestionService, PersistedCandidateContextLoader


def _auth(client, email: str) -> tuple[dict[str, str], str]:
    credentials = {"email": email, "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    headers = {"Authorization": f"Bearer {client.post('/api/v1/auth/login', json=credentials).json()['access_token']}"}
    return headers, email


def _confirm_context(db_session, email: str, *, evidence_title: str = "Platform delivery") -> str:
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    data = CandidateCVData.model_validate(
        {
            "employment": [{"employer": "Example", "title": "Engineer", "description": "Built systems."}],
            "education": [{"institution": "University", "qualification": "MSc"}],
            "skills": [{"name": "Python"}, {"name": "Systems"}],
            "evidence": [
                {"evidence_type": "employment", "title": evidence_title, "text": "Built reliable Python platforms.", "skills": ["Python"]},
                {"evidence_type": "project", "title": "Systems project", "text": "Delivered distributed systems.", "skills": ["Systems"]},
            ],
        }
    )
    service = CVIngestionService(db_session)
    draft = service.upload(user_id, [("cv.json", "application/json", json.dumps(data.model_dump(mode="json")).encode())])
    service.interpret(user_id, draft.id)
    service.confirm(user_id, draft.id)
    return user_id


def test_confirmed_context_loader_preserves_all_evidence_and_never_falls_back(db_session, client) -> None:
    _, email_a = _auth(client, "persisted-a@example.com")
    _, email_b = _auth(client, "persisted-b@example.com")
    user_a = _confirm_context(db_session, email_a)
    user_b = db_session.scalar(select(User.id).where(User.email == email_b))
    loader = PersistedCandidateContextLoader(db_session)

    db_session.add(
        CandidateProfile(
            user_id=user_a,
            career_goal="Build applied AI systems.",
            job_search_criteria="Prefer hybrid technical delivery roles.",
        )
    )
    db_session.commit()

    context = loader.load_confirmed(user_a)
    assert context is not None
    assert [evidence.title for evidence in context.evidence] == [
        "Platform delivery", "Systems project", "Engineer at Example", "MSc at University"
    ]
    assert context.skills_text == "Python, Systems"
    assert context.career_strategy_text == "Build applied AI systems."
    assert context.job_search_criteria_text == "Prefer hybrid technical delivery roles."
    assert candidate_search_profile(context).career_strategy_text == context.career_strategy_text
    assert candidate_search_profile(context).job_search_criteria_text == context.job_search_criteria_text
    assert candidate_career_profile(context).career_strategy_text == context.career_strategy_text
    assert candidate_career_profile(context).job_search_criteria_text == context.job_search_criteria_text
    assert context.source_name is None
    assert loader.load_confirmed(user_b) is None


def test_match_me_uses_only_current_users_confirmed_context(client, db_session) -> None:
    headers_a, email_a = _auth(client, "match-a@example.com")
    headers_b, email_b = _auth(client, "match-b@example.com")
    headers_c, _ = _auth(client, "match-c@example.com")
    _confirm_context(db_session, email_a)
    _confirm_context(db_session, email_b, evidence_title="Other user's evidence")
    captured = []

    class FakeMatchingService:
        def match(self, _job, candidate_context):
            captured.append(candidate_context)
            return RequirementMatchSet(matches=[])

    app.dependency_overrides[get_user_requirement_matching_service] = FakeMatchingService
    try:
        response = client.post("/api/v1/jobs/match-me", headers=headers_a, json={"job_profile": {"title": "Engineer", "requirements": []}})
        assert response.status_code == 200
        assert [item.title for item in captured[0].evidence] == [
            "Platform delivery", "Systems project", "Engineer at Example", "MSc at University"
        ]
        response = client.post("/api/v1/jobs/match-me", headers=headers_b, json={"job_profile": {"title": "Engineer", "requirements": []}})
        assert response.status_code == 200
        assert [item.title for item in captured[1].evidence] == [
            "Other user's evidence", "Systems project", "Engineer at Example", "MSc at University"
        ]
        missing_context = client.post("/api/v1/jobs/match-me", headers=headers_c, json={"job_profile": {"requirements": []}})
        assert missing_context.status_code == 409
        assert "Upload, review and confirm" in missing_context.json()["detail"]
        assert email_b not in captured[0].profile_text
    finally:
        app.dependency_overrides.pop(get_user_requirement_matching_service, None)


def test_match_me_uses_each_authenticated_users_matching_snapshot_without_leakage(
    client,
    db_session,
    monkeypatch,
) -> None:
    headers_a, email_a = _auth(client, "match-runtime-a@example.com")
    headers_b, email_b = _auth(client, "match-runtime-b@example.com")
    user_a = _confirm_context(db_session, email_a)
    user_b = _confirm_context(db_session, email_b)
    settings_service = AiSettingsService(db_session)
    settings_service.replace(
        user_a,
        UserAiSettingsReplace(expected_revision=0, default_model="gpt-5.6-luna", default_reasoning_effort="low"),
    )
    settings_service.replace(
        user_b,
        UserAiSettingsReplace(expected_revision=0, default_model="gpt-5.6-sol", default_reasoning_effort="high"),
    )

    configurations: list[tuple[str, str | None]] = []

    class FakeMatchingService:
        def match(self, _job, _candidate_context):
            return RequirementMatchSet(matches=[])

    def build_matching_service(_settings, runtime_snapshot):
        operation = runtime_snapshot.operation("requirement_matching")
        configurations.append((operation.model, operation.reasoning_effort.value if operation.reasoning_effort else None))
        return FakeMatchingService()

    monkeypatch.setattr(deps, "_build_requirement_matching_service", build_matching_service)

    payload = {"job_profile": {"title": "Engineer", "requirements": []}}
    response_a = client.post("/api/v1/jobs/match-me", headers=headers_a, json=payload)
    response_b = client.post("/api/v1/jobs/match-me", headers=headers_b, json=payload)

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert configurations == [("gpt-5.6-luna", "low"), ("gpt-5.6-sol", "high")]


def test_rank_me_reuses_existing_ranking_service_with_persisted_context(client, db_session) -> None:
    headers, email = _auth(client, "rank-me@example.com")
    _confirm_context(db_session, email)
    captured = []

    class FakeRankingService:
        def rank(self, request):
            captured.append(request)
            return JobRankingResponse(discovered_count=len(request.jobs), gated_out_count=0, relevance_screened_count=0, finalist_count=0, analysed_count=0)

    app.dependency_overrides[get_user_job_ranking_service] = FakeRankingService
    try:
        response = client.post(
            "/api/v1/jobs/rank-me",
            headers=headers,
            json={"jobs": [{"source": "test", "title": "Engineer", "url": "https://jobs.example.test/1", "description": "A role."}]},
        )
        assert response.status_code == 200
        assert [item.title for item in captured[0].candidate_context.evidence] == [
            "Platform delivery", "Systems project", "Engineer at Example", "MSc at University"
        ]
    finally:
        app.dependency_overrides.pop(get_user_job_ranking_service, None)


def test_context_summary_is_safe_and_user_scoped(client, db_session) -> None:
    headers_a, email_a = _auth(client, "summary-a@example.com")
    headers_b, _ = _auth(client, "summary-b@example.com")
    _confirm_context(db_session, email_a)
    summary_a = client.get("/api/v1/profile/context-summary", headers=headers_a)
    assert summary_a.status_code == 200
    assert summary_a.json() == {
        "ready": True,
        "employment_count": 1,
        "education_count": 1,
        "skill_count": 2,
        "evidence_count": 4,
        "structured_profile_available": True,
        "evidence_materialization_status": "complete",
        "expected_evidence_count": 4,
        "missing_evidence_count": 0,
        "career_strategy_configured": False,
        "job_search_criteria_configured": False,
    }
    summary_b = client.get("/api/v1/profile/context-summary", headers=headers_b).json()
    assert summary_b["ready"] is False
    assert summary_b["career_strategy_configured"] is False
    assert summary_b["job_search_criteria_configured"] is False


def test_context_summary_reports_direction_without_exposing_it(client, db_session) -> None:
    headers, email = _auth(client, "direction-summary@example.com")
    user_id = _confirm_context(db_session, email)
    db_session.add(
        CandidateProfile(
            user_id=user_id,
            career_goal="A user-authored career direction.",
            job_search_criteria="A user-authored search preference.",
        )
    )
    db_session.commit()

    response = client.get("/api/v1/profile/context-summary", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["career_strategy_configured"] is True
    assert body["job_search_criteria_configured"] is True
    assert "user-authored" not in str(body)


def test_direction_text_remains_available_to_compact_search_and_career_profiles() -> None:
    context = CandidateContext(
        career_strategy_text="strategy " * 300,
        job_search_criteria_text="criteria " * 300,
    )

    search = candidate_search_profile(context)
    career = candidate_career_profile(context)

    assert 0 < len(search.career_strategy_text) <= 1_200
    assert 0 < len(search.job_search_criteria_text) <= 1_200
    assert career.career_strategy_text == search.career_strategy_text
    assert career.job_search_criteria_text == search.job_search_criteria_text

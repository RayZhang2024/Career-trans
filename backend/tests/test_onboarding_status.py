from datetime import datetime, timezone

from sqlalchemy import event

from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.models.user import User
from app.core.config import Settings
from app.schemas.cv_ingestion import CandidateCVData
from tests.test_profile import auth_header, register_and_login


def test_onboarding_status_is_user_scoped_and_pure(client, db_session):
    token_a = register_and_login(client, "onboarding-a@example.com")
    token_b = register_and_login(client, "onboarding-b@example.com")
    user_a = db_session.query(User).filter_by(email="onboarding-a@example.com").one()
    db_session.add(CandidateProfile(user_id=user_a.id, headline="Synthetic profile"))
    db_session.add(CandidateStructuredProfile(user_id=user_a.id, structured_json=CandidateCVData().model_dump_json()))
    db_session.commit()
    writes: list[str] = []

    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", observe)
    try:
        response_a = client.get("/api/v1/onboarding/status", headers=auth_header(token_a))
        response_b = client.get("/api/v1/onboarding/status", headers=auth_header(token_b))
    finally:
        event.remove(db_session.bind, "before_cursor_execute", observe)
    assert response_a.status_code == 200
    assert response_a.json()["profile_exists"] is True
    assert response_a.json()["candidate_context_ready"] is True
    assert response_b.json()["profile_exists"] is False
    assert response_b.json()["candidate_context_ready"] is False
    assert writes == []


def test_onboarding_latest_draft_uses_created_time_not_updated_time(client, db_session):
    token = register_and_login(client, "draft-order@example.com")
    user = db_session.query(User).filter_by(email="draft-order@example.com").one()
    older = CandidateCVIngestionDraft(user_id=user.id, state="confirmed", documents_json="[]")
    newer = CandidateCVIngestionDraft(user_id=user.id, state="review_ready", documents_json="[]")
    older.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    newer.created_at = datetime(2025, 2, 1, tzinfo=timezone.utc)
    older.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.add_all([older, newer]); db_session.commit()
    response = client.get("/api/v1/onboarding/status", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["latest_cv_draft"]["id"] == newer.id
    assert response.json()["latest_cv_draft"]["state"] == "review_ready"


def test_default_cors_supports_both_documented_vite_origins():
    origins = str(Settings.model_fields["cors_origins"].default).split(",")
    assert "http://localhost:5173" in origins
    assert "http://127.0.0.1:5173" in origins

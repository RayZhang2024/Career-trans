from datetime import datetime, timezone

import json

from sqlalchemy import event, select

from app.models.candidate_adviser import CandidateAdviserAssessmentRecord, CandidateAdviserClarificationRecord
from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.models.user import User
from app.core.config import Settings
from app.schemas.candidate_adviser import CandidateAdviserIntake
from app.schemas.cv_ingestion import CandidateCVData
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_adviser_service import CandidateAdviserService
from tests.test_profile import auth_header, register_and_login


def _adviser_content() -> dict[str, object]:
    insight = {
        "text": "Synthetic source-grounded assessment.",
        "source_references": [{"source_type": "intake", "reference": "career_direction"}],
    }
    return {
        "professional_positioning": insight,
        "transferable_strengths": [],
        "development_gaps": [],
        "role_hypotheses": [],
        "transition_assessment": insight,
        "open_questions": [],
        "career_strategy_summary": insight,
        "job_search_strategy_summary": insight,
    }


def _assessment_state(db_session, user_id: str) -> CandidateAdviserService:
    data = CandidateCVData.model_validate({
        "employment": [{"employer": "Example", "title": "Engineer", "description": "Built systems."}],
        "evidence": [{"evidence_type": "project", "title": "Delivery", "text": "Delivered a reliable system.", "skills": ["Python"]}],
    })
    db_session.add(CandidateStructuredProfile(user_id=user_id, structured_json=data.model_dump_json()))
    db_session.commit()
    ActiveCandidateEvidenceResolver(db_session).resolve(user_id, data)
    db_session.commit()
    service = CandidateAdviserService(db_session)
    service.save_intake(user_id, CandidateAdviserIntake(career_direction="Applied AI delivery"))
    fingerprint = service.input_fingerprint(user_id)
    db_session.add(CandidateAdviserAssessmentRecord(
        user_id=user_id,
        input_fingerprint=fingerprint,
        status="confirmed",
        assessment_json=json.dumps(_adviser_content()),
    ))
    db_session.commit()
    return service


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


def test_onboarding_status_with_assessment_is_provider_free_read_only_and_uses_exact_fingerprint(client, db_session, monkeypatch):
    token = register_and_login(client, "onboarding-assessment@example.com")
    user = db_session.query(User).filter_by(email="onboarding-assessment@example.com").one()
    service = _assessment_state(db_session, user.id)
    normal = service.input_fingerprint(user.id)
    read_only = service.input_fingerprint(user.id, read_only=True)
    assert normal == read_only
    service.save_intake(user.id, CandidateAdviserIntake(career_direction="Changed direction"))
    assert service.get_assessment(user.id).status == "stale"
    assert service.input_fingerprint(user.id, read_only=True) != normal
    service.save_intake(user.id, CandidateAdviserIntake(career_direction="Applied AI delivery"))
    assert service.input_fingerprint(user.id) == service.input_fingerprint(user.id, read_only=True) == normal
    assert service.get_assessment(user.id).status == "confirmed"
    assert db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)).all()

    # A status route must not construct an LLM client or materialise question
    # rows merely to render a read model.
    monkeypatch.setattr(CandidateAdviserService, "list_clarifications", lambda *_args: (_ for _ in ()).throw(AssertionError("must not materialise clarifications")))
    monkeypatch.setattr("app.api.deps.get_candidate_adviser_service", lambda: (_ for _ in ()).throw(AssertionError("provider-backed dependency must not be used")))
    writes: list[str] = []
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)
    event.listen(db_session.bind, "before_cursor_execute", observe)
    try:
        response = client.get("/api/v1/onboarding/status", headers=auth_header(token))
    finally:
        event.remove(db_session.bind, "before_cursor_execute", observe)
    assert response.status_code == 200
    assert response.json()["adviser"]["assessment_status"] == "confirmed"
    assert writes == []


def test_onboarding_status_counts_only_persisted_confirmed_clarifications_and_never_creates_them(client, db_session):
    token = register_and_login(client, "onboarding-clarification-count@example.com")
    user = db_session.query(User).filter_by(email="onboarding-clarification-count@example.com").one()
    _assessment_state(db_session, user.id)
    for index, state in enumerate(("confirmed", "review_ready", "unanswered")):
        db_session.add(CandidateAdviserClarificationRecord(
            user_id=user.id,
            clarification_id=f"{index + 1:064x}", question_key=f"{index + 4:064x}",
            origin_assessment_fingerprint="a" * 64, question_text=f"Question {index}",
            question_source_references_json="[]", priority_index=index, status=state,
        ))
    db_session.commit()
    before = db_session.scalar(select(CandidateAdviserClarificationRecord.id).where(CandidateAdviserClarificationRecord.user_id == user.id))
    response = client.get("/api/v1/onboarding/status", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["adviser"]["confirmed_clarification_count"] == 1
    assert db_session.scalars(select(CandidateAdviserClarificationRecord).where(CandidateAdviserClarificationRecord.user_id == user.id)).all()
    assert before is not None


def test_onboarding_readiness_is_structured_profile_existence_not_draft_or_profile_completeness(client, db_session):
    token = register_and_login(client, "onboarding-readiness@example.com")
    user = db_session.query(User).filter_by(email="onboarding-readiness@example.com").one()
    db_session.add(CandidateProfile(user_id=user.id))
    db_session.add(CandidateCVIngestionDraft(user_id=user.id, state="review_ready", documents_json="[]"))
    db_session.commit()
    first = client.get("/api/v1/onboarding/status", headers=auth_header(token)).json()
    assert first["profile_exists"] is True
    assert first["candidate_context_ready"] is False
    db_session.add(CandidateStructuredProfile(user_id=user.id, structured_json=CandidateCVData().model_dump_json()))
    db_session.commit()
    second = client.get("/api/v1/onboarding/status", headers=auth_header(token)).json()
    assert second["candidate_context_ready"] is True
    assert second["latest_cv_draft"]["state"] == "review_ready"


def test_onboarding_latest_draft_breaks_equal_timestamp_ties_by_id(client, db_session):
    token = register_and_login(client, "draft-tie@example.com")
    user = db_session.query(User).filter_by(email="draft-tie@example.com").one()
    same_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    low = CandidateCVIngestionDraft(id="10000000-0000-0000-0000-000000000000", user_id=user.id, state="uploaded", documents_json="[]", created_at=same_time)
    high = CandidateCVIngestionDraft(id="f0000000-0000-0000-0000-000000000000", user_id=user.id, state="review_ready", documents_json="[]", created_at=same_time)
    db_session.add_all([low, high]); db_session.commit()
    response = client.get("/api/v1/onboarding/status", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["latest_cv_draft"]["id"] == high.id

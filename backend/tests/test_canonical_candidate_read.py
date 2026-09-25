import json

import pytest
from sqlalchemy import event, select

from app.models.candidate_cv_ingestion import CandidateEvidenceRecord
from app.models.candidate_profile import CandidateProfile
from app.models.user import User
from app.schemas.candidate import CandidateEvidenceMaterializationStatus
from app.schemas.candidate_adviser import CandidateAdviserAssessmentContent, CandidateAdviserIntake
from app.schemas.candidate_read_snapshot import CandidateAdviserReadStatus
from app.schemas.candidate import CandidateContext
from app.schemas.cv_ingestion import CandidateCVData, CVIngestionState
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.canonical_candidate_read_service import (
    CandidateEvidenceMaterializationIncomplete,
    CanonicalCandidateReadService,
)
from app.services.cv_ingestion_service import CVIngestionService
from app.services.user_job_discovery_service import UserJobDiscoveryService


def _auth(client, email: str) -> tuple[dict[str, str], str]:
    credentials = {"email": email, "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, email


def _confirm(db_session, user_id: str, *, data: CandidateCVData | None = None) -> None:
    value = data or CandidateCVData.model_validate(
        {
            "employment": [{"employer": "Example Co", "title": "Engineer", "description": "Built systems."}],
            "education": [{"institution": "Example University", "qualification": "MSc"}],
            "credentials": [{"name": "Cloud Certificate", "credential_type": "certification", "issuer": "Example"}],
            "skills": [{"name": "Python"}],
            "projects": [{"name": "Platform", "description": "Built a platform.", "skills": ["Python"]}],
            "achievements": [{"text": "Delivered a reliable service."}],
            "evidence": [{"evidence_type": "project", "title": "Platform delivery", "text": "Built a reliable service.", "skills": ["Python"]}],
        }
    )
    service = CVIngestionService(db_session)
    draft = service.upload(user_id, [("cv.json", "application/json", json.dumps(value.model_dump(mode="json")).encode())])
    service.interpret(user_id, draft.id)
    service.confirm(user_id, draft.id)


def test_snapshot_composes_typed_current_domains_and_context_projection(db_session, client) -> None:
    headers, email = _auth(client, "snapshot-rich@example.com")
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id
    _confirm(db_session, user_id)
    db_session.add(CandidateProfile(user_id=user_id, headline="Engineer", career_goal="Build useful systems."))
    db_session.commit()

    reader = CanonicalCandidateReadService(db_session)
    snapshot = reader.read(user_id)
    context = reader.candidate_context(snapshot, require_structured_profile=True, require_complete_evidence=True)

    assert snapshot.profile and snapshot.profile.headline == "Engineer"
    assert snapshot.structured_profile is not None
    assert snapshot.structured_profile.credentials[0].name == "Cloud Certificate"
    assert snapshot.structured_profile.projects[0].name == "Platform"
    assert snapshot.structured_profile.achievements[0].text == "Delivered a reliable service."
    assert snapshot.readiness.ready_for_candidate_context is True
    assert snapshot.readiness.evidence_materialization_status is CandidateEvidenceMaterializationStatus.COMPLETE
    assert snapshot.adviser_assessment_status is CandidateAdviserReadStatus.NOT_AVAILABLE
    assert context is not None and context.career_strategy_text == "Build useful systems."
    assert [item.title for item in context.evidence] == [item.title for item in snapshot.active_evidence]
    assert "Cloud Certificate" not in context.profile_text
    assert "Platform delivery" in context.evidence[0].title
    trusted_context = CandidateContext(
        profile_text=(
            "Engineer\nEngineer at Example Co. Built systems.\nMSc at Example University. "
        ),
        skills_text="Python",
        career_strategy_text="Build useful systems.",
        evidence=snapshot.active_evidence,
    )
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(context) == (
        UserJobDiscoveryService.candidate_evaluation_fingerprint(trusted_context)
    )


def test_profile_only_and_structured_only_snapshots_are_readable(db_session, client) -> None:
    profile_headers, profile_email = _auth(client, "snapshot-profile-only@example.com")
    structured_headers, structured_email = _auth(client, "snapshot-structured-only@example.com")
    profile_user = db_session.scalar(select(User.id).where(User.email == profile_email))
    structured_user = db_session.scalar(select(User.id).where(User.email == structured_email))
    assert profile_user and structured_user
    db_session.add(CandidateProfile(user_id=profile_user, display_name="Profile Owner"))
    db_session.commit()
    _confirm(db_session, structured_user)

    profile_snapshot = client.get("/api/v1/profile/snapshot", headers=profile_headers)
    assert profile_snapshot.status_code == 200
    assert profile_snapshot.json()["profile"]["display_name"] == "Profile Owner"
    assert profile_snapshot.json()["structured_profile"] is None
    assert profile_snapshot.json()["readiness"]["ready_for_candidate_context"] is False
    assert profile_snapshot.json()["readiness"]["evidence_materialization_status"] == "not_applicable"

    structured_snapshot = client.get("/api/v1/profile/snapshot", headers=structured_headers)
    assert structured_snapshot.status_code == 200
    assert structured_snapshot.json()["profile"] is None
    assert structured_snapshot.json()["structured_profile"]["employment"][0]["employer"] == "Example Co"
    assert structured_snapshot.json()["readiness"]["ready_for_candidate_context"] is True


def test_incomplete_evidence_is_explicit_and_does_not_write_during_read(db_session, client, monkeypatch) -> None:
    headers, email = _auth(client, "snapshot-incomplete@example.com")
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id
    _confirm(db_session, user_id)
    row = db_session.scalar(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))
    assert row is not None
    db_session.delete(row)
    db_session.commit()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read path attempted reconciliation")

    monkeypatch.setattr(ActiveCandidateEvidenceResolver, "resolve", forbidden)
    monkeypatch.setattr(db_session, "commit", forbidden)
    monkeypatch.setattr(db_session, "flush", forbidden)
    statements: list[str] = []
    connection = db_session.get_bind()
    listener = lambda _conn, _cursor, statement, _params, _context, _many: statements.append(statement.lstrip().split(None, 1)[0].upper())
    event.listen(connection, "before_cursor_execute", listener)
    try:
        response = client.get("/api/v1/profile/snapshot", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["readiness"]["evidence_materialization_status"] == "incomplete"
        assert body["readiness"]["missing_evidence_count"] == 1
        snapshot = CanonicalCandidateReadService(db_session).read(user_id)
        with pytest.raises(CandidateEvidenceMaterializationIncomplete):
            CanonicalCandidateReadService.candidate_context(snapshot, require_complete_evidence=True)
        assert UserJobDiscoveryService(db_session)._current_opportunity_items_read_only(user_id) == []
    finally:
        event.remove(connection, "before_cursor_execute", listener)
    assert not any(kind in {"INSERT", "UPDATE", "DELETE", "REPLACE"} for kind in statements)


def test_review_ready_cv_draft_is_not_projected_as_current_truth(db_session, client) -> None:
    headers, email = _auth(client, "snapshot-review-draft@example.com")
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id
    _confirm(db_session, user_id)
    service = CVIngestionService(db_session)
    replacement = CandidateCVData.model_validate({"employment": [{"employer": "Draft Co", "title": "Draft Role"}]})
    draft = service.upload(user_id, [("new.json", "application/json", json.dumps(replacement.model_dump(mode="json")).encode())])
    service.interpret(user_id, draft.id)

    response = client.get("/api/v1/profile/snapshot", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["structured_profile"]["employment"][0]["employer"] == "Example Co"
    assert body["readiness"]["latest_cv_draft_state"] == CVIngestionState.REVIEW_READY.value


def test_adviser_content_requires_confirmed_current_assessment(db_session, client) -> None:
    headers, email = _auth(client, "snapshot-adviser@example.com")
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id
    _confirm(db_session, user_id)

    class Adviser:
        def assess(self, *, semantic_input):
            from app.schemas.candidate_adviser import CandidateAdviserAssessmentContent

            insight = {
                "text": "Grounded adviser summary.",
                "source_references": [{"source_type": "intake", "reference": "career_direction"}],
            }
            return CandidateAdviserAssessmentContent.model_validate(
                {
                    "professional_positioning": insight,
                    "transferable_strengths": [],
                    "development_gaps": [],
                    "role_hypotheses": [insight],
                    "transition_assessment": insight,
                    "open_questions": [],
                    "career_strategy_summary": insight,
                    "job_search_strategy_summary": insight,
                }
            )

    adviser = CandidateAdviserService(db_session, agent=Adviser())
    adviser.save_intake(user_id, CandidateAdviserIntake(career_direction="Move toward applied AI."))
    adviser.assess(user_id)
    review_ready = CanonicalCandidateReadService(db_session).read(user_id)
    assert review_ready.adviser_assessment_status is CandidateAdviserReadStatus.REVIEW_READY
    assert review_ready.adviser_assessment is None

    adviser.confirm_assessment(user_id)
    confirmed = CanonicalCandidateReadService(db_session).read(user_id)
    assert confirmed.adviser_assessment_status is CandidateAdviserReadStatus.CONFIRMED
    assert confirmed.adviser_assessment is not None

    adviser.save_intake(user_id, CandidateAdviserIntake(career_direction="A different direction."))
    stale = CanonicalCandidateReadService(db_session).read(user_id)
    assert stale.adviser_assessment_status is CandidateAdviserReadStatus.STALE
    assert stale.adviser_assessment is None


def test_context_summary_and_snapshot_api_are_read_only_and_user_scoped(db_session, client, monkeypatch) -> None:
    headers_a, email_a = _auth(client, "snapshot-api-a@example.com")
    headers_b, email_b = _auth(client, "snapshot-api-b@example.com")
    user_a = db_session.scalar(select(User.id).where(User.email == email_a))
    user_b = db_session.scalar(select(User.id).where(User.email == email_b))
    assert user_a and user_b
    _confirm(db_session, user_a)
    _confirm(db_session, user_b, data=CandidateCVData.model_validate({"evidence": [{"evidence_type": "project", "title": "Private B", "text": "Only user B."}]}))
    db_session.add_all([
        CandidateProfile(user_id=user_a, headline="Candidate A"),
        CandidateProfile(user_id=user_b, headline="Candidate B"),
    ])

    class Adviser:
        def assess(self, *, semantic_input):
            text = semantic_input.intake.career_direction
            insight = {
                "text": text,
                "source_references": [{"source_type": "intake", "reference": "career_direction"}],
            }
            return CandidateAdviserAssessmentContent.model_validate(
                {
                    "professional_positioning": insight,
                    "transferable_strengths": [],
                    "development_gaps": [],
                    "role_hypotheses": [],
                    "transition_assessment": insight,
                    "open_questions": [],
                    "career_strategy_summary": insight,
                    "job_search_strategy_summary": insight,
                }
            )

    adviser_a = CandidateAdviserService(db_session, agent=Adviser())
    adviser_b = CandidateAdviserService(db_session, agent=Adviser())
    adviser_a.save_intake(user_a, CandidateAdviserIntake(career_direction="Private Adviser A"))
    adviser_b.save_intake(user_b, CandidateAdviserIntake(career_direction="Private Adviser B"))
    adviser_a.assess(user_a)
    adviser_a.confirm_assessment(user_a)
    adviser_b.assess(user_b)
    adviser_b.confirm_assessment(user_b)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read path attempted reconciliation")

    monkeypatch.setattr(ActiveCandidateEvidenceResolver, "resolve", forbidden)
    monkeypatch.setattr(db_session, "commit", forbidden)
    monkeypatch.setattr(db_session, "flush", forbidden)
    statements: list[str] = []
    connection = db_session.get_bind()
    listener = lambda _conn, _cursor, statement, _params, _context, _many: statements.append(statement.lstrip().split(None, 1)[0].upper())
    event.listen(connection, "before_cursor_execute", listener)
    try:
        summary = client.get("/api/v1/profile/context-summary", headers=headers_a)
        snapshot_a = client.get("/api/v1/profile/snapshot", headers=headers_a)
        snapshot_b = client.get("/api/v1/profile/snapshot", headers=headers_b)
    finally:
        event.remove(connection, "before_cursor_execute", listener)
    assert summary.status_code == snapshot_a.status_code == snapshot_b.status_code == 200
    assert "Private B" not in snapshot_a.text
    assert "Platform delivery" in snapshot_a.text
    assert "Candidate A" in snapshot_a.text and "Candidate B" not in snapshot_a.text
    assert "Private Adviser A" in snapshot_a.text and "Private Adviser B" not in snapshot_a.text
    assert not any(kind in {"INSERT", "UPDATE", "DELETE", "REPLACE"} for kind in statements)

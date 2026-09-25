import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import Base
from app.models.candidate_cv_ingestion import (
    CandidateEvidenceRecord,
    CandidateStructuredProfile,
)
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.candidate_adviser import CandidateAdviserAssessmentRecord
from app.models.discovered_job import DiscoveredJob
from app.models.user_job_discovery import UserJobEvaluation
from app.models.user import User
from app.schemas.candidate_adviser import (
    AdviserInsight,
    CandidateAdviserAssessmentContent,
    CandidateAdviserAssessmentStatus,
    CandidateAdviserIntake,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.profile_revision import (
    EditableCandidateProfileData,
    EditableCandidateStructuredData,
)
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.cv_ingestion_service import CVIngestionService
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.services.profile_revision_service import (
    CandidateProfileRevisionService,
    profile_authority_fingerprint,
    structured_authority_fingerprint,
)


def _auth(client, email: str) -> dict[str, str]:
    credentials = {"email": email, "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _user(db_session, email: str) -> User:
    user = db_session.scalar(select(User).where(User.email == email.lower()))
    assert user is not None
    return user


def _seed_profile(db_session, user_id: str, *, headline: str = "Current headline") -> CandidateProfile:
    profile = CandidateProfile(
        user_id=user_id,
        display_name="Example Person",
        headline=headline,
        career_goal="Build useful systems",
        preferred_email="person@example.test",
    )
    db_session.add(profile)
    db_session.commit()
    return profile


def _seed_structured(db_session, user_id: str, *, skill: str = "Python") -> CandidateStructuredProfile:
    data = CandidateCVData.model_validate({
        "employment": [{"employer": "Example Co", "title": "Engineer"}],
        "skills": [{"name": skill}],
        "evidence": [{
            "evidence_type": "project", "title": "CV claim", "text": "Source-backed claim",
            "skills": ["Python"], "provenance": [{
                "document_sha256": "abc123", "segment_ids": ["abc123:segment-1"],
            }],
        }],
    })
    row = CandidateStructuredProfile(
        user_id=user_id, structured_json=json.dumps(data.model_dump(mode="json"))
    )
    db_session.add(row)
    ActiveCandidateEvidenceResolver(db_session).resolve(user_id, data)
    db_session.commit()
    return row


def _review_revision(client, headers, *, edit_profile=None, edit_structured=None):
    created = client.post("/api/v1/profile/revisions", headers=headers)
    assert created.status_code == 200
    revision = created.json()
    patch = {"expected_revision": revision["revision"]}
    if edit_profile is not None:
        proposed = revision["proposed_profile"] or EditableCandidateProfileData().model_dump(mode="json")
        edit_profile(proposed)
        patch["proposed_profile"] = proposed
    if edit_structured is not None:
        proposed = revision["proposed_structured"] or EditableCandidateStructuredData().model_dump(mode="json")
        edit_structured(proposed)
        patch["proposed_structured"] = proposed
    assert len(patch) > 1
    saved = client.patch(
        f"/api/v1/profile/revisions/{revision['id']}", headers=headers, json=patch
    )
    assert saved.status_code == 200, saved.text
    reviewed = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/review",
        headers=headers,
        json={"expected_revision": saved.json()["revision"]},
    )
    assert reviewed.status_code == 200, reviewed.text
    return reviewed.json()


def _cv_json(label: str) -> bytes:
    return json.dumps({
        "employment": [{"employer": label, "title": f"{label} Engineer"}],
        "skills": [{"name": f"{label} Skill"}],
        "evidence": [{
            "evidence_type": "project",
            "title": f"{label} source claim",
            "text": f"Evidence from {label}",
            "skills": [f"{label} Skill"],
        }],
    }).encode()


def test_revision_creation_copies_editable_authorities_without_mutating_current_snapshot(client, db_session) -> None:
    headers = _auth(client, "revision-seed@example.com")
    user = _user(db_session, "revision-seed@example.com")
    profile = _seed_profile(db_session, user.id)
    structured = _seed_structured(db_session, user.id)
    before = CanonicalCandidateReadService(db_session).read(user.id).model_dump(mode="json")
    evidence_before = db_session.scalars(
        select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)
    ).all()

    response = client.post("/api/v1/profile/revisions", headers=headers)
    assert response.status_code == 200
    revision = response.json()
    assert revision["state"] == "draft" and revision["revision"] == 1
    assert revision["proposed_profile"]["headline"] == "Current headline"
    assert revision["proposed_structured"]["employment"][0]["employer"] == "Example Co"
    assert "evidence" not in revision["proposed_structured"]
    assert revision["changed_authorities"] == []

    after = CanonicalCandidateReadService(db_session).read(user.id).model_dump(mode="json")
    assert after == before
    assert db_session.get(CandidateProfile, profile.id).headline == "Current headline"
    assert db_session.get(CandidateStructuredProfile, structured.id).structured_json == structured.structured_json
    assert db_session.scalars(
        select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)
    ).all() == evidence_before


@pytest.mark.parametrize(
    ("has_profile", "has_structured", "expect_profile", "expect_structured"),
    [(False, False, False, False), (True, False, True, False), (False, True, False, True), (True, True, True, True)],
)
def test_revision_initialization_supports_all_authority_presence_combinations(
    client, db_session, has_profile, has_structured, expect_profile, expect_structured
) -> None:
    email = f"presence-{has_profile}-{has_structured}@example.com"
    headers = _auth(client, email)
    user = _user(db_session, email)
    if has_profile:
        _seed_profile(db_session, user.id)
    if has_structured:
        _seed_structured(db_session, user.id)
    revision = client.post("/api/v1/profile/revisions", headers=headers).json()
    assert (revision["proposed_profile"] is not None) is expect_profile
    assert (revision["proposed_structured"] is not None) is expect_structured


def test_revision_lifecycle_is_persisted_versioned_and_discard_releases_active_slot(client, db_session) -> None:
    headers = _auth(client, "revision-lifecycle@example.com")
    created = client.post("/api/v1/profile/revisions", headers=headers).json()
    revision_id = created["id"]
    assert client.get("/api/v1/profile/revisions/active", headers=headers).json()["id"] == revision_id
    assert client.post("/api/v1/profile/revisions", headers=headers).json()["id"] == revision_id
    assert client.get("/api/v1/profile/revisions/active", headers=headers).json()["revision"] == 1

    saved = client.patch(
        f"/api/v1/profile/revisions/{revision_id}",
        headers=headers,
        json={"expected_revision": 1, "proposed_profile": {"headline": "Proposed headline"}},
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2 and saved.json()["state"] == "draft"
    assert client.get("/api/v1/profile/revisions/active", headers=headers).json()["proposed_profile"]["headline"] == "Proposed headline"
    stale_write = client.patch(
        f"/api/v1/profile/revisions/{revision_id}",
        headers=headers,
        json={"expected_revision": 1, "proposed_profile": {"headline": "Lost update"}},
    )
    assert stale_write.status_code == 409

    reviewed = client.post(
        f"/api/v1/profile/revisions/{revision_id}/review",
        headers=headers,
        json={"expected_revision": 2},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["state"] == "review_ready" and reviewed.json()["revision"] == 3
    edited = client.patch(
        f"/api/v1/profile/revisions/{revision_id}",
        headers=headers,
        json={"expected_revision": 3, "proposed_profile": {"headline": "Edited after review"}},
    )
    assert edited.status_code == 200
    assert edited.json()["state"] == "draft" and edited.json()["revision"] == 4

    discarded = client.post(
        f"/api/v1/profile/revisions/{revision_id}/discard",
        headers=headers,
        json={"expected_revision": 4},
    )
    assert discarded.status_code == 200
    assert discarded.json()["state"] == "discarded" and discarded.json()["revision"] == 5
    assert client.get("/api/v1/profile/revisions/active", headers=headers).json() is None
    next_revision = client.post("/api/v1/profile/revisions", headers=headers).json()
    assert next_revision["id"] != revision_id
    rows = db_session.scalars(
        select(CandidateProfileRevisionRecord).where(
            CandidateProfileRevisionRecord.user_id == _user(db_session, "revision-lifecycle@example.com").id
        )
    ).all()
    assert len(rows) == 2


def test_revision_optimistic_version_prevents_concurrent_lost_update(client, db_session) -> None:
    headers = _auth(client, "revision-concurrent@example.com")
    user = _user(db_session, "revision-concurrent@example.com")
    created = client.post("/api/v1/profile/revisions", headers=headers).json()
    competing_session = Session(bind=db_session.get_bind(), expire_on_commit=False)
    try:
        stale_revision = competing_session.get(CandidateProfileRevisionRecord, created["id"])
        assert stale_revision is not None and stale_revision.revision == 1

        first_write = CandidateProfileRevisionService(db_session).save(
            user.id,
            created["id"],
            expected_revision=1,
            patch_fields={"proposed_profile"},
            proposed_profile=EditableCandidateProfileData(headline="First write"),
            proposed_structured=None,
        )
        assert first_write.revision == 2

        with pytest.raises(ValueError, match="another request changed"):
            CandidateProfileRevisionService(competing_session).save(
                user.id,
                created["id"],
                expected_revision=1,
                patch_fields={"proposed_profile"},
                proposed_profile=EditableCandidateProfileData(headline="Lost write"),
                proposed_structured=None,
            )
    finally:
        competing_session.close()


def test_concurrent_confirmation_of_absent_profile_is_idempotent(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'revision-confirm-race.db'}")
    Base.metadata.create_all(bind=engine)
    first_session = Session(engine, expire_on_commit=False)
    second_session = Session(engine, expire_on_commit=False)
    try:
        user = User(email="revision-confirm-race@example.com", password_hash="unused")
        first_session.add(user)
        first_session.commit()

        created = CandidateProfileRevisionService(first_session).create_or_resume(user.id)
        saved = CandidateProfileRevisionService(first_session).save(
            user.id,
            created.id,
            expected_revision=created.revision,
            patch_fields={"proposed_profile"},
            proposed_profile=EditableCandidateProfileData(headline="Race winner"),
            proposed_structured=None,
        )
        reviewed = CandidateProfileRevisionService(first_session).review(
            user.id, created.id, expected_revision=saved.revision
        )

        stale_identity = second_session.scalar(
            select(CandidateProfileRevisionRecord).where(
                CandidateProfileRevisionRecord.id == created.id
            )
        )
        assert stale_identity is not None
        assert stale_identity.state == "review_ready"
        assert stale_identity.revision == reviewed.revision

        first_confirm = CandidateProfileRevisionService(first_session).confirm(
            user.id, created.id, expected_revision=reviewed.revision
        )
        assert first_confirm.state == "confirmed"

        # The second session has a stale identity-map object. Confirmation must
        # refresh the locked row, observe the terminal state, and return safely.
        second_confirm = CandidateProfileRevisionService(second_session).confirm(
            user.id, created.id, expected_revision=reviewed.revision
        )
        assert second_confirm.state == "confirmed"
        assert second_confirm.revision == first_confirm.revision

        first_session.expire_all()
        assert len(first_session.scalars(
            select(CandidateProfile).where(CandidateProfile.user_id == user.id)
        ).all()) == 1
        assert first_session.scalar(
            select(CandidateProfile).where(CandidateProfile.user_id == user.id)
        ).headline == "Race winner"
        assert first_session.scalars(
            select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id)
        ).all() == []
        assert first_session.scalars(
            select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)
        ).all() == []
        persisted = first_session.get(CandidateProfileRevisionRecord, created.id)
        assert persisted.state == "confirmed" and persisted.active_user_id is None
    finally:
        first_session.close()
        second_session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_revision_routes_are_authenticated_user_scoped_and_resume_only_own_active_record(client, db_session) -> None:
    first = _auth(client, "revision-owner@example.com")
    second = _auth(client, "revision-other@example.com")
    assert client.get("/api/v1/profile/revisions/active").status_code == 401
    revision_id = client.post("/api/v1/profile/revisions", headers=first).json()["id"]
    assert client.get("/api/v1/profile/revisions/active", headers=second).json() is None
    assert client.post("/api/v1/profile/revisions", headers=second).status_code == 200

    patch = {"expected_revision": 1, "proposed_profile": {"headline": "Intrusion"}}
    assert client.patch(f"/api/v1/profile/revisions/{revision_id}", headers=second, json=patch).status_code == 404
    assert client.post(f"/api/v1/profile/revisions/{revision_id}/review", headers=second, json={"expected_revision": 1}).status_code == 404
    assert client.post(f"/api/v1/profile/revisions/{revision_id}/discard", headers=second, json={"expected_revision": 1}).status_code == 404
    assert client.get("/api/v1/profile/revisions/active", headers=first).json()["id"] == revision_id


def test_revision_edit_schema_rejects_evidence_and_absence_fingerprints_are_distinct() -> None:
    with pytest.raises(ValidationError):
        EditableCandidateStructuredData.model_validate({"evidence": []})
    with pytest.raises(ValidationError):
        EditableCandidateStructuredData.model_validate({"skills": [], "other": "not editable"})

    assert profile_authority_fingerprint(None) != profile_authority_fingerprint(CandidateProfile())
    assert structured_authority_fingerprint(None) != structured_authority_fingerprint(CandidateCVData())


def test_review_checks_only_changed_authorities_and_full_structured_baseline(client, db_session) -> None:
    headers = _auth(client, "revision-authority@example.com")
    user = _user(db_session, "revision-authority@example.com")
    profile = _seed_profile(db_session, user.id)
    structured = _seed_structured(db_session, user.id)

    scalar_revision = client.post("/api/v1/profile/revisions", headers=headers).json()
    scalar_proposal = scalar_revision["proposed_profile"]
    scalar_proposal["headline"] = "Proposed scalar"
    saved_scalar = client.patch(
        f"/api/v1/profile/revisions/{scalar_revision['id']}",
        headers=headers,
        json={"expected_revision": 1, "proposed_profile": scalar_proposal},
    ).json()
    assert saved_scalar["changed_authorities"] == ["profile"]

    # An unrelated current structured-authority update does not stale a scalar-only proposal.
    current_data = CandidateCVData.model_validate_json(structured.structured_json)
    current_data.skills[0].name = "Go"
    structured.structured_json = json.dumps(current_data.model_dump(mode="json"))
    db_session.commit()
    allowed_review = client.post(
        f"/api/v1/profile/revisions/{scalar_revision['id']}/review",
        headers=headers,
        json={"expected_revision": 2},
    )
    assert allowed_review.status_code == 200

    discarded = client.post(
        f"/api/v1/profile/revisions/{scalar_revision['id']}/discard",
        headers=headers,
        json={"expected_revision": 3},
    )
    assert discarded.status_code == 200

    structured_revision = client.post("/api/v1/profile/revisions", headers=headers).json()
    structured_proposal = structured_revision["proposed_structured"]
    structured_proposal["skills"][0]["name"] = "Rust"
    saved_structured = client.patch(
        f"/api/v1/profile/revisions/{structured_revision['id']}",
        headers=headers,
        json={"expected_revision": 1, "proposed_structured": structured_proposal},
    ).json()
    assert saved_structured["changed_authorities"] == ["structured"]

    # A profile-only change is irrelevant to a structured-only proposal.
    profile.headline = "Externally changed scalar"
    db_session.commit()
    allowed_structured_review = client.post(
        f"/api/v1/profile/revisions/{structured_revision['id']}/review",
        headers=headers,
        json={"expected_revision": 2},
    )
    assert allowed_structured_review.status_code == 200
    discarded_structured = client.post(
        f"/api/v1/profile/revisions/{structured_revision['id']}/discard",
        headers=headers,
        json={"expected_revision": 3},
    )
    assert discarded_structured.status_code == 200

    combined_revision = client.post("/api/v1/profile/revisions", headers=headers).json()
    profile_proposal = combined_revision["proposed_profile"]
    profile_proposal["summary"] = "Combined proposal"
    structured_proposal = combined_revision["proposed_structured"]
    structured_proposal["skills"][0]["name"] = "TypeScript"
    combined = client.patch(
        f"/api/v1/profile/revisions/{combined_revision['id']}",
        headers=headers,
        json={"expected_revision": 1, "proposed_profile": profile_proposal, "proposed_structured": structured_proposal},
    ).json()
    assert combined["changed_authorities"] == ["profile", "structured"]
    current_data = CandidateCVData.model_validate_json(structured.structured_json)
    current_data.evidence = []  # Evidence is read-only in the proposal, but part of its stale baseline.
    structured.structured_json = json.dumps(current_data.model_dump(mode="json"))
    db_session.commit()
    stale_read = client.get("/api/v1/profile/revisions/active", headers=headers).json()
    assert stale_read["id"] == combined_revision["id"]
    assert stale_read["stale_authorities"] == ["structured"]
    stale_review = client.post(
        f"/api/v1/profile/revisions/{combined_revision['id']}/review",
        headers=headers,
        json={"expected_revision": 2},
    )
    assert stale_review.status_code == 409
    assert "structured" in stale_review.json()["detail"]
    assert client.get("/api/v1/profile/revisions/active", headers=headers).json()["state"] == "draft"


def test_no_change_review_confirmation_is_idempotent_and_creates_no_authorities(client, db_session) -> None:
    headers = _auth(client, "revision-noop@example.com")
    user = _user(db_session, "revision-noop@example.com")
    before = CanonicalCandidateReadService(db_session).read(user.id).model_dump(mode="json")
    revision = client.post("/api/v1/profile/revisions", headers=headers).json()
    reviewed = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/review",
        headers=headers,
        json={"expected_revision": 1},
    )
    assert reviewed.status_code == 200 and reviewed.json()["changed_authorities"] == []
    assert client.post(
        f"/api/v1/profile/revisions/{revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": reviewed.json()["revision"]},
    ).status_code == 200
    confirmed = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": 1},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["state"] == "confirmed"
    row = db_session.get(CandidateProfileRevisionRecord, revision["id"])
    assert row is not None and row.active_user_id is None and row.confirmed_at is not None
    assert row.discarded_at is None
    assert db_session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user.id)) is None
    assert db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id)) is None
    assert db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)).all() == []
    assert CanonicalCandidateReadService(db_session).read(user.id).model_dump(mode="json") == before
    next_revision = client.post("/api/v1/profile/revisions", headers=headers)
    assert next_revision.status_code == 200 and next_revision.json()["id"] != revision["id"]
    discarded = client.post(
        f"/api/v1/profile/revisions/{next_revision.json()['id']}/discard",
        headers=headers,
        json={"expected_revision": 1},
    )
    assert discarded.status_code == 200
    rejected_discard = client.post(
        f"/api/v1/profile/revisions/{next_revision.json()['id']}/confirm",
        headers=headers,
        json={"expected_revision": 2},
    )
    assert rejected_discard.status_code == 409


def test_confirmation_requires_review_and_expected_revision_without_mutation(client, db_session) -> None:
    headers = _auth(client, "revision-confirm-guards@example.com")
    created = client.post("/api/v1/profile/revisions", headers=headers).json()
    saved = client.patch(
        f"/api/v1/profile/revisions/{created['id']}",
        headers=headers,
        json={
            "expected_revision": 1,
            "proposed_profile": {"headline": "Reviewed headline"},
        },
    )
    assert saved.status_code == 200
    not_reviewed = client.post(
        f"/api/v1/profile/revisions/{created['id']}/confirm",
        headers=headers,
        json={"expected_revision": 2},
    )
    assert not_reviewed.status_code == 409
    reviewed = client.post(
        f"/api/v1/profile/revisions/{created['id']}/review",
        headers=headers,
        json={"expected_revision": 2},
    )
    assert reviewed.status_code == 200
    wrong_version = client.post(
        f"/api/v1/profile/revisions/{created['id']}/confirm",
        headers=headers,
        json={"expected_revision": 2},
    )
    assert wrong_version.status_code == 409
    assert client.get("/api/v1/profile", headers=headers).status_code == 404


def test_empty_user_scalar_confirmation_creates_profile_only_and_updates_context(client, db_session) -> None:
    headers = _auth(client, "revision-empty-profile@example.com")
    user = _user(db_session, "revision-empty-profile@example.com")
    initial_snapshot = client.get("/api/v1/profile/snapshot", headers=headers).json()
    initial_context = CanonicalCandidateReadService.candidate_context(
        CanonicalCandidateReadService(db_session).read(user.id)
    )
    revision = _review_revision(
        client,
        headers,
        edit_profile=lambda proposal: proposal.update(
            headline="Confirmed headline", career_goal="Build reliable software"
        ),
    )
    assert client.get("/api/v1/profile/snapshot", headers=headers).json() == initial_snapshot
    assert CanonicalCandidateReadService.candidate_context(
        CanonicalCandidateReadService(db_session).read(user.id)
    ) == initial_context
    confirmed = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": revision["revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert db_session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user.id)).headline == "Confirmed headline"
    assert db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id)) is None
    assert db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)).all() == []
    after_snapshot = client.get("/api/v1/profile/snapshot", headers=headers).json()
    assert after_snapshot["profile"]["headline"] == "Confirmed headline"
    assert after_snapshot["readiness"]["structured_profile_available"] is False
    after_context = CanonicalCandidateReadService.candidate_context(
        CanonicalCandidateReadService(db_session).read(user.id)
    )
    assert after_context != initial_context


def test_present_all_null_profile_proposal_creates_profile_authority(client, db_session) -> None:
    headers = _auth(client, "revision-null-profile@example.com")
    user = _user(db_session, "revision-null-profile@example.com")
    revision = _review_revision(
        client,
        headers,
        edit_profile=lambda proposal: proposal.clear(),
    )
    assert revision["changed_authorities"] == ["profile"]
    confirmed = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": revision["revision"]},
    )
    assert confirmed.status_code == 200
    profile = db_session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    assert profile is not None and profile.headline is None and profile.career_goal is None


def test_empty_user_structured_confirmation_creates_and_reconciles_only_at_confirmation(client, db_session) -> None:
    headers = _auth(client, "revision-empty-structured@example.com")
    user = _user(db_session, "revision-empty-structured@example.com")
    initial_snapshot = client.get("/api/v1/profile/snapshot", headers=headers).json()
    revision = _review_revision(
        client,
        headers,
        edit_structured=lambda proposal: proposal.update(
            employment=[{"employer": "Confirmed Co", "title": "Engineer"}],
            education=[{"institution": "Confirmed Uni", "qualification": "BSc"}],
            credentials=[{"name": "Secure Cert", "credential_type": "certification"}],
            skills=[{"name": "Python"}],
            projects=[{"name": "Tooling", "description": "Created a tool"}],
            achievements=[{"text": "Reduced delays"}],
        ),
    )
    assert client.get("/api/v1/profile/snapshot", headers=headers).json() == initial_snapshot
    assert db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id)) is None
    confirmed = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": revision["revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert db_session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user.id)) is None
    row = db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id))
    assert row is not None
    data = CandidateCVData.model_validate_json(row.structured_json)
    assert data.employment[0].employer == "Confirmed Co"
    assert data.evidence == []
    snapshot = client.get("/api/v1/profile/snapshot", headers=headers).json()
    assert snapshot["readiness"]["structured_profile_available"] is True
    assert snapshot["readiness"]["ready_for_candidate_context"] is True
    assert {record.evidence_type for record in db_session.scalars(
        select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)
    )} == {"employment", "education", "credential"}
    evidence_text = " ".join(record.text for record in db_session.scalars(
        select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)
    ))
    assert "Tooling" not in evidence_text and "Reduced delays" not in evidence_text and "Python" not in evidence_text


def test_combined_confirmation_preserves_cv_evidence_and_current_context(client, db_session) -> None:
    headers = _auth(client, "revision-combined@example.com")
    user = _user(db_session, "revision-combined@example.com")
    profile = _seed_profile(db_session, user.id)
    structured = _seed_structured(db_session, user.id)
    data_before = CandidateCVData.model_validate_json(structured.structured_json)
    evidence_before = [item.model_dump(mode="json") for item in data_before.evidence]
    context_before = CanonicalCandidateReadService.candidate_context(
        CanonicalCandidateReadService(db_session).read(user.id)
    )
    revision = _review_revision(
        client,
        headers,
        edit_profile=lambda proposal: proposal.update(headline="Promoted headline"),
        edit_structured=lambda proposal: (
            proposal["employment"].append({"employer": "New Co", "title": "Lead"}),
            proposal["education"].append({"institution": "New Uni", "qualification": "MSc"}),
            proposal["credentials"].append({"name": "New Badge", "credential_type": "certification"}),
            proposal["skills"].append({"name": "Rust"}),
            proposal["projects"].append({"name": "Manual Project", "description": "No derived claim"}),
            proposal["achievements"].append({"text": "Manual achievement"}),
        ),
    )
    assert client.get("/api/v1/profile/snapshot", headers=headers).json()["profile"]["headline"] == "Current headline"
    confirmed = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": revision["revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert db_session.get(CandidateProfile, profile.id).headline == "Promoted headline"
    stored = db_session.get(CandidateStructuredProfile, structured.id)
    assert stored is not None
    data_after = CandidateCVData.model_validate_json(stored.structured_json)
    assert [item.model_dump(mode="json") for item in data_after.evidence] == evidence_before
    new_evidence = db_session.scalars(
        select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)
    ).all()
    assert any(row.title == "Lead at New Co" for row in new_evidence)
    assert any(row.title == "MSc at New Uni" for row in new_evidence)
    assert any(row.title == "New Badge" for row in new_evidence)
    assert not any(row.title == "Manual Project" or row.text == "Manual achievement" for row in new_evidence)
    assert CanonicalCandidateReadService.candidate_context(
        CanonicalCandidateReadService(db_session).read(user.id)
    ) != context_before
    assert client.get("/api/v1/profile/snapshot", headers=headers).json()["profile"]["headline"] == "Promoted headline"


def test_confirm_after_review_rechecks_only_changed_authorities(client, db_session) -> None:
    headers = _auth(client, "revision-post-review-stale@example.com")
    user = _user(db_session, "revision-post-review-stale@example.com")
    profile = _seed_profile(db_session, user.id)
    structured = _seed_structured(db_session, user.id)
    revision = _review_revision(
        client,
        headers,
        edit_structured=lambda proposal: proposal["skills"].append({"name": "Go"}),
    )
    # A post-review scalar change is unrelated to this structured-only proposal.
    profile.headline = "Changed after review"
    db_session.commit()
    accepted = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": revision["revision"]},
    )
    assert accepted.status_code == 200, accepted.text

    # A separate structured-only proposal is blocked if its own full baseline changes.
    headers2 = _auth(client, "revision-post-review-stale-2@example.com")
    user2 = _user(db_session, "revision-post-review-stale-2@example.com")
    structured2 = _seed_structured(db_session, user2.id)
    stale = _review_revision(
        client,
        headers2,
        edit_structured=lambda proposal: proposal["skills"].append({"name": "Go"}),
    )
    current = CandidateCVData.model_validate_json(structured2.structured_json)
    current.skills.append(type(current.skills[0])(name="Java"))
    structured2.structured_json = json.dumps(current.model_dump(mode="json"))
    db_session.commit()
    rejected = client.post(
        f"/api/v1/profile/revisions/{stale['id']}/confirm",
        headers=headers2,
        json={"expected_revision": stale["revision"]},
    )
    assert rejected.status_code == 409 and "structured" in rejected.json()["detail"]
    assert client.get("/api/v1/profile/revisions/active", headers=headers2).json()["state"] == "review_ready"


def test_real_cv_confirmation_stales_structured_revision(client, db_session) -> None:
    headers = _auth(client, "revision-cv-stale@example.com")
    user = _user(db_session, "revision-cv-stale@example.com")
    _seed_profile(db_session, user.id)
    cv = CVIngestionService(db_session)
    first = cv.upload(user.id, [("a.json", "application/json", _cv_json("A"))])
    cv.interpret(user.id, first.id)
    cv.confirm(user.id, first.id)
    structured_revision = _review_revision(
        client,
        headers,
        edit_structured=lambda proposal: proposal["skills"].append({"name": "Manual"}),
    )
    second = cv.upload(user.id, [("b.json", "application/json", _cv_json("B"))])
    cv.interpret(user.id, second.id)
    cv.confirm(user.id, second.id)

    stale = client.post(
        f"/api/v1/profile/revisions/{structured_revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": structured_revision["revision"]},
    )
    assert stale.status_code == 409
    assert client.get("/api/v1/profile/revisions/active", headers=headers).json()["id"] == structured_revision["id"]
    current = CandidateCVData.model_validate_json(
        db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id)).structured_json
    )
    assert current.employment[0].employer == "B"


def test_manual_structured_confirmation_then_cv_replacement_preserves_manual_history_and_profile_authority(client, db_session) -> None:
    headers = _auth(client, "revision-manual-then-cv@example.com")
    user = _user(db_session, "revision-manual-then-cv@example.com")
    profile = _seed_profile(db_session, user.id, headline="Manual headline")
    adviser = CandidateAdviserService(db_session)
    adviser.save_intake(user.id, CandidateAdviserIntake(
        career_direction="Lead engineering", work_preferences=["Remote"],
        eligibility={"work_authorisation": ["UK"], "security_clearances": ["Baseline"], "locations": ["London"]},
    ))
    cv = CVIngestionService(db_session)
    first = cv.upload(user.id, [("a.json", "application/json", _cv_json("A"))])
    cv.interpret(user.id, first.id)
    cv.confirm(user.id, first.id)

    manual = _review_revision(
        client,
        headers,
        edit_profile=lambda proposal: proposal.update(display_name="Manual Name", career_goal="Grow into leadership"),
        edit_structured=lambda proposal: proposal.update(
            employment=[{"employer": "Manual Co", "title": "Staff Engineer"}],
            skills=[{"name": "Manual Skill"}],
        ),
    )
    confirmed = client.post(
        f"/api/v1/profile/revisions/{manual['id']}/confirm", headers=headers,
        json={"expected_revision": manual["revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    manual_record = db_session.get(CandidateProfileRevisionRecord, manual["id"])
    assert manual_record.state == "confirmed"
    assert db_session.get(CandidateProfile, profile.id).display_name == "Manual Name"
    current_manual = CandidateCVData.model_validate_json(
        db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id)).structured_json
    )
    assert current_manual.employment[0].employer == "Manual Co"
    assert [item.name for item in current_manual.skills] == ["Manual Skill"]
    intake_before = adviser.get_intake(user.id).model_dump(mode="json")
    eligibility_before = CanonicalCandidateReadService(db_session).read(user.id).eligibility.model_dump(mode="json")
    insight = AdviserInsight(
        text="A confirmed assessment insight.",
        source_references=[{"source_type": "intake", "reference": "career_direction"}],
    )
    assessment = CandidateAdviserAssessmentContent(
        professional_positioning=insight, transferable_strengths=[], development_gaps=[],
        role_hypotheses=[], transition_assessment=insight, open_questions=[],
        career_strategy_summary=insight, job_search_strategy_summary=insight,
    )
    db_session.add(CandidateAdviserAssessmentRecord(
        user_id=user.id, input_fingerprint=adviser.input_fingerprint(user.id),
        status=CandidateAdviserAssessmentStatus.CONFIRMED,
        assessment_json=json.dumps(assessment.model_dump(mode="json")),
    ))
    db_session.commit()
    assert CanonicalCandidateReadService(db_session).read(user.id).adviser_assessment_status.value == "confirmed"

    second = cv.upload(user.id, [("b.json", "application/json", _cv_json("B"))])
    cv.interpret(user.id, second.id)
    cv.confirm(user.id, second.id)

    refreshed = CandidateCVData.model_validate_json(
        db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id)).structured_json
    )
    assert refreshed.employment[0].employer == "B"
    assert refreshed.skills[0].name == "B Skill"
    assert refreshed.evidence[0].title == "B source claim"
    assert CanonicalCandidateReadService(db_session).read(user.id).active_evidence[0].title == "B source claim"
    assert db_session.get(CandidateProfile, profile.id).display_name == "Manual Name"
    assert db_session.get(CandidateProfile, profile.id).career_goal == "Grow into leadership"
    assert db_session.get(CandidateProfile, profile.id).headline == "Manual headline"
    assert adviser.get_intake(user.id).model_dump(mode="json") == intake_before
    assert CanonicalCandidateReadService(db_session).read(user.id).eligibility.model_dump(mode="json") == eligibility_before
    db_session.refresh(manual_record)
    assert manual_record.state == "confirmed"
    assert manual_record.confirmed_at is not None
    assert CanonicalCandidateReadService(db_session).read(user.id).adviser_assessment_status.value == "stale"


def test_candidate_context_and_discovery_fingerprint_follow_only_confirmed_relevant_profile_state(client, db_session) -> None:
    headers = _auth(client, "revision-discovery-fingerprint@example.com")
    user = _user(db_session, "revision-discovery-fingerprint@example.com")
    _seed_profile(db_session, user.id, headline="Original headline")
    _seed_structured(db_session, user.id, skill="Python")

    def fingerprint() -> str:
        snapshot = CanonicalCandidateReadService(db_session).read(user.id)
        context = CanonicalCandidateReadService.candidate_context(snapshot, require_complete_evidence=True)
        assert context is not None
        return UserJobDiscoveryService.candidate_evaluation_fingerprint(context)

    f1 = fingerprint()
    job = DiscoveredJob(
        identity_key="https://example.test/jobs/phase4", source="test", title="Engineer",
        url="https://example.test/jobs/phase4", content_hash="a" * 64, state="active",
        first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
        last_changed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    historical = UserJobEvaluation(
        user_id=user.id, discovered_job_id=job.id, job_content_hash=job.content_hash,
        candidate_evaluation_fingerprint=f1, evaluation_contract_fingerprint="b" * 64,
        job_snapshot_json='{"title":"Engineer"}', evaluation_json='{"score":72}',
    )
    db_session.add(historical)
    db_session.commit()
    history_before = (historical.candidate_evaluation_fingerprint, historical.job_snapshot_json, historical.evaluation_json)

    pending = _review_revision(
        client, headers,
        edit_profile=lambda proposal: proposal.update(headline="Pending headline", career_goal="Pending goal"),
        edit_structured=lambda proposal: proposal["skills"].append({"name": "Pending skill"}),
    )
    pending_context = CanonicalCandidateReadService.candidate_context(CanonicalCandidateReadService(db_session).read(user.id))
    assert pending_context is not None
    assert fingerprint() == f1
    assert "Pending headline" not in pending_context.profile_text
    assert "Pending skill" not in pending_context.skills_text

    structured_confirm = client.post(
        f"/api/v1/profile/revisions/{pending['id']}/confirm", headers=headers,
        json={"expected_revision": pending["revision"]},
    )
    assert structured_confirm.status_code == 200, structured_confirm.text
    f2 = fingerprint()
    assert f2 != f1
    db_session.refresh(historical)
    assert (historical.candidate_evaluation_fingerprint, historical.job_snapshot_json, historical.evaluation_json) == history_before

    relevant_profile = _review_revision(
        client, headers, edit_profile=lambda proposal: proposal.update(job_search_criteria="Remote leadership roles"),
    )
    profile_confirm = client.post(
        f"/api/v1/profile/revisions/{relevant_profile['id']}/confirm", headers=headers,
        json={"expected_revision": relevant_profile["revision"]},
    )
    assert profile_confirm.status_code == 200, profile_confirm.text
    f3 = fingerprint()
    assert f3 != f2

    identity = _review_revision(
        client, headers,
        edit_profile=lambda proposal: proposal.update(display_name="New Name", preferred_email="new@example.test", phone="555-0100"),
    )
    identity_confirm = client.post(
        f"/api/v1/profile/revisions/{identity['id']}/confirm", headers=headers,
        json={"expected_revision": identity["revision"]},
    )
    assert identity_confirm.status_code == 200, identity_confirm.text
    assert fingerprint() == f3
    db_session.refresh(historical)
    assert (historical.candidate_evaluation_fingerprint, historical.job_snapshot_json, historical.evaluation_json) == history_before



def test_scalar_revision_survives_unrelated_real_cv_confirmation(client, db_session) -> None:
    headers = _auth(client, "revision-scalar-survives-cv@example.com")
    user = _user(db_session, "revision-scalar-survives-cv@example.com")
    profile = _seed_profile(db_session, user.id)
    cv = CVIngestionService(db_session)
    first = cv.upload(user.id, [("a.json", "application/json", _cv_json("A"))])
    cv.interpret(user.id, first.id)
    cv.confirm(user.id, first.id)
    scalar_revision = _review_revision(
        client,
        headers,
        edit_profile=lambda proposal: proposal.update(career_goal="New goal"),
    )
    second = cv.upload(user.id, [("b.json", "application/json", _cv_json("B"))])
    cv.interpret(user.id, second.id)
    cv.confirm(user.id, second.id)
    scalar_confirmed = client.post(
        f"/api/v1/profile/revisions/{scalar_revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": scalar_revision["revision"]},
    )
    assert scalar_confirmed.status_code == 200, scalar_confirmed.text
    assert db_session.get(CandidateProfile, profile.id).career_goal == "New goal"
    current = CandidateCVData.model_validate_json(
        db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id)).structured_json
    )
    assert current.employment[0].employer == "B"


def test_confirmation_resolver_failure_rolls_back_all_authorities_evidence_and_revision(client, db_session, monkeypatch) -> None:
    headers = _auth(client, "revision-rollback@example.com")
    user = _user(db_session, "revision-rollback@example.com")
    profile = _seed_profile(db_session, user.id)
    structured = _seed_structured(db_session, user.id)
    revision = _review_revision(
        client,
        headers,
        edit_profile=lambda proposal: proposal.update(headline="Would be promoted"),
        edit_structured=lambda proposal: proposal["skills"].append({"name": "Go"}),
    )
    profile_before = {field: getattr(profile, field) for field in EditableCandidateProfileData.model_fields}
    structured_before = structured.structured_json
    evidence_before = [
        (row.id, row.fingerprint, row.evidence_type, row.title, row.text, row.skills_json, row.provenance_json)
        for row in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id))
    ]
    revision_id = revision["id"]

    def fail_resolution(_self, _user_id, _data):
        raise RuntimeError("forced manual evidence reconciliation failure")

    monkeypatch.setattr(
        "app.services.profile_revision_service.ActiveCandidateEvidenceResolver.resolve",
        fail_resolution,
    )
    with pytest.raises(RuntimeError, match="forced manual evidence"):
        CandidateProfileRevisionService(db_session).confirm(
            user.id, revision_id, expected_revision=revision["revision"]
        )
    db_session.expire_all()
    persisted_profile = db_session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    persisted_structured = db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user.id))
    persisted_revision = db_session.get(CandidateProfileRevisionRecord, revision_id)
    assert {field: getattr(persisted_profile, field) for field in EditableCandidateProfileData.model_fields} == profile_before
    assert persisted_structured.structured_json == structured_before
    evidence_after = [
        (row.id, row.fingerprint, row.evidence_type, row.title, row.text, row.skills_json, row.provenance_json)
        for row in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id))
    ]
    assert evidence_after == evidence_before
    assert persisted_revision.state == "review_ready"
    assert persisted_revision.active_user_id == user.id
    assert persisted_revision.confirmed_at is None


def test_confirmation_is_user_scoped_and_provider_free(client, db_session, monkeypatch) -> None:
    first = _auth(client, "revision-confirm-owner@example.com")
    second = _auth(client, "revision-confirm-other@example.com")
    reviewed = _review_revision(
        client,
        first,
        edit_profile=lambda proposal: proposal.update(headline="Owner only"),
    )
    foreign = client.post(
        f"/api/v1/profile/revisions/{reviewed['id']}/confirm",
        headers=second,
        json={"expected_revision": reviewed["revision"]},
    )
    assert foreign.status_code == 404

    def unexpected(*_args, **_kwargs):
        raise AssertionError("confirmation attempted to resolve a semantic provider")

    monkeypatch.setattr("app.api.deps.resolve_runtime_snapshot", unexpected)
    confirmed = client.post(
        f"/api/v1/profile/revisions/{reviewed['id']}/confirm",
        headers=first,
        json={"expected_revision": reviewed["revision"]},
    )
    assert confirmed.status_code == 200


def test_structured_confirmation_makes_existing_adviser_assessment_stale(client, db_session) -> None:
    headers = _auth(client, "revision-adviser-currentness@example.com")
    user = _user(db_session, "revision-adviser-currentness@example.com")
    _seed_structured(db_session, user.id)
    adviser = CandidateAdviserService(db_session)
    adviser.save_intake(user.id, CandidateAdviserIntake(career_direction="Lead engineering"))
    insight = AdviserInsight(
        text="A confirmed assessment insight.",
        source_references=[{"source_type": "intake", "reference": "career_direction"}],
    )
    content = CandidateAdviserAssessmentContent(
        professional_positioning=insight,
        transferable_strengths=[],
        development_gaps=[],
        role_hypotheses=[],
        transition_assessment=insight,
        open_questions=[],
        career_strategy_summary=insight,
        job_search_strategy_summary=insight,
    )
    fingerprint = adviser.input_fingerprint(user.id)
    db_session.add(CandidateAdviserAssessmentRecord(
        user_id=user.id,
        input_fingerprint=fingerprint,
        status=CandidateAdviserAssessmentStatus.CONFIRMED,
        assessment_json=json.dumps(content.model_dump(mode="json")),
    ))
    db_session.commit()
    before = CanonicalCandidateReadService(db_session).read(user.id)
    assert before.adviser_assessment_status.value == "confirmed"

    revision = _review_revision(
        client,
        headers,
        edit_structured=lambda proposal: proposal["skills"].append({"name": "Go"}),
    )
    confirmed = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": revision["revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    after = CanonicalCandidateReadService(db_session).read(user.id)
    assert after.adviser_assessment_status.value == "stale"


def test_identity_only_profile_confirmation_preserves_adviser_currentness(client, db_session) -> None:
    headers = _auth(client, "revision-adviser-identity@example.com")
    user = _user(db_session, "revision-adviser-identity@example.com")
    _seed_structured(db_session, user.id)
    adviser = CandidateAdviserService(db_session)
    adviser.save_intake(user.id, CandidateAdviserIntake(career_direction="Lead engineering"))
    insight = AdviserInsight(
        text="A current assessment insight.",
        source_references=[{"source_type": "intake", "reference": "career_direction"}],
    )
    content = CandidateAdviserAssessmentContent(
        professional_positioning=insight,
        transferable_strengths=[],
        development_gaps=[],
        role_hypotheses=[],
        transition_assessment=insight,
        open_questions=[],
        career_strategy_summary=insight,
        job_search_strategy_summary=insight,
    )
    fingerprint = adviser.input_fingerprint(user.id)
    db_session.add(CandidateAdviserAssessmentRecord(
        user_id=user.id,
        input_fingerprint=fingerprint,
        status=CandidateAdviserAssessmentStatus.CONFIRMED,
        assessment_json=json.dumps(content.model_dump(mode="json")),
    ))
    db_session.commit()
    assert CanonicalCandidateReadService(db_session).read(user.id).adviser_assessment_status.value == "confirmed"

    revision = _review_revision(
        client,
        headers,
        edit_profile=lambda proposal: proposal.update(
            display_name="New display name", preferred_email="new@example.test"
        ),
    )
    confirmed = client.post(
        f"/api/v1/profile/revisions/{revision['id']}/confirm",
        headers=headers,
        json={"expected_revision": revision["revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert adviser.input_fingerprint(user.id) == fingerprint
    assert CanonicalCandidateReadService(db_session).read(user.id).adviser_assessment_status.value == "confirmed"


def test_revision_read_write_routes_do_not_resolve_provider_runtime(client, db_session, monkeypatch) -> None:
    headers = _auth(client, "revision-provider-free@example.com")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("revision route attempted to resolve a semantic provider")

    monkeypatch.setattr("app.api.deps.resolve_runtime_snapshot", unexpected)
    created = client.post("/api/v1/profile/revisions", headers=headers)
    assert created.status_code == 200
    revision_id = created.json()["id"]
    assert client.get("/api/v1/profile/revisions/active", headers=headers).status_code == 200
    saved = client.patch(
        f"/api/v1/profile/revisions/{revision_id}",
        headers=headers,
        json={"expected_revision": 1, "proposed_profile": {"headline": "Provider free"}},
    )
    assert saved.status_code == 200
    assert client.post(
        f"/api/v1/profile/revisions/{revision_id}/review",
        headers=headers,
        json={"expected_revision": 2},
    ).status_code == 200
    assert client.post(
        f"/api/v1/profile/revisions/{revision_id}/discard",
        headers=headers,
        json={"expected_revision": 3},
    ).status_code == 200
    assert "/api/v1/profile/revisions/{revision_id}/confirm" in app.openapi()["paths"]


def test_database_active_slot_is_unique_per_user(db_session) -> None:
    user = User(email="revision-unique@example.com", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    service = CandidateProfileRevisionService(db_session)
    service.create_or_resume(user.id)
    duplicate = CandidateProfileRevisionRecord(
        user_id=user.id,
        active_user_id=user.id,
        state="draft",
        revision=1,
        base_profile_fingerprint="0" * 64,
        base_structured_fingerprint="0" * 64,
        base_editable_structured_fingerprint="0" * 64,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.main import app
from app.models.candidate_cv_ingestion import (
    CandidateEvidenceRecord,
    CandidateStructuredProfile,
)
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.user import User
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.profile_revision import EditableCandidateProfileData, EditableCandidateStructuredData
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
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
    assert "/api/v1/profile/revisions/{revision_id}/confirm" not in app.openapi()["paths"]


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

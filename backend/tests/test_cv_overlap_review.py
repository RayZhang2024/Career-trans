import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.candidate_cv_ingestion import (
    CandidateCVIngestionDraft,
    CandidateEvidenceRecord,
    CandidateStructuredProfile,
)
from app.models.candidate_cv_overlap_review import CandidateCVOverlapReviewRecord
from app.models.user import User
from app.schemas.cv_ingestion import CandidateCVData, Employment, Skill
from app.schemas.application_preparation import ApplicationPrepareRequest, ApplicationTargetInput
from app.schemas.cv_overlap_review import (
    CVOverlapResolution,
    CVOverlapResolutionAction as Action,
    CVOverlapReviewPatch,
)
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.candidate_structured_item_lineage import CandidateStructuredItemLineageService
from app.services.cv_ingestion_service import CVIngestionService
from app.services.cv_overlap_review_service import (
    CVOverlapInvalidResolution,
    CVOverlapReviewRequired,
    CVOverlapReviewService,
    CVOverlapReviewStale,
)
from app.services.application_preparation_service import ApplicationPreparationService
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.services.structured_profile_identity import structured_profile_item_fingerprint
from app.schemas.structured_profile import StructuredItemRelationship, StructuredItemSourceKind


def _user(session, email="overlap@example.test"):
    user = User(email=email, password_hash="unused")
    session.add(user)
    session.commit()
    return user.id


def _current(session, user_id, data):
    row = CandidateStructuredProfile(user_id=user_id, structured_json=data.model_dump_json())
    session.add(row)
    ActiveCandidateEvidenceResolver(session).resolve(user_id, data)
    session.commit()
    return row


def _draft(session, user_id, data):
    row = CandidateCVIngestionDraft(
        user_id=user_id, state="review_ready", documents_json="[]",
        merged_json=data.model_dump_json(),
    )
    session.add(row)
    session.commit()
    return row


def _save_choice(service, user_id, draft_id, item, action, target=None, revision=None):
    current = service.read(user_id, draft_id)
    found = next(value for value in current.items if value.item_key == item or value.incoming_item == item)
    choice = CVOverlapResolution(
        item_key=found.item_key, action=action, target_fingerprint=target,
    )
    return service.update(user_id, draft_id, CVOverlapReviewPatch(
        expected_review_revision=current.revision if revision is None else revision,
        expected_base_structured_fingerprint=current.base_structured_fingerprint,
        expected_draft_fingerprint=current.draft_fingerprint,
        resolutions=[choice],
    ))


def _confirm(session, user_id, draft):
    return CVIngestionService(session).confirm(user_id, draft.id)


def _skills(session, user_id):
    row = session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
    return CandidateCVData.model_validate_json(row.structured_json).skills


def test_new_only_and_exact_or_normalized_reinforcement_confirm_one_item(db_session):
    for suffix, old, incoming, expected in (
        ("new", None, Skill(name="Rust"), StructuredItemRelationship.NEW),
        ("exact", Skill(name="Python"), Skill(name="Python"), StructuredItemRelationship.REINFORCEMENT),
        ("normalized", Skill(name="Python"), Skill(name="  PYTHON  "), StructuredItemRelationship.REINFORCEMENT),
    ):
        user_id = _user(db_session, f"{suffix}@overlap.example.test")
        if old is not None:
            _current(db_session, user_id, CandidateCVData(skills=[old]))
        draft = _draft(db_session, user_id, CandidateCVData(skills=[incoming]))
        _confirm(db_session, user_id, draft)
        assert _skills(db_session, user_id) == [incoming]
        rows = CandidateStructuredItemLineageService(db_session).read_history(user_id)
        event = next(value for value in rows if value.source_kind is StructuredItemSourceKind.CV)
        assert event.relationship is expected
        if old is not None:
            assert event.predecessor_item == old
            assert event.predecessor_fingerprint == structured_profile_item_fingerprint("skills", old)
            assert event.item_fingerprint == structured_profile_item_fingerprint("skills", incoming)


@pytest.mark.parametrize("relationship,before,incoming", [
    (StructuredItemRelationship.REFINEMENT,
     Employment(employer="Acme", title="Engineer", start_date="2021"),
     Employment(employer="Acme", title="Engineer", start_date="Sep 2021")),
    (StructuredItemRelationship.CONFLICT,
     Skill(name="Python", category="Language"), Skill(name="Python", category="Data")),
    (StructuredItemRelationship.AMBIGUOUS,
     [Employment(employer="Acme", title="Engineer", start_date="2021"),
      Employment(employer="Acme", title="Manager", start_date="2021")],
     Employment(employer="Acme", title="Director", start_date="2021")),
])
def test_unresolved_overlap_blocks_without_mutating_canonical_evidence_or_lineage(
    db_session, relationship, before, incoming
):
    user_id = _user(db_session, f"{relationship.value}@overlap.example.test")
    before_data = CandidateCVData(
        employment=before if isinstance(before, list) else ([before] if isinstance(before, Employment) else []),
        skills=[before] if isinstance(before, Skill) else [],
        evidence=[],
    )
    incoming_data = CandidateCVData(
        employment=[incoming] if isinstance(incoming, Employment) else [],
        skills=[incoming] if isinstance(incoming, Skill) else [],
    )
    _current(db_session, user_id, before_data)
    draft = _draft(db_session, user_id, incoming_data)
    canonical_before = CanonicalCandidateReadService(db_session).read(user_id)
    context_before = CanonicalCandidateReadService.candidate_context(canonical_before)
    discovery_before = (
        UserJobDiscoveryService.candidate_evaluation_fingerprint(context_before)
        if context_before is not None else None
    )
    request = ApplicationPrepareRequest(target=ApplicationTargetInput(job_text="x" * 100))
    application_before = ApplicationPreparationService._input_fingerprint(
        {"job": "synthetic"}, {"display_name": "Person"}, {}, canonical_before.structured_profile, request
    )
    evidence_before = list(db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)))
    with pytest.raises(CVOverlapReviewRequired):
        _confirm(db_session, user_id, draft)
    db_session.expire_all()
    assert CandidateCVData.model_validate_json(db_session.scalar(
        select(CandidateStructuredProfile.structured_json).where(CandidateStructuredProfile.user_id == user_id)
    )) == before_data
    assert db_session.get(CandidateCVIngestionDraft, draft.id).state == "review_ready"
    assert db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all() == evidence_before
    assert CandidateStructuredItemLineageService(db_session).read_history(user_id) == []
    canonical_after = CanonicalCandidateReadService(db_session).read(user_id)
    assert canonical_after == canonical_before
    context_after = CanonicalCandidateReadService.candidate_context(canonical_after)
    assert context_after == context_before
    if context_before is not None:
        assert UserJobDiscoveryService.candidate_evaluation_fingerprint(context_after) == discovery_before
    assert ApplicationPreparationService._input_fingerprint(
        {"job": "synthetic"}, {"display_name": "Person"}, {}, canonical_after.structured_profile, request
    ) == application_before
    review = CVOverlapReviewService(db_session).read(user_id, draft.id)
    assert review.items[0].relationship is relationship
    assert review.items[0].resolution_required


@pytest.mark.parametrize("action,expected,write_lineage", [
    (Action.REPLACE_CURRENT, Employment(employer="Acme", title="Engineer", start_date="Sep 2021"), True),
    (Action.KEEP_CURRENT, Employment(employer="Acme", title="Engineer", start_date="2021"), False),
])
def test_unique_refinement_resolution_replace_or_keep(db_session, action, expected, write_lineage):
    user_id = _user(db_session, f"unique-{action.value}@overlap.example.test")
    old = Employment(employer="Acme", title="Engineer", start_date="2021")
    incoming = Employment(employer="Acme", title="Engineer", start_date="Sep 2021")
    _current(db_session, user_id, CandidateCVData(employment=[old], skills=[Skill(name="Unrelated")]))
    draft = _draft(db_session, user_id, CandidateCVData(employment=[incoming]))
    service = CVOverlapReviewService(db_session)
    review = service.read(user_id, draft.id)
    item = review.items[0]
    _save_choice(
        service, user_id, draft.id, item.item_key, action,
        target=item.target_fingerprint if action is Action.REPLACE_CURRENT else None,
    )
    _confirm(db_session, user_id, draft)
    result = CandidateCVData.model_validate_json(db_session.scalar(
        select(CandidateStructuredProfile.structured_json).where(CandidateStructuredProfile.user_id == user_id)
    ))
    assert result.employment == [expected]
    assert result.skills == []  # complete-source replacement omits unrelated current items
    history = CandidateStructuredItemLineageService(db_session).read_history(user_id)
    if write_lineage:
        assert history[0].relationship is StructuredItemRelationship.REFINEMENT
        assert history[0].predecessor_item == old
    else:
        assert history == []


@pytest.mark.parametrize("action,expected", [
    (Action.REPLACE_CURRENT, Skill(name="Python", category="Data")),
    (Action.KEEP_CURRENT, Skill(name="Python", category="Language")),
])
def test_unique_conflict_resolution_replace_or_keep(db_session, action, expected):
    user_id = _user(db_session, f"conflict-{action.value}@overlap.example.test")
    old = Skill(name="Python", category="Language")
    incoming = Skill(name="Python", category="Data")
    _current(db_session, user_id, CandidateCVData(skills=[old]))
    draft = _draft(db_session, user_id, CandidateCVData(skills=[incoming]))
    service = CVOverlapReviewService(db_session)
    item = service.read(user_id, draft.id).items[0]
    _save_choice(
        service, user_id, draft.id, item.item_key, action,
        target=item.target_fingerprint if action is Action.REPLACE_CURRENT else None,
    )
    _confirm(db_session, user_id, draft)
    assert _skills(db_session, user_id) == [expected]
    history = CandidateStructuredItemLineageService(db_session).read_history(user_id)
    if action is Action.REPLACE_CURRENT:
        assert history[0].relationship is StructuredItemRelationship.CONFLICT
        assert history[0].predecessor_item == old
    else:
        assert history == []


@pytest.mark.parametrize("action", [Action.REPLACE_CURRENT, Action.ADD_AS_NEW, Action.SKIP_INCOMING])
def test_ambiguous_resolution_requires_explicit_action_and_never_picks_first(db_session, action):
    user_id = _user(db_session, f"ambig-{action.value}@overlap.example.test")
    first = Employment(employer="Acme", title="Engineer", start_date="2021")
    second = Employment(employer="Acme", title="Manager", start_date="2021")
    incoming = Employment(employer="Acme", title="Director", start_date="2021")
    _current(db_session, user_id, CandidateCVData(employment=[first, second]))
    draft = _draft(db_session, user_id, CandidateCVData(employment=[incoming]))
    service = CVOverlapReviewService(db_session)
    review = service.read(user_id, draft.id)
    item = review.items[0]
    target = item.candidate_matches[-1].fingerprint if action is Action.REPLACE_CURRENT else None
    _save_choice(service, user_id, draft.id, item.item_key, action, target=target)
    _confirm(db_session, user_id, draft)
    result = CandidateCVData.model_validate_json(db_session.scalar(
        select(CandidateStructuredProfile.structured_json).where(CandidateStructuredProfile.user_id == user_id)
    ))
    if action is Action.SKIP_INCOMING:
        assert result.employment == []
        assert CandidateStructuredItemLineageService(db_session).read_history(user_id) == []
    else:
        assert result.employment == [incoming]
        event = CandidateStructuredItemLineageService(db_session).read_history(user_id)[0]
        assert event.relationship is StructuredItemRelationship.AMBIGUOUS
        if action is Action.REPLACE_CURRENT:
            assert event.predecessor_fingerprint == target
            assert event.predecessor_item == second
        else:
            assert event.predecessor_item is None


def test_invalid_target_choice_rejected_and_no_state_change(db_session):
    user_id = _user(db_session)
    old = Skill(name="Python", category="Language")
    incoming = Skill(name="Python", category="Data")
    _current(db_session, user_id, CandidateCVData(skills=[old]))
    draft = _draft(db_session, user_id, CandidateCVData(skills=[incoming]))
    service = CVOverlapReviewService(db_session)
    item = service.read(user_id, draft.id).items[0]
    with pytest.raises(CVOverlapInvalidResolution):
        _save_choice(service, user_id, draft.id, item.item_key, Action.REPLACE_CURRENT, target="a" * 64)
    assert db_session.scalar(select(CandidateCVOverlapReviewRecord).where(
        CandidateCVOverlapReviewRecord.draft_id == draft.id
    )) is None


def test_stale_base_choice_fails_and_editing_draft_invalidates_sidecar(db_session):
    user_id = _user(db_session)
    old = Skill(name="Python", category="Language")
    incoming = Skill(name="Python", category="Data")
    current = _current(db_session, user_id, CandidateCVData(skills=[old]))
    draft = _draft(db_session, user_id, CandidateCVData(skills=[incoming]))
    service = CVOverlapReviewService(db_session)
    review = service.read(user_id, draft.id)
    item = review.items[0]
    _save_choice(service, user_id, draft.id, item.item_key, Action.REPLACE_CURRENT, item.target_fingerprint)
    current.structured_json = CandidateCVData(skills=[Skill(name="Rust")]).model_dump_json()
    db_session.commit()
    with pytest.raises(CVOverlapReviewStale):
        _confirm(db_session, user_id, draft)
    assert _skills(db_session, user_id) == [Skill(name="Rust")]
    db_session.rollback()

    # Restore the original authority, save a fresh choice, then edit the draft.
    current.structured_json = CandidateCVData(skills=[old]).model_dump_json()
    db_session.commit()
    fresh = service.read(user_id, draft.id)
    fresh_item = fresh.items[0]
    _save_choice(service, user_id, draft.id, fresh_item.item_key, Action.REPLACE_CURRENT, fresh_item.target_fingerprint)
    changed_incoming = Skill(name="Python", category="Platform")
    CVIngestionService(db_session).edit_review(
        user_id, draft.id, CandidateCVData(skills=[changed_incoming])
    )
    assert db_session.scalar(select(CandidateCVOverlapReviewRecord).where(
        CandidateCVOverlapReviewRecord.draft_id == draft.id
    )) is None
    with pytest.raises(CVOverlapReviewRequired):
        _confirm(db_session, user_id, draft)
    assert _skills(db_session, user_id) == [old]
    assert CandidateStructuredItemLineageService(db_session).read_history(user_id) == []


@pytest.mark.parametrize("skills", [
    [Skill(name="Python"), Skill(name="Python")],
    [Skill(name="Python"), Skill(name=" PYTHON ")],
])
def test_same_fact_duplicates_inside_one_cv_block_confirmation(db_session, skills):
    user_id = _user(db_session, f"duplicate-{len(skills)}@overlap.example.test")
    draft = _draft(db_session, user_id, CandidateCVData(skills=skills))
    review = CVOverlapReviewService(db_session).read(user_id, draft.id)
    assert len(review.incoming_duplicates) == 1
    with pytest.raises(CVOverlapReviewRequired, match="same-fact duplicate"):
        _confirm(db_session, user_id, draft)
    assert db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )) is None
    assert db_session.get(CandidateCVIngestionDraft, draft.id).state == "review_ready"
    assert CandidateStructuredItemLineageService(db_session).read_history(user_id) == []


def test_review_api_is_provider_free_typed_and_persists_only_choices(client, db_session):
    credentials = {"email": "overlap-api@example.com", "password": "phase-three-overlap-secret!"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = db_session.scalar(select(User.id).where(User.email == credentials["email"]))
    old = Skill(name="Python", category="Language")
    incoming = Skill(name="Python", category="Data")
    _current(db_session, user_id, CandidateCVData(skills=[old]))
    draft = _draft(db_session, user_id, CandidateCVData(skills=[incoming]))
    before = db_session.get(CandidateStructuredProfile, db_session.scalar(
        select(CandidateStructuredProfile.id).where(CandidateStructuredProfile.user_id == user_id)
    )).structured_json
    get = client.get(f"/api/v1/cv-ingestion/{draft.id}/overlap-review", headers=headers)
    assert get.status_code == 200, get.text
    body = get.json()
    assert body["items"][0]["relationship"] == "conflict"
    assert body["items"][0]["incoming_item"]["name"] == "Python"
    patch = client.patch(
        f"/api/v1/cv-ingestion/{draft.id}/overlap-review", headers=headers,
        json={
            "expected_review_revision": 0,
            "expected_base_structured_fingerprint": body["base_structured_fingerprint"],
            "expected_draft_fingerprint": body["draft_fingerprint"],
            "resolutions": [{
            "item_key": body["items"][0]["item_key"], "action": "keep_current",
        }]},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["revision"] == 1
    assert db_session.get(CandidateStructuredProfile, db_session.scalar(
        select(CandidateStructuredProfile.id).where(CandidateStructuredProfile.user_id == user_id)
    )).structured_json == before
    assert CandidateStructuredItemLineageService(db_session).read_history(user_id) == []


def test_review_api_rejects_stale_fingerprints_with_controlled_conflict(client, db_session):
    credentials = {"email": "overlap-api-stale@example.com", "password": "phase-three-overlap-secret!"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = db_session.scalar(select(User.id).where(User.email == credentials["email"]))
    row = _current(db_session, user_id, CandidateCVData(skills=[Skill(name="Python", category="Language")]))
    draft = _draft(db_session, user_id, CandidateCVData(skills=[Skill(name="Python", category="Data")]))
    review = client.get(f"/api/v1/cv-ingestion/{draft.id}/overlap-review", headers=headers).json()
    row.structured_json = CandidateCVData(skills=[Skill(name="Python", category="Platform")]).model_dump_json()
    db_session.commit()
    response = client.patch(
        f"/api/v1/cv-ingestion/{draft.id}/overlap-review", headers=headers,
        json={
            "expected_review_revision": review["revision"],
            "expected_base_structured_fingerprint": review["base_structured_fingerprint"],
            "expected_draft_fingerprint": review["draft_fingerprint"],
            "resolutions": [{"item_key": review["items"][0]["item_key"], "action": "keep_current"}],
        },
    )
    assert response.status_code == 409
    assert "stale" in response.json()["detail"].lower()
    assert "Python" not in response.json()["detail"]
    assert db_session.scalar(select(CandidateCVOverlapReviewRecord).where(
        CandidateCVOverlapReviewRecord.draft_id == draft.id
    )) is None


@pytest.mark.parametrize("existing_sidecar", [False, True])
def test_patch_fingerprint_binding_rejects_stale_get_and_allows_refreshed_recovery(tmp_path, existing_sidecar):
    engine = create_engine(f"sqlite:///{tmp_path / f'cv-overlap-patch-{existing_sidecar}.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as first, Session(engine) as second:
            user_id = _user(first, f"patch-fingerprint-{existing_sidecar}@example.test")
            old = Skill(name="Python", category="Language")
            current = _current(first, user_id, CandidateCVData(skills=[old]))
            draft = _draft(first, user_id, CandidateCVData(skills=[Skill(name="Python", category="Data")]))
            service = CVOverlapReviewService(first)
            viewed = service.read(user_id, draft.id)
            item = viewed.items[0]
            if existing_sidecar:
                saved = _save_choice(
                    service, user_id, draft.id, item.item_key, Action.REPLACE_CURRENT,
                    item.target_fingerprint,
                )
                viewed = saved
                item = viewed.items[0]
            old_choice = CVOverlapResolution(item_key=item.item_key, action=Action.KEEP_CURRENT)

            concurrent = second.scalar(select(CandidateStructuredProfile).where(
                CandidateStructuredProfile.user_id == user_id
            ))
            concurrent.structured_json = CandidateCVData(
                skills=[Skill(name="Python", category="Platform")]
            ).model_dump_json()
            second.commit()

            with pytest.raises(CVOverlapReviewStale):
                service.update(user_id, draft.id, CVOverlapReviewPatch(
                    expected_review_revision=viewed.revision,
                    expected_base_structured_fingerprint=viewed.base_structured_fingerprint,
                    expected_draft_fingerprint=viewed.draft_fingerprint,
                    resolutions=[old_choice],
                ))
            sidecar = first.scalar(select(CandidateCVOverlapReviewRecord).where(
                CandidateCVOverlapReviewRecord.draft_id == draft.id
            ))
            if existing_sidecar:
                assert sidecar is not None and sidecar.revision == viewed.revision
                original = json.loads(sidecar.resolutions_json)
                assert original[0]["action"] == "replace_current"
            else:
                assert sidecar is None
            assert CandidateCVData.model_validate_json(
                first.scalar(select(CandidateStructuredProfile.structured_json).where(
                    CandidateStructuredProfile.user_id == user_id
                ))
            ).skills == [Skill(name="Python", category="Platform")]

            refreshed = service.read(user_id, draft.id)
            assert refreshed.stale is existing_sidecar
            assert refreshed.revision == viewed.revision
            recovered = service.update(user_id, draft.id, CVOverlapReviewPatch(
                expected_review_revision=refreshed.revision,
                expected_base_structured_fingerprint=refreshed.base_structured_fingerprint,
                expected_draft_fingerprint=refreshed.draft_fingerprint,
                resolutions=[CVOverlapResolution(
                    item_key=refreshed.items[0].item_key, action=Action.KEEP_CURRENT
                )],
            ))
            assert recovered.stale is False
            assert recovered.revision == refreshed.revision + 1
            assert recovered.items[0].saved_resolution.action is Action.KEEP_CURRENT
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_two_session_current_change_invalidates_saved_review(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cv-overlap-two-session.db'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as first, Session(engine) as second:
            user_id = _user(first, "two-session-overlap@example.test")
            old = Skill(name="Python", category="Language")
            current = CandidateStructuredProfile(user_id=user_id, structured_json=CandidateCVData(skills=[old]).model_dump_json())
            draft = CandidateCVIngestionDraft(user_id=user_id, state="review_ready", documents_json="[]",
                                              merged_json=CandidateCVData(skills=[Skill(name="Python", category="Data")]).model_dump_json())
            first.add_all([current, draft])
            first.commit()
            review_service = CVOverlapReviewService(first)
            review = review_service.read(user_id, draft.id)
            item = review.items[0]
            _save_choice(review_service, user_id, draft.id, item.item_key, Action.REPLACE_CURRENT, item.target_fingerprint)

            other_current = second.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
            other_current.structured_json = CandidateCVData(skills=[Skill(name="Rust")]).model_dump_json()
            second.commit()
            with pytest.raises(CVOverlapReviewStale):
                CVIngestionService(first).confirm(user_id, draft.id)
            first.expire_all()
            assert first.get(CandidateCVIngestionDraft, draft.id).state == "review_ready"
            assert _skills(first, user_id) == [Skill(name="Rust")]
            assert CandidateStructuredItemLineageService(first).read_history(user_id) == []
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()

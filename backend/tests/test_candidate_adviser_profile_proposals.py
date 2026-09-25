import hashlib
import json
from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.models.candidate_adviser import (
    CandidateAdviserAssessmentRecord,
    CandidateAdviserClarificationRecord,
    CandidateAdviserIntakeRecord,
)
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.candidate_profile import CandidateProfile
from app.models.user import User
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessmentContent,
    CandidateAdviserAssessmentStatus,
    CandidateAdviserIntake,
    ClarificationAnswerKind,
    ClarificationInterpretation,
    ClarificationProposedEvidence,
)
from app.schemas.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalRead,
    CandidateAdviserProfileProposalState,
    CandidateAdviserProfileProposalUpdate,
    StructuredProfileSection,
)
from app.schemas.cv_ingestion import CandidateCVData, Skill
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalConflict,
    CandidateAdviserProfileProposalNotFound,
    CandidateAdviserProfileProposalService,
    structured_profile_item_fingerprint,
)
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.services.profile_revision_service import (
    CandidateProfileRevisionService,
    ProfileRevisionStale,
)


_UPDATE_ADAPTER = TypeAdapter(CandidateAdviserProfileProposalUpdate)
_CAREER_INTERPRETATION = ClarificationInterpretation(
    answer_kind=ClarificationAnswerKind.CAREER_FACT,
    confirmed_context_summary="A synthetic confirmed career fact.",
    proposed_evidence=[
        ClarificationProposedEvidence(
            fact_domain="career",
            evidence_type="achievement",
            title="Synthetic delivery",
            text="Delivered a synthetic service improvement.",
            skills=["Python"],
        )
    ],
)


def _user(session, email: str) -> str:
    user = User(email=email, password_hash="unused")
    session.add(user)
    session.commit()
    return user.id


def _source(
    session,
    user_id: str,
    *,
    answer_kind: ClarificationAnswerKind = ClarificationAnswerKind.CAREER_FACT,
    status: str = "confirmed",
    include_interpretation: bool = True,
    clarification_id: str | None = None,
) -> str:
    clarification_id = clarification_id or hashlib.sha256(
        f"source:{user_id}:{answer_kind}:{status}".encode()
    ).hexdigest()
    interpretation = _CAREER_INTERPRETATION.model_copy(update={"answer_kind": answer_kind})
    if answer_kind in {
        ClarificationAnswerKind.ELIGIBILITY_FACT,
        ClarificationAnswerKind.PREFERENCE_INTENT,
        ClarificationAnswerKind.INSUFFICIENT,
    }:
        interpretation = interpretation.model_copy(update={"proposed_evidence": []})
    session.add(CandidateAdviserClarificationRecord(
        user_id=user_id,
        clarification_id=clarification_id,
        question_key=hashlib.sha256(b"question").hexdigest(),
        origin_assessment_fingerprint="a" * 64,
        question_text="What career fact should be recorded?",
        question_source_references_json="[]",
        priority_index=0,
        answer_text="Synthetic answer.",
        interpretation_json=(
            json.dumps(interpretation.model_dump(mode="json"), sort_keys=True)
            if include_interpretation else None
        ),
        status=status,
    ))
    session.commit()
    return clarification_id


def _update(
    section: str = "skills",
    *,
    name: str = "Python",
    operation: str = "add",
    target_fingerprint: str | None = None,
) -> CandidateAdviserProfileProposalUpdate:
    items: dict[str, dict[str, object]] = {
        "employment": {"employer": "Example", "title": name, "description": "Built systems."},
        "education": {"institution": "Example University", "qualification": name},
        "credentials": {"name": name, "credential_type": "certification"},
        "skills": {"name": name},
        "projects": {"name": name, "description": "Built a useful project."},
        "achievements": {"text": name},
    }
    payload: dict[str, object] = {
        "section": section,
        "operation": operation,
        "target_fingerprint": target_fingerprint,
        "item": items[section],
    }
    return _UPDATE_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("section", "item"),
    [
        ("employment", {"employer": "Example", "title": "Engineer"}),
        ("education", {"institution": "Example University", "qualification": "BSc"}),
        ("credentials", {"name": "Chartered member", "credential_type": "professional_membership"}),
        ("skills", {"name": "Python"}),
        ("projects", {"name": "Example project"}),
        ("achievements", {"text": "Improved a service."}),
    ],
)
def test_proposal_schema_supports_each_editable_structured_section(section, item) -> None:
    parsed = _UPDATE_ADAPTER.validate_python({
        "section": section, "operation": "add", "target_fingerprint": None, "item": item,
    })
    assert parsed.section == section
    assert parsed.item is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"section": "evidence", "operation": "add", "item": {"text": "x"}},
        {"section": "eligibility", "operation": "add", "item": {}},
        {"section": "preferences", "operation": "add", "item": {}},
        {"section": "skills", "operation": "add", "item": {"name": "Python", "evidence": []}},
        {"section": "skills", "operation": "add", "item": {"name": "Python"}, "eligibility": {}},
        {"section": "skills", "operation": "replace_exact", "item": {"name": "Python"}},
        {"section": "skills", "operation": "add", "target_fingerprint": "a" * 64, "item": {"name": "Python"}},
        {"section": "skills", "operation": "replace_exact", "target_fingerprint": "bad", "item": {"name": "Python"}},
    ],
)
def test_schema_rejects_unsupported_fields_and_invalid_operation_fingerprint_pairs(payload) -> None:
    with pytest.raises(ValidationError):
        _UPDATE_ADAPTER.validate_python(payload)


def test_exact_item_fingerprint_is_canonical_material_and_section_specific() -> None:
    skill = Skill(name="Python", category="language")
    first = structured_profile_item_fingerprint(StructuredProfileSection.SKILLS, skill)
    same = structured_profile_item_fingerprint("skills", Skill(category="language", name="Python"))
    different_section = structured_profile_item_fingerprint("projects", skill)
    changed_item = structured_profile_item_fingerprint("skills", Skill(name="Rust", category="language"))
    assert len(first) == 64
    assert first == same
    assert first != different_section
    assert first != changed_item


def test_transferred_is_readable_as_reserved_history_state() -> None:
    transferred_at = datetime.now(timezone.utc)
    history = CandidateAdviserProfileProposalRead(
        id="historical-proposal",
        state=CandidateAdviserProfileProposalState.TRANSFERRED,
        revision=2,
        source_clarification_id="a" * 64,
        source_assessment_fingerprint="b" * 64,
        original_update=_update(),
        proposed_update=_update(name="Edited"),
        created_at=transferred_at,
        updated_at=transferred_at,
        rejected_at=None,
        transferred_at=transferred_at,
        transferred_profile_revision_id="profile-revision-id",
    )
    assert history.state == "transferred"
    assert history.transferred_at == transferred_at
    assert history.transferred_profile_revision_id == "profile-revision-id"


def test_database_state_constraint_accepts_reserved_transferred_history(db_session) -> None:
    user_id = _user(db_session, "proposal-transferred-history@example.com")
    update_json = json.dumps(_update().model_dump(mode="json"), sort_keys=True)
    history = CandidateAdviserProfileProposalRecord(
        user_id=user_id,
        proposal_key="f" * 64,
        state=CandidateAdviserProfileProposalState.TRANSFERRED,
        revision=2,
        source_clarification_id="a" * 64,
        source_assessment_fingerprint="b" * 64,
        original_update_json=update_json,
        proposed_update_json=update_json,
    )
    # Seed a persisted history representation directly to exercise the model
    # CHECK constraint. Phase 1 exposes no operation that performs this state change.
    db_session.add(history)
    db_session.commit()
    read = CandidateAdviserProfileProposalService(db_session).get_for_user(user_id, history.id)
    assert read.state == CandidateAdviserProfileProposalState.TRANSFERRED
    assert read.transferred_at is None
    assert read.transferred_profile_revision_id is None


def test_replace_exact_requires_well_formed_target_and_add_has_no_target() -> None:
    add = _update()
    replace = _update(operation="replace_exact", target_fingerprint="b" * 64)
    assert add.target_fingerprint is None
    assert replace.target_fingerprint == "b" * 64


def _seed_structured_profile(session, user_id: str, data: CandidateCVData) -> CandidateStructuredProfile:
    row = CandidateStructuredProfile(
        user_id=user_id,
        structured_json=json.dumps(data.model_dump(mode="json"), sort_keys=True),
    )
    session.add(row)
    ActiveCandidateEvidenceResolver(session).resolve(user_id, data)
    session.commit()
    return row


def test_transfer_stages_normal_draft_without_mutating_current_profile_and_is_idempotent(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-add@example.com")
    clarification_id = _source(db_session, user_id)
    current = CandidateCVData(skills=[Skill(name="Python"), Skill(name="Go")])
    structured = _seed_structured_profile(db_session, user_id, current)
    evidence_before = [
        (item.id, item.evidence_type, item.title, item.provenance_json)
        for item in db_session.scalars(
            select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)
        )
    ]
    proposal = CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Rust")
    )

    service = CandidateAdviserProfileProposalService(db_session)
    transferred = service.transfer_to_profile_revision(
        user_id, proposal.id, expected_revision=proposal.revision
    )
    assert transferred.proposal.state == CandidateAdviserProfileProposalState.TRANSFERRED
    assert transferred.proposal.transferred_at is not None
    assert transferred.proposal.transferred_profile_revision_id == transferred.profile_revision.id
    assert transferred.profile_revision.state == "draft"
    assert transferred.profile_revision.revision == 1
    assert transferred.profile_revision.changed_authorities == ["structured"]
    assert transferred.profile_revision.proposed_profile is None
    assert [skill.name for skill in transferred.profile_revision.proposed_structured.skills] == [
        "Python", "Go", "Rust"
    ]
    assert not hasattr(transferred.profile_revision.proposed_structured, "evidence")
    assert db_session.get(CandidateStructuredProfile, structured.id).structured_json == structured.structured_json
    assert db_session.get(CandidateProfile, user_id) is None
    evidence_after = [
        (item.id, item.evidence_type, item.title, item.provenance_json)
        for item in db_session.scalars(
            select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)
        )
    ]
    assert evidence_after == evidence_before

    # Terminal idempotence is checked before the expected-version gate.
    again = service.transfer_to_profile_revision(user_id, proposal.id, expected_revision=1)
    assert again.proposal.transferred_profile_revision_id == transferred.profile_revision.id
    assert db_session.scalar(
        select(CandidateProfileRevisionRecord).where(
            CandidateProfileRevisionRecord.user_id == user_id
        )
    ).id == transferred.profile_revision.id


def test_transfer_uses_edited_replace_target_and_preserves_its_position(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-replace@example.com")
    clarification_id = _source(db_session, user_id)
    current = CandidateCVData(skills=[Skill(name="Python"), Skill(name="Go"), Skill(name="Rust")])
    _seed_structured_profile(db_session, user_id, current)
    service = CandidateAdviserProfileProposalService(db_session)
    created = service.materialize_from_confirmed_clarification(
        user_id,
        clarification_id,
        _update(name="TypeScript", operation="replace_exact", target_fingerprint="a" * 64),
    )
    edited = service.edit_pending(
        user_id,
        created.id,
        expected_revision=created.revision,
        proposed_update=_update(
            name="Go Advanced",
            operation="replace_exact",
            target_fingerprint=structured_profile_item_fingerprint("skills", Skill(name="Go")),
        ),
    )
    result = service.transfer_to_profile_revision(
        user_id, created.id, expected_revision=edited.revision
    )
    assert [skill.name for skill in result.profile_revision.proposed_structured.skills] == [
        "Python", "Go Advanced", "Rust"
    ]


@pytest.mark.parametrize("current_skills", [
    [Skill(name="Python")],
    [Skill(name="Python"), Skill(name="Python")],
])
def test_transfer_replace_requires_one_exact_current_target(db_session, current_skills) -> None:
    user_id = _user(db_session, f"proposal-transfer-target-{len(current_skills)}@example.com")
    clarification_id = _source(db_session, user_id)
    _seed_structured_profile(db_session, user_id, CandidateCVData(skills=current_skills))
    service = CandidateAdviserProfileProposalService(db_session)
    proposal = service.materialize_from_confirmed_clarification(
        user_id,
        clarification_id,
        _update(
            name="New skill",
            operation="replace_exact",
            target_fingerprint=structured_profile_item_fingerprint("skills", Skill(name="Missing")),
        ),
    )
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="target"):
        service.transfer_to_profile_revision(user_id, proposal.id, expected_revision=1)
    db_session.expire_all()
    assert service.get_for_user(user_id, proposal.id).state == CandidateAdviserProfileProposalState.PENDING
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None


def test_transfer_add_rejects_exact_duplicate_in_current_section(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-duplicate@example.com")
    clarification_id = _source(db_session, user_id)
    _seed_structured_profile(db_session, user_id, CandidateCVData(skills=[Skill(name="Rust")]))
    service = CandidateAdviserProfileProposalService(db_session)
    proposal = service.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Rust")
    )
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="already exists"):
        service.transfer_to_profile_revision(user_id, proposal.id, expected_revision=1)
    db_session.expire_all()
    assert service.get_for_user(user_id, proposal.id).state == CandidateAdviserProfileProposalState.PENDING
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None


def test_transfer_can_stage_structured_authority_when_none_existed(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-no-structured@example.com")
    clarification_id = _source(db_session, user_id)
    proposals = CandidateAdviserProfileProposalService(db_session)
    proposal = proposals.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="First skill")
    )
    transfer = proposals.transfer_to_profile_revision(user_id, proposal.id, expected_revision=1)
    assert transfer.profile_revision.proposed_structured.skills == [Skill(name="First skill")]
    assert transfer.profile_revision.changed_authorities == ["structured"]
    assert db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )) is None
    confirmed_review = CandidateProfileRevisionService(db_session).review(
        user_id, transfer.profile_revision.id, expected_revision=1
    )
    CandidateProfileRevisionService(db_session).confirm(
        user_id, transfer.profile_revision.id, expected_revision=confirmed_review.revision
    )
    assert db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )) is not None


def test_transfer_failure_after_staging_rolls_back_both_records(db_session, monkeypatch) -> None:
    user_id = _user(db_session, "proposal-transfer-rollback@example.com")
    clarification_id = _source(db_session, user_id)
    proposal = CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Rollback skill")
    )
    original_commit = db_session.commit

    def fail_commit():
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="injected commit failure"):
        CandidateAdviserProfileProposalService(db_session).transfer_to_profile_revision(
            user_id, proposal.id, expected_revision=1
        )
    monkeypatch.setattr(db_session, "commit", original_commit)
    db_session.expire_all()
    stored = CandidateAdviserProfileProposalService(db_session).get_for_user(user_id, proposal.id)
    assert stored.state == CandidateAdviserProfileProposalState.PENDING
    assert stored.transferred_at is None and stored.transferred_profile_revision_id is None
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None


def test_transfer_catches_active_slot_race_and_rolls_back_proposal(tmp_path) -> None:
    from app.core.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'proposal-active-slot-race.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        with sessions() as seed:
            user_id = _user(seed, "proposal-active-slot-race@example.com")
            clarification_id = _source(seed, user_id)
            proposal = CandidateAdviserProfileProposalService(seed).materialize_from_confirmed_clarification(
                user_id, clarification_id, _update(name="Racing skill")
            )
            proposal_id = proposal.id

        with sessions() as transfer_session, sessions() as competing_session:
            winner: list[str] = []

            def create_competing_draft(session, flush_context, instances):
                winner.append(
                    CandidateProfileRevisionService(competing_session)
                    .create_or_resume(user_id)
                    .id
                )

            event.listen(transfer_session, "before_flush", create_competing_draft, once=True)
            with pytest.raises(CandidateAdviserProfileProposalConflict, match="active Profile draft"):
                CandidateAdviserProfileProposalService(transfer_session).transfer_to_profile_revision(
                    user_id, proposal_id, expected_revision=1
                )
            assert winner
            transfer_session.expire_all()
            stored = CandidateAdviserProfileProposalService(transfer_session).get_for_user(
                user_id, proposal_id
            )
            assert stored.state == CandidateAdviserProfileProposalState.PENDING
            assert stored.transferred_at is None
            assert stored.transferred_profile_revision_id is None
            assert transfer_session.query(CandidateProfileRevisionRecord).filter_by(
                user_id=user_id
            ).count() == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_transfer_conflicts_with_existing_active_draft_without_touching_proposal(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-active@example.com")
    clarification_id = _source(db_session, user_id)
    proposal = CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Rust")
    )
    existing = CandidateProfileRevisionService(db_session).create_or_resume(user_id)
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="active Profile draft"):
        CandidateAdviserProfileProposalService(db_session).transfer_to_profile_revision(
            user_id, proposal.id, expected_revision=1
        )
    db_session.expire_all()
    assert CandidateProfileRevisionService(db_session).active(user_id).id == existing.id
    stored = CandidateAdviserProfileProposalService(db_session).get_for_user(user_id, proposal.id)
    assert stored.state == CandidateAdviserProfileProposalState.PENDING
    assert stored.transferred_at is None and stored.transferred_profile_revision_id is None


def test_transfer_wrong_expected_revision_is_non_mutating_and_correct_version_can_retry(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-wrong-version@example.com")
    clarification_id = _source(db_session, user_id)
    service = CandidateAdviserProfileProposalService(db_session)
    proposal = service.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Retry skill")
    )
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="version"):
        service.transfer_to_profile_revision(
            user_id, proposal.id, expected_revision=proposal.revision + 1
        )
    db_session.expire_all()
    unchanged = service.get_for_user(user_id, proposal.id)
    assert unchanged.state == CandidateAdviserProfileProposalState.PENDING
    assert unchanged.revision == proposal.revision
    assert unchanged.transferred_at is None
    assert unchanged.transferred_profile_revision_id is None
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None

    retried = service.transfer_to_profile_revision(
        user_id, proposal.id, expected_revision=proposal.revision
    )
    assert retried.proposal.state == CandidateAdviserProfileProposalState.TRANSFERRED


def test_transfer_is_user_scoped_for_service_and_http_endpoint(client, db_session) -> None:
    headers_a = {}
    headers_b = {}
    for email in ("proposal-transfer-owner@example.com", "proposal-transfer-other@example.com"):
        credentials = {"email": email, "password": "strong-password"}
        assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
        token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
        if email.startswith("proposal-transfer-owner"):
            headers_a = {"Authorization": f"Bearer {token}"}
        else:
            headers_b = {"Authorization": f"Bearer {token}"}
    owner_id = db_session.scalar(select(User).where(
        User.email == "proposal-transfer-owner@example.com"
    )).id
    other_id = db_session.scalar(select(User).where(
        User.email == "proposal-transfer-other@example.com"
    )).id
    clarification_id = _source(db_session, owner_id)
    proposal = CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
        owner_id, clarification_id, _update(name="Private proposal")
    )
    service = CandidateAdviserProfileProposalService(db_session)
    with pytest.raises(CandidateAdviserProfileProposalNotFound):
        service.transfer_to_profile_revision(other_id, proposal.id, expected_revision=1)
    response = client.post(
        f"/api/v1/candidate-adviser/profile-proposals/{proposal.id}/transfer",
        headers=headers_b,
        json={"expected_revision": 1},
    )
    assert response.status_code == 404
    db_session.expire_all()
    assert service.get_for_user(owner_id, proposal.id).state == CandidateAdviserProfileProposalState.PENDING
    assert db_session.scalars(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id.in_([owner_id, other_id])
    )).all() == []


def test_rejected_proposal_cannot_be_transferred(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-rejected@example.com")
    clarification_id = _source(db_session, user_id)
    service = CandidateAdviserProfileProposalService(db_session)
    proposal = service.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Rejected")
    )
    rejected = service.reject_pending(user_id, proposal.id, expected_revision=proposal.revision)
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="rejected"):
        service.transfer_to_profile_revision(
            user_id, proposal.id, expected_revision=rejected.revision
        )
    db_session.expire_all()
    stored = service.get_for_user(user_id, proposal.id)
    assert stored.state == CandidateAdviserProfileProposalState.REJECTED
    assert stored.transferred_at is None and stored.transferred_profile_revision_id is None
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None


@pytest.mark.parametrize("invalidity", ["status", "interpretation", "answer_kind", "provenance"])
def test_transfer_fails_closed_when_confirmed_source_is_invalidated(db_session, invalidity) -> None:
    user_id = _user(db_session, f"proposal-transfer-invalid-source-{invalidity}@example.com")
    clarification_id = _source(db_session, user_id)
    proposals = CandidateAdviserProfileProposalService(db_session)
    proposal = proposals.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Source-bound")
    )
    source = db_session.scalar(select(CandidateAdviserClarificationRecord).where(
        CandidateAdviserClarificationRecord.user_id == user_id,
        CandidateAdviserClarificationRecord.clarification_id == clarification_id,
    ))
    if invalidity == "status":
        source.status = "review_ready"
    elif invalidity == "interpretation":
        source.interpretation_json = None
    elif invalidity == "answer_kind":
        interpretation = ClarificationInterpretation.model_validate_json(source.interpretation_json)
        source.interpretation_json = json.dumps(
            interpretation.model_copy(update={
                "answer_kind": ClarificationAnswerKind.ELIGIBILITY_FACT,
                "proposed_evidence": [],
            }).model_dump(mode="json"),
            sort_keys=True,
        )
    else:
        source.origin_assessment_fingerprint = "c" * 64
    db_session.commit()

    with pytest.raises(CandidateAdviserProfileProposalConflict):
        proposals.transfer_to_profile_revision(user_id, proposal.id, expected_revision=1)
    db_session.expire_all()
    stored = proposals.get_for_user(user_id, proposal.id)
    assert stored.state == CandidateAdviserProfileProposalState.PENDING
    assert stored.transferred_at is None and stored.transferred_profile_revision_id is None
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None


def test_review_ready_profile_revision_blocks_adviser_transfer(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-review-ready-collision@example.com")
    clarification_id = _source(db_session, user_id)
    proposals = CandidateAdviserProfileProposalService(db_session)
    proposal = proposals.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Waiting")
    )
    revisions = CandidateProfileRevisionService(db_session)
    active = revisions.create_or_resume(user_id)
    ready = revisions.review(user_id, active.id, expected_revision=active.revision)
    assert ready.state == "review_ready"
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="active Profile draft"):
        proposals.transfer_to_profile_revision(user_id, proposal.id, expected_revision=proposal.revision)
    db_session.expire_all()
    unchanged = revisions.active(user_id)
    assert unchanged.id == ready.id
    assert unchanged.state == "review_ready" and unchanged.revision == ready.revision
    assert proposals.get_for_user(user_id, proposal.id).state == CandidateAdviserProfileProposalState.PENDING
    assert db_session.scalars(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )).all() == [db_session.get(CandidateProfileRevisionRecord, ready.id)]


def test_transfer_preserves_existing_scalar_profile_authority(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-scalar-preservation@example.com")
    profile = CandidateProfile(
        user_id=user_id,
        display_name="Scalar Candidate",
        headline="Staff engineer",
        current_role="Platform lead",
        location="Toronto",
        preferred_email="scalar@example.test",
    )
    db_session.add(profile)
    _seed_structured_profile(db_session, user_id, CandidateCVData(skills=[Skill(name="Python")]))
    before = (
        profile.display_name, profile.headline, profile.current_role, profile.location,
        profile.preferred_email,
    )
    clarification_id = _source(db_session, user_id)
    proposal = CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Rust")
    )
    transfer = CandidateAdviserProfileProposalService(db_session).transfer_to_profile_revision(
        user_id, proposal.id, expected_revision=proposal.revision
    )
    assert transfer.profile_revision.changed_authorities == ["structured"]
    assert transfer.profile_revision.proposed_profile.display_name == before[0]
    assert transfer.profile_revision.proposed_profile.headline == before[1]
    assert transfer.profile_revision.proposed_profile.current_role == before[2]
    assert transfer.profile_revision.proposed_profile.location == before[3]
    assert (
        profile.display_name, profile.headline, profile.current_role, profile.location,
        profile.preferred_email,
    ) == before
    revisions = CandidateProfileRevisionService(db_session)
    ready = revisions.review(user_id, transfer.profile_revision.id, expected_revision=1)
    revisions.confirm(user_id, transfer.profile_revision.id, expected_revision=ready.revision)
    db_session.refresh(profile)
    assert (
        profile.display_name, profile.headline, profile.current_role, profile.location,
        profile.preferred_email,
    ) == before


def test_transferred_revision_can_be_reviewed_confirmed_and_discard_does_not_reopen_proposal(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-lifecycle@example.com")
    clarification_id = _source(db_session, user_id)
    ActiveCandidateEvidenceResolver(db_session).resolve(user_id, CandidateCVData())
    db_session.commit()
    evidence_before = [
        (item.fingerprint, item.evidence_type, item.title, item.provenance_json)
        for item in db_session.scalars(
            select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)
        )
    ]
    assert len(evidence_before) == 1
    assert '"source_kind": "user_confirmed"' in evidence_before[0][3]
    _seed_structured_profile(db_session, user_id, CandidateCVData(skills=[Skill(name="Python")]))
    proposals = CandidateAdviserProfileProposalService(db_session)
    created = proposals.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Rust")
    )
    transfer = proposals.transfer_to_profile_revision(user_id, created.id, expected_revision=1)
    revisions = CandidateProfileRevisionService(db_session)
    reviewed = revisions.review(user_id, transfer.profile_revision.id, expected_revision=1)
    confirmed = revisions.confirm(user_id, transfer.profile_revision.id, expected_revision=reviewed.revision)
    assert confirmed.state == "confirmed"
    assert [skill.name for skill in CandidateCVData.model_validate_json(
        db_session.scalar(select(CandidateStructuredProfile).where(
            CandidateStructuredProfile.user_id == user_id
        )).structured_json
    ).skills] == ["Python", "Rust"]
    evidence_after = [
        (item.fingerprint, item.evidence_type, item.title, item.provenance_json)
        for item in db_session.scalars(
            select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)
        )
    ]
    assert evidence_after == evidence_before
    assert all('"source_kind": "confirmed_profile"' not in item[3] for item in evidence_after)

    other_user = _user(db_session, "proposal-transfer-discard@example.com")
    other_source = _source(db_session, other_user)
    other_proposal = proposals.materialize_from_confirmed_clarification(
        other_user, other_source, _update(name="Discarded skill")
    )
    other_transfer = proposals.transfer_to_profile_revision(
        other_user, other_proposal.id, expected_revision=1
    )
    revisions.discard(other_user, other_transfer.profile_revision.id, expected_revision=1)
    db_session.expire_all()
    assert proposals.get_for_user(other_user, other_proposal.id).state == CandidateAdviserProfileProposalState.TRANSFERRED
    assert proposals.transfer_to_profile_revision(other_user, other_proposal.id, expected_revision=1).profile_revision.state == "discarded"


def test_transferred_revision_becomes_stale_after_current_structured_state_changes(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-stale@example.com")
    clarification_id = _source(db_session, user_id)
    row = _seed_structured_profile(
        db_session, user_id, CandidateCVData(skills=[Skill(name="Original")])
    )
    proposals = CandidateAdviserProfileProposalService(db_session)
    proposal = proposals.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Proposed")
    )
    transfer = proposals.transfer_to_profile_revision(user_id, proposal.id, expected_revision=1)
    changed = CandidateCVData(skills=[Skill(name="CV confirmed update")])
    row.structured_json = json.dumps(changed.model_dump(mode="json"), sort_keys=True)
    ActiveCandidateEvidenceResolver(db_session).resolve(user_id, changed)
    db_session.commit()
    with pytest.raises(ProfileRevisionStale, match="structured"):
        CandidateProfileRevisionService(db_session).review(
            user_id, transfer.profile_revision.id, expected_revision=1
        )


def test_competing_transfer_sessions_observe_first_terminal_transition(tmp_path) -> None:
    from app.core.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'proposal-transfer-race.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        with sessions() as seed:
            user_id = _user(seed, "proposal-transfer-race@example.com")
            clarification_id = _source(seed, user_id)
            proposal = CandidateAdviserProfileProposalService(seed).materialize_from_confirmed_clarification(
                user_id, clarification_id, _update(name="Concurrent skill")
            )
            proposal_id = proposal.id

        with sessions() as first, sessions() as second:
            stale = CandidateAdviserProfileProposalService(first).get_for_user(user_id, proposal_id)
            assert stale.state == CandidateAdviserProfileProposalState.PENDING
            winner = CandidateAdviserProfileProposalService(second).transfer_to_profile_revision(
                user_id, proposal_id, expected_revision=stale.revision
            )
            loser = CandidateAdviserProfileProposalService(first).transfer_to_profile_revision(
                user_id, proposal_id, expected_revision=stale.revision
            )
            assert loser.proposal.state == CandidateAdviserProfileProposalState.TRANSFERRED
            assert loser.profile_revision.id == winner.profile_revision.id
            assert first.query(CandidateProfileRevisionRecord).filter_by(user_id=user_id).count() == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize("answer_kind", [ClarificationAnswerKind.CAREER_FACT, ClarificationAnswerKind.MIXED])
def test_materialization_accepts_confirmed_career_or_mixed_clarification(db_session, answer_kind) -> None:
    user_id = _user(db_session, f"proposal-source-{answer_kind}@example.com")
    clarification_id = _source(db_session, user_id, answer_kind=answer_kind)
    created = CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
        user_id, clarification_id, _update()
    )
    assert created.state == "pending"
    assert created.transferred_at is None
    assert created.transferred_profile_revision_id is None
    assert created.source_clarification_id == clarification_id
    assert created.source_assessment_fingerprint == "a" * 64


@pytest.mark.parametrize("status,interpretation", [("review_ready", True), ("unanswered", False), ("confirmed", False)])
def test_materialization_requires_confirmed_interpreted_source(db_session, status, interpretation) -> None:
    user_id = _user(db_session, f"proposal-invalid-source-{status}-{interpretation}@example.com")
    clarification_id = _source(
        db_session, user_id, status=status, include_interpretation=interpretation
    )
    with pytest.raises(CandidateAdviserProfileProposalConflict):
        CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
            user_id, clarification_id, _update()
        )


@pytest.mark.parametrize("answer_kind", [
    ClarificationAnswerKind.ELIGIBILITY_FACT,
    ClarificationAnswerKind.PREFERENCE_INTENT,
    ClarificationAnswerKind.INSUFFICIENT,
])
def test_noncareer_clarifications_cannot_source_structured_proposals(db_session, answer_kind) -> None:
    user_id = _user(db_session, f"proposal-noncareer-{answer_kind}@example.com")
    clarification_id = _source(db_session, user_id, answer_kind=answer_kind)
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="career-fact or mixed"):
        CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
            user_id, clarification_id, _update()
        )


def test_materialization_requires_owned_clarification_and_does_not_check_current_assessment(db_session) -> None:
    owner_id = _user(db_session, "proposal-owner@example.com")
    other_id = _user(db_session, "proposal-other@example.com")
    clarification_id = _source(db_session, owner_id)
    service = CandidateAdviserProfileProposalService(db_session)
    with pytest.raises(CandidateAdviserProfileProposalNotFound):
        service.materialize_from_confirmed_clarification(other_id, clarification_id, _update())

    # Reassess after the confirmation so the source's origin fingerprint is no
    # longer the current assessment fingerprint.
    db_session.add(CandidateStructuredProfile(
        user_id=owner_id,
        structured_json=json.dumps(CandidateCVData().model_dump(mode="json")),
    ))
    db_session.commit()
    adviser = CandidateAdviserService(db_session)
    adviser.save_intake(owner_id, CandidateAdviserIntake(career_direction="Synthetic direction"))
    insight = {"text": "Synthetic strategy.", "source_references": [{"source_type": "intake", "reference": "career_direction"}]}
    content = CandidateAdviserAssessmentContent.model_validate({
        "professional_positioning": insight,
        "transferable_strengths": [], "development_gaps": [], "role_hypotheses": [],
        "transition_assessment": insight, "open_questions": [],
        "career_strategy_summary": insight, "job_search_strategy_summary": insight,
    })

    class _ReassessmentAgent:
        def assess(self, *, semantic_input):
            return content

    reassessment = CandidateAdviserService(db_session, agent=_ReassessmentAgent())
    current = reassessment.assess(owner_id)
    reassessment.confirm_assessment(owner_id)
    assert current.input_fingerprint != "a" * 64
    proposal = service.materialize_from_confirmed_clarification(owner_id, clarification_id, _update())
    assert proposal.source_assessment_fingerprint == "a" * 64
    assert reassessment.get_assessment_read_only(owner_id).status == CandidateAdviserAssessmentStatus.CONFIRMED


def test_materialization_is_idempotent_but_keeps_distinct_updates_distinct(db_session) -> None:
    user_id = _user(db_session, "proposal-idempotent@example.com")
    clarification_id = _source(db_session, user_id)
    service = CandidateAdviserProfileProposalService(db_session)
    first = service.materialize_from_confirmed_clarification(user_id, clarification_id, _update())
    repeated = service.materialize_from_confirmed_clarification(user_id, clarification_id, _update())
    second = service.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="TypeScript")
    )
    exact = service.materialize_from_confirmed_clarification(
        user_id,
        clarification_id,
        _update(operation="replace_exact", target_fingerprint="d" * 64),
    )
    rows = db_session.scalars(select(CandidateAdviserProfileProposalRecord)).all()
    assert first.id == repeated.id
    assert second.id != first.id and exact.id != first.id
    assert len(rows) == 3
    assert first.proposed_update.item.name == "Python"


def test_edit_preserves_original_and_increments_optimistic_revision(db_session) -> None:
    user_id = _user(db_session, "proposal-edit@example.com")
    clarification_id = _source(db_session, user_id)
    service = CandidateAdviserProfileProposalService(db_session)
    created = service.materialize_from_confirmed_clarification(user_id, clarification_id, _update())
    edited = service.edit_pending(
        user_id, created.id, expected_revision=created.revision, proposed_update=_update(name="Python 3")
    )
    assert edited.revision == created.revision + 1
    assert edited.original_update == created.original_update
    assert edited.proposed_update.item.name == "Python 3"
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="version conflict"):
        service.edit_pending(
            user_id, created.id, expected_revision=created.revision, proposed_update=_update(name="Lost edit")
        )


def test_edit_cannot_change_section_or_edit_rejected_proposal(db_session) -> None:
    user_id = _user(db_session, "proposal-section-edit@example.com")
    clarification_id = _source(db_session, user_id)
    service = CandidateAdviserProfileProposalService(db_session)
    created = service.materialize_from_confirmed_clarification(user_id, clarification_id, _update())
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="original structured section"):
        service.edit_pending(
            user_id, created.id, expected_revision=created.revision, proposed_update=_update("projects")
        )
    rejected = service.reject_pending(user_id, created.id, expected_revision=created.revision)
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="Only a pending"):
        service.edit_pending(
            user_id, created.id, expected_revision=rejected.revision, proposed_update=_update(name="No")
        )


def test_reject_is_idempotent_retains_history_and_uses_expected_revision(db_session) -> None:
    user_id = _user(db_session, "proposal-reject@example.com")
    clarification_id = _source(db_session, user_id)
    service = CandidateAdviserProfileProposalService(db_session)
    created = service.materialize_from_confirmed_clarification(user_id, clarification_id, _update())
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="expected"):
        service.reject_pending(user_id, created.id, expected_revision=created.revision + 1)
    rejected = service.reject_pending(user_id, created.id, expected_revision=created.revision)
    repeated = service.reject_pending(user_id, created.id, expected_revision=created.revision)
    assert rejected.state == "rejected"
    assert rejected.rejected_at is not None
    assert rejected.transferred_at is None
    assert rejected.transferred_profile_revision_id is None
    assert rejected.revision == created.revision + 1
    assert rejected.original_update == created.original_update
    assert rejected.proposed_update == created.proposed_update
    assert rejected.source_clarification_id == created.source_clarification_id
    assert rejected.source_assessment_fingerprint == created.source_assessment_fingerprint
    assert repeated == rejected
    rematerialized = service.materialize_from_confirmed_clarification(user_id, clarification_id, _update())
    assert rematerialized.id == rejected.id
    assert rematerialized.state == "rejected"


def test_list_is_user_scoped_newest_first_and_bounded(db_session) -> None:
    first_user = _user(db_session, "proposal-list-a@example.com")
    second_user = _user(db_session, "proposal-list-b@example.com")
    first_source = _source(db_session, first_user)
    another_source = _source(
        db_session,
        first_user,
        clarification_id=hashlib.sha256(b"another confirmed source").hexdigest(),
    )
    second_source = _source(db_session, second_user)
    service = CandidateAdviserProfileProposalService(db_session)
    assert not hasattr(service, "transfer")
    older = service.materialize_from_confirmed_clarification(first_user, first_source, _update(name="Older"))
    newer = service.materialize_from_confirmed_clarification(first_user, another_source, _update(name="Newer"))
    other = service.materialize_from_confirmed_clarification(second_user, second_source, _update())
    assert [row.id for row in service.list_for_user(first_user, limit=1)] == [newer.id]
    assert {row.id for row in service.list_for_user(first_user)} == {older.id, newer.id}
    assert service.list_for_user(second_user)[0].id == other.id
    with pytest.raises(ValueError, match="between 1 and 100"):
        service.list_for_user(first_user, limit=101)
    with pytest.raises(CandidateAdviserProfileProposalNotFound):
        service.get_for_user(first_user, other.id)


def test_proposal_crud_is_inert_to_snapshot_context_evidence_and_adviser_currentness(db_session) -> None:
    user_id = _user(db_session, "proposal-inert@example.com")
    source_id = _source(db_session, user_id)
    db_session.add(CandidateStructuredProfile(
        user_id=user_id,
        structured_json=json.dumps(CandidateCVData().model_dump(mode="json")),
    ))
    db_session.add(CandidateAdviserIntakeRecord(
        user_id=user_id,
        intake_json=json.dumps(CandidateAdviserIntake(career_direction="Synthetic direction").model_dump(mode="json")),
    ))
    db_session.commit()
    ActiveCandidateEvidenceResolver(db_session).resolve(user_id, CandidateCVData())
    adviser = CandidateAdviserService(db_session)
    fingerprint = adviser.input_fingerprint(user_id, read_only=True)
    insight = {"text": "Synthetic strategy.", "source_references": [{"source_type": "intake", "reference": "career_direction"}]}
    assessment_content = CandidateAdviserAssessmentContent.model_validate({
        "professional_positioning": insight,
        "transferable_strengths": [], "development_gaps": [], "role_hypotheses": [],
        "transition_assessment": insight, "open_questions": [],
        "career_strategy_summary": insight, "job_search_strategy_summary": insight,
    })
    db_session.add(CandidateAdviserAssessmentRecord(
        user_id=user_id,
        input_fingerprint=fingerprint,
        status=CandidateAdviserAssessmentStatus.CONFIRMED,
        assessment_json=json.dumps(assessment_content.model_dump(mode="json"), sort_keys=True),
    ))
    db_session.commit()

    reader = CanonicalCandidateReadService(db_session)
    before_snapshot = reader.read(user_id).model_dump(mode="json")
    before_context_model = reader.candidate_context(reader.read(user_id))
    before_context = before_context_model.model_dump(mode="json")
    before_discovery_fingerprint = UserJobDiscoveryService.candidate_evaluation_fingerprint(
        before_context_model
    )
    before_evidence = [
        (row.id, row.fingerprint, row.evidence_type, row.title, row.text, row.skills_json, row.provenance_json)
        for row in db_session.scalars(
            select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)
        )
    ]
    before_revisions = db_session.scalars(select(CandidateProfileRevisionRecord)).all()
    before_structured = db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )).structured_json
    assert adviser.get_assessment_read_only(user_id).status == CandidateAdviserAssessmentStatus.CONFIRMED

    service = CandidateAdviserProfileProposalService(db_session)
    created = service.materialize_from_confirmed_clarification(user_id, source_id, _update())
    edited = service.edit_pending(
        user_id, created.id, expected_revision=created.revision, proposed_update=_update(name="Edited")
    )
    service.reject_pending(user_id, edited.id, expected_revision=edited.revision)
    staged = service.materialize_from_confirmed_clarification(
        user_id, source_id, _update(name="Transfer only")
    )
    transfer = service.transfer_to_profile_revision(
        user_id, staged.id, expected_revision=staged.revision
    )
    assert transfer.proposal.state == CandidateAdviserProfileProposalState.TRANSFERRED

    after_snapshot = reader.read(user_id).model_dump(mode="json")
    after_context_model = reader.candidate_context(reader.read(user_id))
    after_context = after_context_model.model_dump(mode="json")
    after_evidence = [
        (row.id, row.fingerprint, row.evidence_type, row.title, row.text, row.skills_json, row.provenance_json)
        for row in db_session.scalars(
            select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)
        )
    ]
    after_revisions = db_session.scalars(select(CandidateProfileRevisionRecord)).all()
    after_structured = db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )).structured_json
    assert after_snapshot == before_snapshot
    assert after_context == before_context
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(
        after_context_model
    ) == before_discovery_fingerprint
    assert after_evidence == before_evidence
    assert before_revisions == []
    assert len(after_revisions) == 1 and after_revisions[0].state == "draft"
    assert after_structured == before_structured
    assert adviser.get_assessment_read_only(user_id).status == CandidateAdviserAssessmentStatus.CONFIRMED


def test_http_proposal_routes_are_authenticated_user_scoped_and_provider_free(client, db_session, monkeypatch) -> None:
    from app.api import deps

    def no_adviser_factory(*args, **kwargs):
        raise AssertionError("proposal CRUD must not construct Candidate Adviser provider factories")

    monkeypatch.setattr(deps, "_build_candidate_adviser_service", no_adviser_factory)
    headers_a = {}
    headers_b = {}
    for email, target in (("proposal-http-a@example.com", "a"), ("proposal-http-b@example.com", "b")):
        credentials = {"email": email, "password": "strong-password"}
        assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
        token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
        if target == "a":
            headers_a = {"Authorization": f"Bearer {token}"}
        else:
            headers_b = {"Authorization": f"Bearer {token}"}
    user_a = db_session.scalar(select(User).where(User.email == "proposal-http-a@example.com"))
    clarification_id = _source(db_session, user_a.id)
    created = CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
        user_a.id, clarification_id, _update()
    )
    assert client.get("/api/v1/candidate-adviser/profile-proposals").status_code == 401
    assert client.post("/api/v1/candidate-adviser/profile-proposals", headers=headers_a).status_code == 405
    assert client.get("/api/v1/candidate-adviser/profile-proposals", headers=headers_a).json()[0]["id"] == created.id
    assert client.get(
        f"/api/v1/candidate-adviser/profile-proposals/{created.id}", headers=headers_a
    ).status_code == 200
    assert client.get(
        f"/api/v1/candidate-adviser/profile-proposals/{created.id}", headers=headers_b
    ).status_code == 404
    assert client.patch(
        f"/api/v1/candidate-adviser/profile-proposals/{created.id}",
        headers=headers_a,
        json={"expected_revision": created.revision, "proposed_update": _update(name="HTTP edit").model_dump(mode="json")},
    ).status_code == 200
    assert client.post(
        f"/api/v1/candidate-adviser/profile-proposals/{created.id}/reject",
        headers=headers_a,
        json={"expected_revision": 2},
    ).json()["state"] == "rejected"
    assert client.post(
        f"/api/v1/candidate-adviser/profile-proposals/{created.id}/transfer",
        headers=headers_a,
        json={"expected_revision": 3},
    ).status_code == 409


def test_transfer_http_endpoint_is_provider_free(client, db_session, monkeypatch) -> None:
    from app.api import deps

    def no_provider(*args, **kwargs):
        raise AssertionError("Profile proposal transfer must not construct provider services")

    monkeypatch.setattr(deps, "_build_candidate_adviser_service", no_provider)
    monkeypatch.setattr(deps, "get_semantic_response_client", no_provider)
    credentials = {"email": "proposal-transfer-http@example.com", "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user = db_session.scalar(select(User).where(User.email == credentials["email"]))
    clarification_id = _source(db_session, user.id)
    proposal = CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
        user.id, clarification_id, _update(name="HTTP transferred")
    )
    response = client.post(
        f"/api/v1/candidate-adviser/profile-proposals/{proposal.id}/transfer",
        headers=headers,
        json={"expected_revision": proposal.revision},
    )
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["state"] == "transferred"
    assert response.json()["profile_revision"]["state"] == "draft"

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
    EmploymentProposalUpdate,
    CandidateAdviserProfileProposalRead,
    CandidateAdviserProfileProposalOverlapAction,
    CandidateAdviserProfileProposalOverlapResolutionRequest,
    CandidateAdviserProfileProposalState,
    CandidateAdviserProfileProposalUpdate,
    SkillProposalUpdate,
    StructuredProfileSection,
)
from app.schemas.cv_ingestion import CandidateCVData, Employment, Project, Skill
from app.schemas.structured_profile import StructuredItemRelationship, StructuredItemSourceKind
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
from app.services.candidate_structured_item_lineage import CandidateStructuredItemLineageService
from app.services.profile_revision_service import structured_authority_fingerprint
from tests.test_profile import auth_header, register_and_login


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


def test_transfer_add_exact_reinforcement_keeps_one_current_item(db_session) -> None:
    user_id = _user(db_session, "proposal-transfer-duplicate@example.com")
    clarification_id = _source(db_session, user_id)
    _seed_structured_profile(db_session, user_id, CandidateCVData(skills=[Skill(name="Rust")]))
    service = CandidateAdviserProfileProposalService(db_session)
    proposal = service.materialize_from_confirmed_clarification(
        user_id, clarification_id, _update(name="Rust")
    )
    transfer = service.transfer_to_profile_revision(user_id, proposal.id, expected_revision=1)
    assert transfer.profile_revision.proposed_structured.skills == [Skill(name="Rust")]
    reviewed = CandidateProfileRevisionService(db_session).review(
        user_id, transfer.profile_revision.id, expected_revision=transfer.profile_revision.revision
    )
    confirmed = CandidateProfileRevisionService(db_session).confirm(
        user_id, transfer.profile_revision.id, expected_revision=reviewed.revision
    )
    current = db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    ))
    assert CandidateCVData.model_validate_json(current.structured_json).skills == [Skill(name="Rust")]
    history = CandidateStructuredItemLineageService(db_session).read_history(user_id)
    assert any(
        value.source_kind is StructuredItemSourceKind.CANDIDATE_ADVISER
        and value.source_ref == proposal.id
        and value.relationship is StructuredItemRelationship.REINFORCEMENT
        for value in history
    )
    assert confirmed.state == "confirmed"
    db_session.expire_all()
    assert service.get_for_user(user_id, proposal.id).state == CandidateAdviserProfileProposalState.TRANSFERRED


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


@pytest.mark.parametrize("change_between_review_and_confirm", [False, True])
def test_normalized_adviser_reinforcement_keeps_structured_dependency_fresh(tmp_path, change_between_review_and_confirm):
    from app.core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / f'adviser-reinforcement-dependency-{change_between_review_and_confirm}.db'}"
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        with sessions() as first:
            user_id = _user(first, f"adviser-reinforcement-dependency-{change_between_review_and_confirm}@example.test")
            _source(first, user_id)
            _seed_structured_profile(first, user_id, CandidateCVData(skills=[Skill(name="Python")]))
            proposal = CandidateAdviserProfileProposalService(first).materialize_from_confirmed_clarification(
                user_id,
                first.scalar(select(CandidateAdviserClarificationRecord.clarification_id).where(
                    CandidateAdviserClarificationRecord.user_id == user_id
                )),
                _update(name=" PYTHON "),
            )
            transfer = CandidateAdviserProfileProposalService(first).transfer_to_profile_revision(
                user_id, proposal.id, expected_revision=proposal.revision
            )
            revision_id = transfer.profile_revision.id
            proposal_id = proposal.id
            assert transfer.profile_revision.changed_authorities == []
            assert transfer.profile_revision.proposed_structured.skills == [Skill(name="Python")]

            if not change_between_review_and_confirm:
                projection = CandidateProfileRevisionService(first).read_record(
                    first.get(CandidateProfileRevisionRecord, revision_id), user_id
                )
                assert projection.stale_authorities == []

        if change_between_review_and_confirm:
            with sessions() as first, sessions() as second:
                revision = first.get(CandidateProfileRevisionRecord, revision_id)
                reviewed = CandidateProfileRevisionService(first).review(
                    user_id, revision_id, expected_revision=revision.revision
                )
                current = second.scalar(select(CandidateStructuredProfile).where(
                    CandidateStructuredProfile.user_id == user_id
                ))
                changed = CandidateCVData(skills=[Skill(name="Rust")])
                current.structured_json = changed.model_dump_json()
                ActiveCandidateEvidenceResolver(second).resolve(user_id, changed)
                second.commit()
                with pytest.raises(ProfileRevisionStale, match="structured"):
                    CandidateProfileRevisionService(first).confirm(
                        user_id, revision_id, expected_revision=reviewed.revision
                    )
                assert first.get(CandidateProfileRevisionRecord, revision_id).state == "review_ready"
                assert CandidateAdviserProfileProposalService(first).get_for_user(
                    user_id, proposal_id
                ).state is CandidateAdviserProfileProposalState.TRANSFERRED
                first.expire_all()
                current_after = first.scalar(select(CandidateStructuredProfile).where(
                    CandidateStructuredProfile.user_id == user_id
                ))
                assert CandidateCVData.model_validate_json(current_after.structured_json).skills == [Skill(name="Rust")]
                assert not any(
                    row.source_kind is StructuredItemSourceKind.CANDIDATE_ADVISER
                    for row in CandidateStructuredItemLineageService(first).read_history(user_id)
                )
        else:
            with sessions() as first, sessions() as second:
                current = second.scalar(select(CandidateStructuredProfile).where(
                    CandidateStructuredProfile.user_id == user_id
                ))
                changed = CandidateCVData(skills=[Skill(name="Rust")])
                current.structured_json = changed.model_dump_json()
                ActiveCandidateEvidenceResolver(second).resolve(user_id, changed)
                second.commit()

                service = CandidateProfileRevisionService(first)
                revision = first.get(CandidateProfileRevisionRecord, revision_id)
                projection = service.read_record(revision, user_id)
                assert projection.changed_authorities == []
                assert projection.stale_authorities == ["structured"]
                with pytest.raises(ProfileRevisionStale, match="structured"):
                    service.review(user_id, revision_id, expected_revision=revision.revision)
                assert revision.state == "draft"
                assert CandidateAdviserProfileProposalService(first).get_for_user(
                    user_id, proposal_id
                ).state is CandidateAdviserProfileProposalState.TRANSFERRED
                first.expire_all()
                current_after = first.scalar(select(CandidateStructuredProfile).where(
                    CandidateStructuredProfile.user_id == user_id
                ))
                assert CandidateCVData.model_validate_json(current_after.structured_json).skills == [Skill(name="Rust")]
                assert not any(
                    row.source_kind is StructuredItemSourceKind.CANDIDATE_ADVISER
                    for row in CandidateStructuredItemLineageService(first).read_history(user_id)
                )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


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


def _ambiguous_employment_proposal(session, email):
    user_id = session.scalar(select(User.id).where(User.email == email))
    if user_id is None:
        user_id = _user(session, email)
    _seed_structured_profile(session, user_id, CandidateCVData(employment=[
        Employment(employer="Example", title="Engineer", start_date="2021"),
        Employment(employer="Example", title="Manager", start_date="2021"),
    ]))
    clarification_id = _source(session, user_id)
    proposal = CandidateAdviserProfileProposalService(session).materialize_from_confirmed_clarification(
        user_id, clarification_id,
        EmploymentProposalUpdate(
            section="employment", operation="add",
            item=Employment(employer="Example", title="Director", start_date="2021"),
        ),
    )
    return user_id, proposal


def test_pending_ambiguous_proposal_exposes_fresh_comparison_and_bound_add_as_new_choice(db_session):
    user_id, proposal = _ambiguous_employment_proposal(db_session, "adviser-ambiguous-resolution@example.test")
    service = CandidateAdviserProfileProposalService(db_session)
    viewed = service.get_for_user(user_id, proposal.id)
    current = CandidateCVData.model_validate_json(db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )).structured_json)
    assert viewed.comparison.relationship is StructuredItemRelationship.AMBIGUOUS
    assert viewed.comparison_base_fingerprint == structured_authority_fingerprint(current)
    assert viewed.overlap_resolution is None and viewed.overlap_resolution_stale is False

    resolved = service.resolve_overlap(
        user_id, proposal.id,
        CandidateAdviserProfileProposalOverlapResolutionRequest(
            expected_revision=viewed.revision,
            expected_comparison_base_fingerprint=viewed.comparison_base_fingerprint,
            action=CandidateAdviserProfileProposalOverlapAction.ADD_AS_NEW,
        ),
    )
    assert resolved.revision == viewed.revision + 1
    assert resolved.overlap_resolution is not None
    assert resolved.overlap_resolution.incoming_fingerprint == viewed.comparison.incoming_fingerprint
    assert resolved.overlap_resolution.candidate_fingerprints == [
        value.fingerprint for value in viewed.comparison.candidate_matches
    ]
    assert resolved.overlap_resolution_stale is False


@pytest.mark.parametrize(
    "before,incoming,relationship",
    [
        ([], Skill(name="Python"), StructuredItemRelationship.NEW),
        ([Skill(name="Python")], Skill(name=" PYTHON "), StructuredItemRelationship.REINFORCEMENT),
        ([Skill(name="Python")], Skill(name="Python", category="Language"), StructuredItemRelationship.REFINEMENT),
        ([Skill(name="Python", category="Language")], Skill(name="Python", category="Data"), StructuredItemRelationship.CONFLICT),
    ],
)
def test_add_as_new_resolution_is_only_valid_for_ambiguous_comparison(db_session, before, incoming, relationship):
    user_id = _user(db_session, f"adviser-add-as-new-{relationship.value}@example.test")
    _seed_structured_profile(db_session, user_id, CandidateCVData(skills=before))
    clarification = _source(db_session, user_id)
    service = CandidateAdviserProfileProposalService(db_session)
    proposal = service.materialize_from_confirmed_clarification(
        user_id, clarification, SkillProposalUpdate(section="skills", operation="add", item=incoming)
    )
    viewed = service.get_for_user(user_id, proposal.id)
    assert viewed.comparison.relationship is relationship
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="only for a currently ambiguous"):
        service.resolve_overlap(
            user_id, proposal.id,
            CandidateAdviserProfileProposalOverlapResolutionRequest(
                expected_revision=viewed.revision,
                expected_comparison_base_fingerprint=viewed.comparison_base_fingerprint,
                action=CandidateAdviserProfileProposalOverlapAction.ADD_AS_NEW,
            ),
        )
    assert service.get_for_user(user_id, proposal.id).overlap_resolution is None


def test_adviser_ambiguity_resolution_rejects_wrong_revision_stale_base_and_other_user(client, db_session, monkeypatch):
    from app.api import deps

    def no_provider(*args, **kwargs):
        raise AssertionError("Adviser overlap resolution must not construct provider services")

    monkeypatch.setattr(deps, "_build_candidate_adviser_service", no_provider)
    monkeypatch.setattr(deps, "get_semantic_response_client", no_provider)
    token = register_and_login(client, "resolution-owner-auth@example.com")
    token_other = register_and_login(client, "resolution-other-auth@example.com")
    headers = auth_header(token)
    other_headers = auth_header(token_other)
    user_id, proposal = _ambiguous_employment_proposal(db_session, "resolution-owner-auth@example.com")
    service = CandidateAdviserProfileProposalService(db_session)
    viewed = service.get_for_user(user_id, proposal.id)
    wrong_revision = client.post(
        f"/api/v1/candidate-adviser/profile-proposals/{proposal.id}/resolve-overlap",
        headers=headers,
        json={"expected_revision": viewed.revision + 1,
              "expected_comparison_base_fingerprint": viewed.comparison_base_fingerprint,
              "action": "add_as_new"},
    )
    assert wrong_revision.status_code == 409
    stale_base = client.post(
        f"/api/v1/candidate-adviser/profile-proposals/{proposal.id}/resolve-overlap",
        headers=headers,
        json={"expected_revision": viewed.revision,
              "expected_comparison_base_fingerprint": "0" * 64,
              "action": "add_as_new"},
    )
    assert stale_base.status_code == 409
    missing = client.post(
        f"/api/v1/candidate-adviser/profile-proposals/{proposal.id}/resolve-overlap",
        headers=other_headers,
        json={"expected_revision": viewed.revision,
              "expected_comparison_base_fingerprint": viewed.comparison_base_fingerprint,
              "action": "add_as_new"},
    )
    assert missing.status_code == 404
    assert service.get_for_user(user_id, proposal.id).overlap_resolution is None
    before = db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )).structured_json
    success = client.post(
        f"/api/v1/candidate-adviser/profile-proposals/{proposal.id}/resolve-overlap",
        headers=headers,
        json={"expected_revision": viewed.revision,
              "expected_comparison_base_fingerprint": viewed.comparison_base_fingerprint,
              "action": "add_as_new"},
    )
    assert success.status_code == 200, success.text
    assert success.json()["overlap_resolution_stale"] is False
    assert db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )).structured_json == before


def test_adviser_overlap_resolution_edit_clears_but_reject_preserves_choice(db_session):
    user_id, proposal = _ambiguous_employment_proposal(db_session, "adviser-resolution-edit@example.test")
    service = CandidateAdviserProfileProposalService(db_session)
    viewed = service.get_for_user(user_id, proposal.id)
    chosen = service.resolve_overlap(
        user_id, proposal.id,
        CandidateAdviserProfileProposalOverlapResolutionRequest(
            expected_revision=viewed.revision,
            expected_comparison_base_fingerprint=viewed.comparison_base_fingerprint,
            action=CandidateAdviserProfileProposalOverlapAction.ADD_AS_NEW,
        ),
    )
    edited = service.edit_pending(
        user_id, proposal.id, expected_revision=chosen.revision,
        proposed_update=EmploymentProposalUpdate(
            section="employment", operation="add",
            item=Employment(employer="Example", title="Director of Engineering", start_date="2021"),
        ),
    )
    assert edited.overlap_resolution is None
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="overlaps current Profile information"):
        service.transfer_to_profile_revision(user_id, proposal.id, expected_revision=edited.revision)
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None
    viewed_again = service.get_for_user(user_id, proposal.id)
    chosen_again = service.resolve_overlap(
        user_id, proposal.id,
        CandidateAdviserProfileProposalOverlapResolutionRequest(
            expected_revision=viewed_again.revision,
            expected_comparison_base_fingerprint=viewed_again.comparison_base_fingerprint,
            action=CandidateAdviserProfileProposalOverlapAction.ADD_AS_NEW,
        ),
    )
    rejected = service.reject_pending(user_id, proposal.id, expected_revision=chosen_again.revision)
    assert rejected.state is CandidateAdviserProfileProposalState.REJECTED
    assert rejected.overlap_resolution == chosen_again.overlap_resolution
    assert rejected.comparison is None and rejected.comparison_base_fingerprint is None


@pytest.mark.parametrize("change_candidate_set", [False, True])
def test_stale_adviser_add_as_new_choice_blocks_transfer_after_profile_change(db_session, change_candidate_set):
    user_id, proposal = _ambiguous_employment_proposal(
        db_session, f"adviser-resolution-stale-{change_candidate_set}@example.test"
    )
    service = CandidateAdviserProfileProposalService(db_session)
    viewed = service.get_for_user(user_id, proposal.id)
    resolved = service.resolve_overlap(
        user_id, proposal.id,
        CandidateAdviserProfileProposalOverlapResolutionRequest(
            expected_revision=viewed.revision,
            expected_comparison_base_fingerprint=viewed.comparison_base_fingerprint,
            action=CandidateAdviserProfileProposalOverlapAction.ADD_AS_NEW,
        ),
    )
    row = db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
    data = CandidateCVData.model_validate_json(row.structured_json)
    if change_candidate_set:
        data.employment[1] = Employment(employer="Example", title="Principal", start_date="2021")
    else:
        data.projects.append(Project(name="Unrelated", description="Current authority changed"))
    row.structured_json = json.dumps(data.model_dump(mode="json"), sort_keys=True)
    db_session.commit()
    stale = service.get_for_user(user_id, proposal.id)
    assert stale.overlap_resolution is not None and stale.overlap_resolution_stale is True
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="stale"):
        service.transfer_to_profile_revision(user_id, proposal.id, expected_revision=resolved.revision)
    db_session.expire_all()
    assert service.get_for_user(user_id, proposal.id).state is CandidateAdviserProfileProposalState.PENDING
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None
    assert CandidateStructuredItemLineageService(db_session).read_history(user_id) == []


def test_resolved_ambiguous_add_transfers_as_new_without_canonical_mutation_then_confirms_truthful_lineage(db_session):
    user_id, proposal = _ambiguous_employment_proposal(db_session, "adviser-resolution-transfer@example.test")
    proposals = CandidateAdviserProfileProposalService(db_session)
    viewed = proposals.get_for_user(user_id, proposal.id)
    resolved = proposals.resolve_overlap(
        user_id, proposal.id,
        CandidateAdviserProfileProposalOverlapResolutionRequest(
            expected_revision=viewed.revision,
            expected_comparison_base_fingerprint=viewed.comparison_base_fingerprint,
            action=CandidateAdviserProfileProposalOverlapAction.ADD_AS_NEW,
        ),
    )
    before = db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id)).structured_json
    transfer = proposals.transfer_to_profile_revision(user_id, proposal.id, expected_revision=resolved.revision)
    assert transfer.profile_revision.proposed_structured.employment[-1].title == "Director"
    assert db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id)).structured_json == before
    assert len(CandidateStructuredItemLineageService(db_session).read_history(user_id)) == 0
    revisions = CandidateProfileRevisionService(db_session)
    reviewed = revisions.review(user_id, transfer.profile_revision.id, expected_revision=transfer.profile_revision.revision)
    confirmed = revisions.confirm(user_id, transfer.profile_revision.id, expected_revision=reviewed.revision)
    assert confirmed.state == "confirmed"
    current = CandidateCVData.model_validate_json(db_session.scalar(
        select(CandidateStructuredProfile.structured_json).where(CandidateStructuredProfile.user_id == user_id)
    ))
    assert [item.title for item in current.employment] == ["Engineer", "Manager", "Director"]
    event = next(row for row in CandidateStructuredItemLineageService(db_session).read_history(user_id)
                 if row.source_kind is StructuredItemSourceKind.CANDIDATE_ADVISER)
    assert event.relationship is StructuredItemRelationship.AMBIGUOUS
    assert event.item == Employment(employer="Example", title="Director", start_date="2021")
    assert event.predecessor_item is None


def test_exact_target_patch_clears_ambiguous_add_as_new_and_remains_transferable(db_session):
    user_id, proposal = _ambiguous_employment_proposal(db_session, "adviser-resolution-replace@example.test")
    service = CandidateAdviserProfileProposalService(db_session)
    viewed = service.get_for_user(user_id, proposal.id)
    resolved = service.resolve_overlap(
        user_id, proposal.id,
        CandidateAdviserProfileProposalOverlapResolutionRequest(
            expected_revision=viewed.revision,
            expected_comparison_base_fingerprint=viewed.comparison_base_fingerprint,
            action=CandidateAdviserProfileProposalOverlapAction.ADD_AS_NEW,
        ),
    )
    target = viewed.comparison.candidate_matches[0]
    replacement = EmploymentProposalUpdate(
        section="employment", operation="replace_exact", target_fingerprint=target.fingerprint,
        item=Employment(employer="Example", title="Director", start_date="2021"),
    )
    edited = service.edit_pending(
        user_id, proposal.id, expected_revision=resolved.revision, proposed_update=replacement
    )
    assert edited.overlap_resolution is None
    transfer = service.transfer_to_profile_revision(user_id, proposal.id, expected_revision=edited.revision)
    assert transfer.profile_revision.proposed_structured.employment[0].title == "Director"

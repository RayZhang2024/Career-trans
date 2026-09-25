import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from app.models.candidate_adviser import (
    CandidateAdviserAssessmentRecord,
    CandidateAdviserClarificationRecord,
    CandidateAdviserIntakeRecord,
)
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
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


def test_replace_exact_requires_well_formed_target_and_add_has_no_target() -> None:
    add = _update()
    replace = _update(operation="replace_exact", target_fingerprint="b" * 64)
    assert add.target_fingerprint is None
    assert replace.target_fingerprint == "b" * 64


@pytest.mark.parametrize("answer_kind", [ClarificationAnswerKind.CAREER_FACT, ClarificationAnswerKind.MIXED])
def test_materialization_accepts_confirmed_career_or_mixed_clarification(db_session, answer_kind) -> None:
    user_id = _user(db_session, f"proposal-source-{answer_kind}@example.com")
    clarification_id = _source(db_session, user_id, answer_kind=answer_kind)
    created = CandidateAdviserProfileProposalService(db_session).materialize_from_confirmed_clarification(
        user_id, clarification_id, _update()
    )
    assert created.state == "pending"
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
    assert after_revisions == before_revisions == []
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

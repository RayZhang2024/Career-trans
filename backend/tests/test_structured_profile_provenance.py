import json
from datetime import datetime, timezone

from sqlalchemy import event, select

from app.models.candidate_adviser import CandidateAdviserClarificationRecord
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateStructuredProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.user import User
from app.schemas.application_preparation import ApplicationPrepareRequest, ApplicationTargetInput
from app.schemas.application_preparation import ApplicationIdentitySnapshot
from app.schemas.candidate_adviser_profile_proposal import EmploymentProposalUpdate, SkillProposalUpdate
from app.schemas.cv_ingestion import CandidateCVData, Employment, Project, Skill
from app.schemas.candidate_adviser import CandidateAdviserIntake
from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredItemSourceKind,
    StructuredProfileItemLineageInput,
    StructuredProfileSection,
)
from app.services.application_preparation_service import ApplicationPreparationService
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_structured_item_lineage import CandidateStructuredItemLineageService
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.structured_profile_provenance_service import StructuredProfileProvenanceService
from app.services.user_job_discovery_service import UserJobDiscoveryService
from tests.test_profile import auth_header, register_and_login


def _user(session, email):
    user = User(email=email, password_hash="unused")
    session.add(user)
    session.commit()
    return user.id


def _source_rows(session, user_id, *, cv_filename="resume.pdf", adviser_item=None, adviser_employment_item=None):
    cv = CandidateCVIngestionDraft(
        user_id=user_id, state="confirmed",
        documents_json=json.dumps([{
            "provenance": {"filename": cv_filename, "media_type": "application/pdf"},
            "segments": [{"text": "PRIVATE EXTRACTED CV TEXT MUST NOT APPEAR"}],
        }]),
        merged_json="{}",
    )
    clarification = CandidateAdviserClarificationRecord(
        user_id=user_id, clarification_id="c" * 64, question_key="q" * 64,
        origin_assessment_fingerprint="a" * 64, question_text="Which platform did you deliver?",
        question_source_references_json="[]", priority_index=0, answer_text="Platform work",
        interpretation_json="{}", status="confirmed",
    )
    session.add_all([cv, clarification])
    session.flush()
    adviser = None
    if adviser_item is not None or adviser_employment_item is not None:
        update = (
            SkillProposalUpdate(section="skills", operation="add", item=adviser_item)
            if adviser_item is not None
            else EmploymentProposalUpdate(section="employment", operation="add", item=adviser_employment_item)
        )
        update_json = json.dumps(update.model_dump(mode="json"), sort_keys=True)
        adviser = CandidateAdviserProfileProposalRecord(
            user_id=user_id, proposal_key="p" * 64, state="transferred", revision=2,
            source_clarification_id=clarification.clarification_id,
            source_assessment_fingerprint="a" * 64,
            original_update_json=update_json, proposed_update_json=update_json,
            transferred_at=datetime.now(timezone.utc),
        )
        session.add(adviser)
        session.flush()
    employment_adviser = None
    if adviser_employment_item is not None:
        update = EmploymentProposalUpdate(section="employment", operation="add", item=adviser_employment_item)
        update_json = json.dumps(update.model_dump(mode="json"), sort_keys=True)
        employment_adviser = CandidateAdviserProfileProposalRecord(
            user_id=user_id, proposal_key="e" * 64, state="transferred", revision=2,
            source_clarification_id=clarification.clarification_id,
            source_assessment_fingerprint="a" * 64,
            original_update_json=update_json, proposed_update_json=update_json,
            transferred_at=datetime.now(timezone.utc),
        )
        session.add(employment_adviser)
        session.flush()
    return cv, clarification, adviser, employment_adviser


def test_current_provenance_is_ordered_typed_and_keeps_direct_and_predecessor_sources_distinct(db_session):
    user_id = _user(db_session, "provenance-full@example.test")
    python = Skill(name="Python")
    portal = Project(name="Portal", description="Current implementation")
    current_role = Employment(employer="Example", title="Director")
    current = CandidateCVData(skills=[python, python, Skill(name="Rust")], projects=[portal], employment=[current_role])
    db_session.add(CandidateStructuredProfile(user_id=user_id, structured_json=current.model_dump_json()))
    cv, clarification, adviser, employment_adviser = _source_rows(
        db_session, user_id, adviser_item=Skill(name=" PYTHON "),
        adviser_employment_item=current_role,
    )
    old_role = Employment(employer="Example", title="Engineer")
    manual = CandidateProfileRevisionRecord(
        user_id=user_id, active_user_id=None, state="confirmed", revision=2,
        base_profile_fingerprint="a" * 64, base_structured_fingerprint="b" * 64,
        base_editable_structured_fingerprint="c" * 64, proposed_profile_json=None,
        proposed_structured_json="{}", confirmed_at=datetime.now(timezone.utc),
    )
    db_session.add(manual)
    db_session.flush()
    lineage = CandidateStructuredItemLineageService(db_session)
    lineage.stage_many(user_id, [
        StructuredProfileItemLineageInput(
            section=StructuredProfileSection.SKILLS, item=python,
            source_kind=StructuredItemSourceKind.CV, source_ref=cv.id,
            relationship=StructuredItemRelationship.NEW,
        ),
        StructuredProfileItemLineageInput(
            section=StructuredProfileSection.SKILLS, item=python,
            source_kind=StructuredItemSourceKind.CANDIDATE_ADVISER, source_ref=adviser.id,
            relationship=StructuredItemRelationship.REINFORCEMENT,
            predecessor_item=Skill(name=" PYTHON "),
        ),
        StructuredProfileItemLineageInput(
            section=StructuredProfileSection.EMPLOYMENT, item=current_role,
            source_kind=StructuredItemSourceKind.CANDIDATE_ADVISER, source_ref=employment_adviser.id,
            relationship=StructuredItemRelationship.REFINEMENT, predecessor_item=old_role,
        ),
        StructuredProfileItemLineageInput(
            section=StructuredProfileSection.EMPLOYMENT, item=old_role,
            source_kind=StructuredItemSourceKind.CV, source_ref=cv.id,
            relationship=StructuredItemRelationship.NEW,
        ),
        StructuredProfileItemLineageInput(
            section=StructuredProfileSection.PROJECTS, item=portal,
            source_kind=StructuredItemSourceKind.MANUAL_PROFILE, source_ref=manual.id,
            relationship=StructuredItemRelationship.NEW,
        ),
        StructuredProfileItemLineageInput(
            section=StructuredProfileSection.SKILLS, item=Skill(name="Rust"),
            source_kind=StructuredItemSourceKind.CV, source_ref="missing-cv-source",
            relationship=StructuredItemRelationship.NEW,
        ),
    ])
    db_session.commit()

    projection = StructuredProfileProvenanceService(db_session).read_current(user_id, current)
    assert [(item.section.value, item.item_index) for item in projection.items] == [
        ("employment", 0), ("skills", 0), ("skills", 1), ("skills", 2), ("projects", 0),
    ]
    python_items = [item for item in projection.items if item.section is StructuredProfileSection.SKILLS and item.item.name == "Python"]
    assert [item.item_index for item in python_items] == [0, 1]
    assert python_items[0].item_fingerprint == python_items[1].item_fingerprint
    direct_sources = {event.source.kind for event in python_items[0].direct_events}
    assert direct_sources == {"cv", "candidate_adviser"}
    adviser_direct = next(event for event in python_items[0].direct_events if event.source.kind == "candidate_adviser")
    assert adviser_direct.source.proposal_item == Skill(name=" PYTHON ")
    cv_direct = next(event for event in python_items[0].direct_events if event.source.kind == "cv")
    assert cv_direct.source.filenames == ["resume.pdf"]
    assert "PRIVATE EXTRACTED" not in json.dumps(projection.model_dump(mode="json"))

    employment = projection.items[0]
    assert [event.source.kind for event in employment.direct_events] == ["candidate_adviser"]
    assert employment.direct_events[0].source.clarification_question == "Which platform did you deliver?"
    assert employment.direct_events[0].source.proposal_item == current_role
    assert len(employment.history) == 1
    assert employment.history[0].depth == 1
    assert employment.history[0].lineage_event.source.kind == "cv"
    assert employment.history[0].lineage_event.item == old_role
    assert not any(event.source.kind == "cv" for event in employment.direct_events)

    manual_item = projection.items[-1]
    assert manual_item.direct_events[0].source.kind == "manual_profile"
    assert manual_item.direct_events[0].source.available is True
    missing = next(item for item in projection.items if isinstance(item.item, Skill) and item.item.name == "Rust")
    assert missing.direct_events[0].source.available is False
    assert missing.direct_events[0].source.source_id == "missing-cv-source"


def test_legacy_duplicate_items_without_lineage_are_represented_as_unavailable(db_session):
    user_id = _user(db_session, "provenance-legacy@example.test")
    duplicate = Skill(name="Legacy")
    current = CandidateCVData(skills=[duplicate, duplicate])
    projection = StructuredProfileProvenanceService(db_session).read_current(user_id, current)
    assert len(projection.items) == 2
    assert [item.item_index for item in projection.items] == [0, 1]
    assert projection.items[0].item_fingerprint == projection.items[1].item_fingerprint
    assert all(not item.source_history_available and not item.direct_events and not item.history for item in projection.items)


def test_cyclic_predecessor_history_is_bounded_and_does_not_fail(db_session):
    user_id = _user(db_session, "provenance-cycle@example.test")
    current = Skill(name="Current")
    predecessor = Skill(name="Predecessor")
    lineage = CandidateStructuredItemLineageService(db_session)
    lineage.stage_many(user_id, [
        StructuredProfileItemLineageInput(
            section=StructuredProfileSection.SKILLS, item=current,
            source_kind=StructuredItemSourceKind.CV, source_ref="gone-current",
            relationship=StructuredItemRelationship.REFINEMENT, predecessor_item=predecessor,
        ),
        StructuredProfileItemLineageInput(
            section=StructuredProfileSection.SKILLS, item=predecessor,
            source_kind=StructuredItemSourceKind.CV, source_ref="gone-predecessor",
            relationship=StructuredItemRelationship.REFINEMENT, predecessor_item=current,
        ),
    ])
    db_session.commit()
    projection = StructuredProfileProvenanceService(db_session).read_current(
        user_id, CandidateCVData(skills=[current])
    )
    assert len(projection.items[0].direct_events) == 1
    assert len(projection.items[0].history) == 1
    assert projection.items[0].history[0].depth == 1


def test_profile_provenance_endpoint_is_read_only_and_downstream_inert(client, db_session, monkeypatch):
    from app.api import deps

    def no_provider(*args, **kwargs):
        raise AssertionError("Profile provenance reads must not construct semantic providers")

    monkeypatch.setattr(deps, "_build_candidate_adviser_service", no_provider)
    monkeypatch.setattr(deps, "get_semantic_response_client", no_provider)
    token = register_and_login(client, "provenance-endpoint@example.com")
    user = db_session.scalar(select(User).where(User.email == "provenance-endpoint@example.com"))
    current = CandidateCVData(skills=[Skill(name="Python")])
    db_session.add(CandidateStructuredProfile(user_id=user.id, structured_json=current.model_dump_json()))
    db_session.commit()
    CandidateAdviserService(db_session).save_intake(
        user.id, CandidateAdviserIntake(career_direction="Synthetic leadership path")
    )
    reader = CanonicalCandidateReadService(db_session)

    def downstream_values():
        snapshot = reader.read(user.id)
        context = reader.candidate_context(snapshot)
        request = ApplicationPrepareRequest(target=ApplicationTargetInput(job_text="Synthetic role " * 20))
        app_fp = ApplicationPreparationService._input_fingerprint(
            {"title": "Synthetic role"},
            ApplicationIdentitySnapshot(display_name="Example", email="example@example.test"),
            {}, snapshot.structured_profile, request,
        )
        return (
            snapshot,
            context,
            UserJobDiscoveryService.candidate_evaluation_fingerprint(context),
            app_fp,
            CandidateAdviserService(db_session).input_fingerprint(user.id, read_only=True),
        )

    before = downstream_values()
    writes: list[str] = []

    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
            writes.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", observe)
    try:
        response = client.get("/api/v1/profile/structured-provenance", headers=auth_header(token))
    finally:
        event.remove(db_session.bind, "before_cursor_execute", observe)
    assert response.status_code == 200
    assert response.json()["items"][0]["source_history_available"] is False
    assert writes == []

    CandidateStructuredItemLineageService(db_session).record(user.id, StructuredProfileItemLineageInput(
        section=StructuredProfileSection.SKILLS, item=Skill(name="Python"),
        source_kind=StructuredItemSourceKind.CV, source_ref="missing-source",
        relationship=StructuredItemRelationship.NEW,
    ))
    after = downstream_values()
    assert after == before


def test_profile_provenance_endpoint_returns_empty_for_no_structured_profile_and_is_user_scoped(client, db_session):
    token_a = register_and_login(client, "provenance-a@example.com")
    token_b = register_and_login(client, "provenance-b@example.com")
    user_a = db_session.scalar(select(User).where(User.email == "provenance-a@example.com"))
    user_b = db_session.scalar(select(User).where(User.email == "provenance-b@example.com"))
    _source_rows(db_session, user_b.id)
    secret_skill = Skill(name="Private User B skill")
    CandidateStructuredItemLineageService(db_session).record(user_b.id, StructuredProfileItemLineageInput(
        section=StructuredProfileSection.SKILLS, item=secret_skill,
        source_kind=StructuredItemSourceKind.CV, source_ref="missing-user-b-source",
        relationship=StructuredItemRelationship.NEW,
    ))
    db_session.add(CandidateStructuredProfile(user_id=user_b.id, structured_json=CandidateCVData(skills=[secret_skill]).model_dump_json()))
    db_session.commit()
    empty = client.get("/api/v1/profile/structured-provenance", headers=auth_header(token_a))
    own = client.get("/api/v1/profile/structured-provenance", headers=auth_header(token_b))
    assert empty.status_code == 200 and empty.json() == {"items": []}
    assert own.status_code == 200
    assert "Private User B skill" not in empty.text
    assert own.json()["items"][0]["item"]["name"] == "Private User B skill"

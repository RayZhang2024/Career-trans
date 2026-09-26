import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.models.application_preparation import ApplicationPreparation
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.candidate_structured_item_lineage import CandidateStructuredItemLineageRecord
from app.models.discovered_job import DiscoveredJob
from app.models.user import User
from app.models.user_job_discovery import UserJobEvaluation
from app.schemas.candidate import CareerEvidence
from app.schemas.cv_ingestion import (
    Achievement,
    CandidateCVData,
    Credential,
    CredentialType,
    Education,
    Employment,
    Project,
    Skill,
)
from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredItemSourceKind,
    StructuredProfileItemLineageInput,
    StructuredProfileSection,
)
from app.services.application_preparation_service import ApplicationPreparationService
from app.services.candidate_structured_item_lineage import (
    CandidateStructuredItemLineageConflict,
    CandidateStructuredItemLineageService,
)
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.structured_profile_comparison import StructuredProfileComparisonService
from app.services.structured_profile_identity import (
    structured_item_lineage_key,
    structured_profile_item_fingerprint,
)
from app.services.user_job_discovery_service import UserJobDiscoveryService


def _user(session, email="lineage@example.test"):
    row = User(email=email, password_hash="unused")
    session.add(row)
    session.commit()
    return row.id


def _event(section, item, *, source_ref="draft-1", relationship=StructuredItemRelationship.NEW, predecessor=None):
    return StructuredProfileItemLineageInput(
        section=section,
        item=item,
        source_kind=StructuredItemSourceKind.CV,
        source_ref=source_ref,
        relationship=relationship,
        predecessor_item=predecessor,
    )


def test_lineage_table_is_additive_user_owned_and_constrained(db_session):
    table = CandidateStructuredItemLineageRecord.__table__
    assert table.name == "candidate_structured_item_lineage"
    assert "user_id" in table.c and table.c.user_id.nullable is False
    assert {"candidate_structured_profiles", "candidate_evidence"}.isdisjoint(
        {foreign_key.target_fullname.split(".")[0] for foreign_key in table.foreign_keys}
    )
    assert any(constraint.name == "uq_structured_item_lineage_user_key" for constraint in table.constraints)
    assert {"cv", "manual_profile", "candidate_adviser"} == {value.value for value in StructuredItemSourceKind}
    assert {"new", "reinforcement", "refinement", "conflict", "ambiguous"} == {value.value for value in StructuredItemRelationship}


@pytest.mark.parametrize(
    "section,item",
    [
        (StructuredProfileSection.EMPLOYMENT, Employment(employer="A", title="B")),
        (StructuredProfileSection.EDUCATION, Education(institution="A", qualification="B")),
        (StructuredProfileSection.CREDENTIALS, Credential(name="A", credential_type=CredentialType.CERTIFICATION)),
        (StructuredProfileSection.SKILLS, Skill(name="A")),
        (StructuredProfileSection.PROJECTS, Project(name="A")),
        (StructuredProfileSection.ACHIEVEMENTS, Achievement(text="A")),
    ],
)
def test_lineage_records_typed_snapshot_for_each_section(db_session, section, item):
    user_id = _user(db_session, f"{section.value}@example.test")
    read = CandidateStructuredItemLineageService(db_session).record(user_id, _event(section, item))
    assert read.section is section
    assert read.item == item
    assert read.item_fingerprint == structured_profile_item_fingerprint(section, item)
    assert read.source_kind is StructuredItemSourceKind.CV


def test_lineage_is_idempotent_per_event_but_distinguishes_source_refs(db_session):
    user_id = _user(db_session)
    service = CandidateStructuredItemLineageService(db_session)
    item = Skill(name="Python")
    first = service.record(user_id, _event(StructuredProfileSection.SKILLS, item))
    repeated = service.record(user_id, _event(StructuredProfileSection.SKILLS, item))
    other_source = service.record(user_id, _event(StructuredProfileSection.SKILLS, item, source_ref="draft-2"))
    assert first.id == repeated.id
    assert other_source.id != first.id
    assert [row.source_ref for row in service.read_history(user_id)] == ["draft-1", "draft-2"]
    assert first.lineage_key == structured_item_lineage_key(
        user_id=user_id, section="skills", item_fingerprint=first.item_fingerprint,
        source_kind="cv", source_ref="draft-1", relationship="new", predecessor_fingerprint=None,
    )


def test_lineage_predecessor_is_typed_historical_snapshot_and_immutable(db_session):
    user_id = _user(db_session)
    old = Skill(name="Python", category=None)
    new = Skill(name="Python", category="Programming")
    service = CandidateStructuredItemLineageService(db_session)
    row = service.record(user_id, _event(
        StructuredProfileSection.SKILLS, new, relationship=StructuredItemRelationship.REFINEMENT, predecessor=old,
    ))
    assert row.predecessor_item == old
    assert row.predecessor_fingerprint == structured_profile_item_fingerprint("skills", old)
    record = db_session.get(CandidateStructuredItemLineageRecord, row.id)
    record.source_ref = "changed"
    with pytest.raises(ValueError, match="immutable"):
        db_session.flush()
    db_session.rollback()


def test_lineage_reads_are_user_scoped_and_attach_only_exact_current_items(db_session):
    user_a = _user(db_session)
    user_b = _user(db_session, "other@example.test")
    service = CandidateStructuredItemLineageService(db_session)
    x = Skill(name="Python")
    y = Skill(name="Python", category="Programming")
    service.record(user_a, _event("skills", x))
    assert service.read_history(user_b) == []
    current_x = service.read_current(user_a, CandidateCVData(skills=[x]))
    current_y = service.read_current(user_a, CandidateCVData(skills=[y]))
    assert [row.item for row in current_x[StructuredProfileSection.SKILLS]] == [x]
    assert current_y[StructuredProfileSection.SKILLS] == []
    assert all(not current_y[section] for section in StructuredProfileSection if section is not StructuredProfileSection.SKILLS)
    assert service.read_current(user_a, CandidateCVData())[StructuredProfileSection.SKILLS] == []


def test_lineage_service_fails_closed_on_wrong_typed_item_and_source_kind():
    with pytest.raises(ValidationError):
        StructuredProfileItemLineageInput(
            section="projects", item=Skill(name="Python"), source_kind="cv", source_ref="draft",
            relationship="new",
        )
    with pytest.raises(ValidationError):
        StructuredProfileItemLineageInput(
            section="skills", item=Skill(name="Python"), source_kind="legacy_unknown", source_ref="draft",
            relationship="new",
        )
    with pytest.raises(ValidationError):
        StructuredProfileItemLineageInput(
            section="skills", item=Skill(name="Python"), source_kind="cv", source_ref=" ",
            relationship="new",
        )


def test_lineage_rejects_false_reinforcement_with_unrelated_predecessor(db_session):
    user_id = _user(db_session)
    event = _event(
        "skills", Skill(name="Rust"), relationship=StructuredItemRelationship.REINFORCEMENT,
        predecessor=Skill(name="Python"),
    )
    with pytest.raises(CandidateStructuredItemLineageConflict, match="normalized-equivalent"):
        CandidateStructuredItemLineageService(db_session).stage(user_id, event)
    assert CandidateStructuredItemLineageService(db_session).read_history(user_id) == []


def test_normalized_reinforcement_keeps_deterministic_key_and_is_idempotent(db_session):
    user_id = _user(db_session)
    incoming = Skill(name="  PYTHON  ")
    predecessor = Skill(name="Python")
    event = _event(
        "skills", incoming, source_ref="cv-draft-normalized",
        relationship=StructuredItemRelationship.REINFORCEMENT,
        predecessor=predecessor,
    )
    service = CandidateStructuredItemLineageService(db_session)

    batch = service.stage_many(user_id, [event, event])
    assert len(batch) == 1
    first_key = batch[0].lineage_key
    db_session.commit()
    repeated = service.stage(user_id, event)
    db_session.commit()

    assert repeated.lineage_key == first_key
    rows = service.read_history(user_id)
    assert len(rows) == 1
    assert rows[0].predecessor_item == predecessor


def test_corrupt_persisted_item_or_predecessor_fingerprint_is_rejected(db_session):
    user_id = _user(db_session)
    service = CandidateStructuredItemLineageService(db_session)
    read = service.record(user_id, _event("skills", Skill(name="Python")))
    row = db_session.get(CandidateStructuredItemLineageRecord, read.id)
    db_session.execute(
        text("UPDATE candidate_structured_item_lineage SET item_json = :item WHERE id = :id"),
        {"item": json.dumps({"name": "Rust", "category": None}), "id": row.id},
    )
    db_session.commit()
    with pytest.raises(CandidateStructuredItemLineageConflict, match="does not match"):
        service.read_history(user_id)
    db_session.rollback()

    db_session.execute(
        text("UPDATE candidate_structured_item_lineage SET item_json = :item WHERE id = :id"),
        {"item": json.dumps({"name": "Python", "category": None}), "id": row.id},
    )
    db_session.commit()
    refined = service.record(user_id, _event(
        "skills", Skill(name="Python", category="Programming"),
        source_ref="draft-2", relationship=StructuredItemRelationship.REFINEMENT,
        predecessor=Skill(name="Python"),
    ))
    db_session.execute(
        text("UPDATE candidate_structured_item_lineage SET predecessor_item_json = :item WHERE id = :id"),
        {"item": json.dumps({"name": "Rust", "category": None}), "id": refined.id},
    )
    db_session.commit()
    with pytest.raises(CandidateStructuredItemLineageConflict, match="predecessor"):
        service.read_history(user_id)


def test_lineage_writes_do_not_change_canonical_or_downstream_fingerprints_or_artifacts(db_session):
    user_id = _user(db_session)
    data = CandidateCVData(skills=[Skill(name="Python")])
    profile_row = CandidateStructuredProfile(user_id=user_id, structured_json=data.model_dump_json())
    evidence_row = CandidateEvidenceRecord(
        user_id=user_id, fingerprint="f" * 64, evidence_type="achievement", title="Existing",
        text="Existing durable evidence", skills_json="[]", provenance_json="[]",
    )
    db_session.add_all([profile_row, evidence_row])
    db_session.commit()
    read_service = CanonicalCandidateReadService(db_session)
    before = read_service.read(user_id)
    before_context = read_service.candidate_context(before)
    before_discovery = UserJobDiscoveryService.candidate_evaluation_fingerprint(before_context)
    request = SimpleNamespace(target_pages=2, include_cover_letter=True, application_questions=[])
    before_application = ApplicationPreparationService._input_fingerprint(
        {"title": "Example"}, {"display_name": "Person"}, {}, data, request,
    )
    app_row = ApplicationPreparation(
        user_id=user_id, target_snapshot_json='{"target":"old"}', identity_snapshot_json='{}',
        preparation_input_fingerprint="a" * 64, preparation_contract_fingerprint="b" * 64,
        preparation_result_json='{"result":"old"}',
    )
    job = DiscoveredJob(
        identity_key="lineage-test-job", source="test", title="Engineer", url="https://example.test/job",
        content_hash="c" * 64, state="active", last_seen_at=datetime.now(timezone.utc),
        last_changed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([app_row, job])
    db_session.flush()
    evaluation = UserJobEvaluation(
        user_id=user_id, discovered_job_id=job.id, job_content_hash=job.content_hash,
        candidate_evaluation_fingerprint="d" * 64, evaluation_contract_fingerprint="e" * 64,
        job_snapshot_json='{"job":"old"}', evaluation_json='{"evaluation":"old"}',
    )
    db_session.add(evaluation)
    db_session.commit()
    old_artifacts = (app_row.target_snapshot_json, app_row.identity_snapshot_json, app_row.preparation_input_fingerprint, app_row.preparation_result_json,
                     evaluation.job_snapshot_json, evaluation.evaluation_json, evaluation.candidate_evaluation_fingerprint)
    profile_json_before = profile_row.structured_json
    evidence_before = [(row.id, row.fingerprint, row.title, row.text, row.skills_json, row.provenance_json)
                       for row in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))]

    CandidateStructuredItemLineageService(db_session).record(user_id, _event("skills", data.skills[0]))

    after = read_service.read(user_id)
    after_context = read_service.candidate_context(after)
    assert after.model_dump(mode="json") == before.model_dump(mode="json")
    assert after_context.model_dump(mode="json") == before_context.model_dump(mode="json")
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(after_context) == before_discovery
    assert ApplicationPreparationService._input_fingerprint(
        {"title": "Example"}, {"display_name": "Person"}, {}, data, request,
    ) == before_application
    assert profile_row.structured_json == profile_json_before
    assert [(row.id, row.fingerprint, row.title, row.text, row.skills_json, row.provenance_json)
            for row in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))] == evidence_before
    db_session.refresh(app_row)
    db_session.refresh(evaluation)
    assert (app_row.target_snapshot_json, app_row.identity_snapshot_json, app_row.preparation_input_fingerprint, app_row.preparation_result_json,
            evaluation.job_snapshot_json, evaluation.evaluation_json, evaluation.candidate_evaluation_fingerprint) == old_artifacts


def test_comparison_identity_type_boundary_and_all_sections():
    comparator = StructuredProfileComparisonService()
    cases = [
        ("employment", Employment(employer="A", title="B")),
        ("education", Education(institution="A", qualification="B")),
        ("credentials", Credential(name="A", credential_type=CredentialType.CERTIFICATION)),
        ("skills", Skill(name="A")),
        ("projects", Project(name="A")),
        ("achievements", Achievement(text="A")),
    ]
    for section, item in cases:
        result = comparator.compare(section, item, [item])
        assert result.section.value == section
        assert result.relationship is StructuredItemRelationship.REINFORCEMENT
        assert result.target_fingerprint == structured_profile_item_fingerprint(section, item)
        assert result.candidate_matches[0].item == item
    with pytest.raises(ValueError, match="not a projects"):
        comparator.compare("projects", Skill(name="Python"), [])
    with pytest.raises(ValueError, match="not a employment"):
        comparator.compare("employment", Education(institution="A", qualification="B"), [])
    with pytest.raises(ValueError, match="not a skills"):
        comparator.compare("skills", CareerEvidence(evidence_id="x", title="x", text="x"), [])


@pytest.mark.parametrize(
    "incoming,current,expected",
    [
        (Skill(name="  PYTHON  "), Skill(name="Python"), StructuredItemRelationship.REINFORCEMENT),
        (Skill(name="Python", category="Programming"), Skill(name="Python"), StructuredItemRelationship.REFINEMENT),
        (Skill(name="Python", category="Programming"), Skill(name="Python", category="Data"), StructuredItemRelationship.CONFLICT),
        (Skill(name="Go"), Skill(name="Python"), StructuredItemRelationship.NEW),
        (Project(name="P", skills=["Python", "SQL"]), Project(name="P", skills=["Python"]), StructuredItemRelationship.REFINEMENT),
        (Project(name="P", description="delivery"), Project(name="P"), StructuredItemRelationship.REFINEMENT),
        (Project(name="P", skills=[]), Project(name="P", skills=["Python"]), StructuredItemRelationship.CONFLICT),
        (Project(name="P", description="new conflicting text"), Project(name="P", description="old text"), StructuredItemRelationship.CONFLICT),
        (Project(name="Other"), Project(name="P"), StructuredItemRelationship.NEW),
    ],
)
def test_skill_and_project_comparison_taxonomy(incoming, current, expected):
    section = "skills" if isinstance(incoming, Skill) else "projects"
    assert StructuredProfileComparisonService().compare(section, incoming, [current]).relationship is expected


def test_employment_date_precision_and_conflict_and_ambiguity():
    compare = StructuredProfileComparisonService()
    current = Employment(employer="Acme", title="Engineer", start_date="2021", location=None)
    precise = Employment(employer=" ACME ", title="ENGINEER", start_date="Sep 2021", location="London")
    result = compare.compare("employment", precise, [current])
    assert result.relationship is StructuredItemRelationship.REFINEMENT
    assert result.target_fingerprint == structured_profile_item_fingerprint("employment", current)
    conflict = Employment(employer="Acme", title="Engineer", start_date="2022")
    assert compare.compare("employment", conflict, [current]).relationship is StructuredItemRelationship.CONFLICT
    unrelated = Employment(employer="Other", title="Engineer")
    assert compare.compare("employment", unrelated, [current]).relationship is StructuredItemRelationship.NEW
    same_company_roles = [
        Employment(employer="Acme", title="Engineer", start_date="2021"),
        Employment(employer="Acme", title="Manager", start_date="2021"),
    ]
    ambiguous = compare.compare("employment", Employment(employer="Acme", title="Director", start_date="2021"), same_company_roles)
    assert ambiguous.relationship is StructuredItemRelationship.AMBIGUOUS
    assert len(ambiguous.candidate_matches) == 2 and ambiguous.target_fingerprint is None


def test_education_credential_achievement_and_duplicate_ambiguity():
    compare = StructuredProfileComparisonService()
    assert compare.compare("education", Education(institution="U", qualification="MSc"), [Education(institution="U", qualification="BA")]).relationship is StructuredItemRelationship.NEW
    assert compare.compare("credentials", Credential(name="First Aid", credential_type=CredentialType.CERTIFICATION), [Credential(name="First Aid", credential_type=CredentialType.FORMAL_TRAINING)]).relationship is StructuredItemRelationship.NEW
    assert compare.compare("achievements", Achievement(text="Raised revenue"), [Achievement(text="Raised revenue significantly")]).relationship is StructuredItemRelationship.NEW
    exact = Skill(name="Python")
    assert compare.compare("skills", exact, [exact, exact]).relationship is StructuredItemRelationship.AMBIGUOUS
    projects = [Project(name="Migration"), Project(name=" Migration ")]
    ambiguous = compare.compare("projects", Project(name="MIGRATION"), projects)
    assert ambiguous.relationship is StructuredItemRelationship.AMBIGUOUS
    assert [item.item for item in ambiguous.candidate_matches] == projects
    education_candidates = [
        Education(institution="U", qualification="MSc", field_of_study="Computer Science"),
        Education(institution="U", qualification="MSc", field_of_study="Data Science"),
    ]
    assert compare.compare("education", Education(institution="U", qualification="MSc"), education_candidates).relationship is StructuredItemRelationship.AMBIGUOUS
    credential_candidates = [
        Credential(name="First Aid", credential_type=CredentialType.CERTIFICATION, issuer="A"),
        Credential(name="First Aid", credential_type=CredentialType.CERTIFICATION, issuer="B"),
    ]
    assert compare.compare("credentials", Credential(name="First Aid", credential_type=CredentialType.CERTIFICATION), credential_candidates).relationship is StructuredItemRelationship.AMBIGUOUS


def test_date_precision_rejects_incompatible_years_and_normalization_is_conservative():
    compare = StructuredProfileComparisonService()
    assert compare.compare("employment", Employment(employer="A", title="B", end_date="Sep 2022"), [Employment(employer="A", title="B", end_date="2021")]).relationship is StructuredItemRelationship.CONFLICT
    assert compare.compare("skills", Skill(name="ML"), [Skill(name="Machine Learning")]).relationship is StructuredItemRelationship.NEW
    assert compare.compare("skills", Skill(name="Python"), [Skill(name="Ｐｙｔｈｏｎ")]).relationship is StructuredItemRelationship.REINFORCEMENT

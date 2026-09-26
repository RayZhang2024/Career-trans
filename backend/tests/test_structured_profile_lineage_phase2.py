import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.application_preparation import ApplicationPreparation
from app.models.candidate_adviser import CandidateAdviserClarificationRecord
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_cv_ingestion import (
    CandidateCVIngestionDraft,
    CandidateEvidenceRecord,
    CandidateStructuredProfile,
)
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.discovered_job import DiscoveredJob
from app.models.user import User
from app.models.user_job_discovery import UserJobEvaluation
from app.core.database import Base
from app.schemas.candidate_adviser import (
    ClarificationAnswerKind,
    ClarificationInterpretation,
    ClarificationProposedEvidence,
)
from app.schemas.candidate_adviser_profile_proposal import SkillProposalUpdate
from app.schemas.cv_overlap_review import (
    CVOverlapResolution,
    CVOverlapResolutionAction,
    CVOverlapReviewPatch,
)
from app.schemas.cv_ingestion import (
    Achievement,
    CandidateCVData,
    Credential,
    CredentialType,
    CareerEvidenceDraft,
    Education,
    Employment,
    EvidenceProvenance,
    Project,
    Skill,
)
from app.schemas.profile_revision import (
    CandidateProfileRevisionState,
    EditableCandidateProfileData,
    EditableCandidateStructuredData,
)
from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredItemSourceKind,
    StructuredProfileItemLineageInput,
    StructuredProfileSection,
)
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalService
from app.services.candidate_structured_item_lineage import CandidateStructuredItemLineageService
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.cv_ingestion_service import CVIngestionService
from app.services.cv_overlap_review_service import CVOverlapReviewService
from app.services.profile_revision_service import CandidateProfileRevisionService, ProfileRevisionConflict
from app.services.structured_profile_identity import structured_profile_item_fingerprint
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.schemas.application_preparation import ApplicationPrepareRequest
from app.schemas.application_preparation import ApplicationTargetInput
from app.services.application_preparation_service import ApplicationPreparationService


def _user(session, email="phase2@example.test"):
    user = User(email=email, password_hash="unused")
    session.add(user)
    session.commit()
    return user.id


def _seed_current(session, user_id, data):
    row = CandidateStructuredProfile(user_id=user_id, structured_json=data.model_dump_json())
    session.add(row)
    ActiveCandidateEvidenceResolver(session).resolve(user_id, data)
    session.commit()
    return row


def _new_cv(session, user_id, data):
    draft = CandidateCVIngestionDraft(
        user_id=user_id,
        state="review_ready",
        documents_json="[]",
        merged_json=data.model_dump_json(),
    )
    session.add(draft)
    session.commit()
    return draft


def _confirm_cv(session, user_id, data):
    draft = _new_cv(session, user_id, data)
    count = CVIngestionService(session).confirm(user_id, draft.id)
    return draft, count


def _lineage_rows(session, user_id):
    return CandidateStructuredItemLineageService(session).read_history(user_id)


def _confirm_revision(session, user_id, *, structured=None, profile=None):
    service = CandidateProfileRevisionService(session)
    revision = service.create_or_resume(user_id)
    fields = set()
    if structured is not None:
        fields.add("proposed_structured")
    if profile is not None:
        fields.add("proposed_profile")
    saved = service.save(
        user_id,
        revision.id,
        expected_revision=revision.revision,
        patch_fields=fields,
        proposed_profile=profile,
        proposed_structured=structured,
    )
    reviewed = service.review(user_id, revision.id, expected_revision=saved.revision)
    confirmed = service.confirm(user_id, revision.id, expected_revision=reviewed.revision)
    return confirmed


def _adviser_source(session, user_id, *, skill="Rust"):
    clarification_id = "c" * 64
    interpretation = ClarificationInterpretation(
        answer_kind=ClarificationAnswerKind.CAREER_FACT,
        confirmed_context_summary="A synthetic confirmed career fact.",
        proposed_evidence=[ClarificationProposedEvidence(
            fact_domain="career", evidence_type="skill", title=skill,
            text=f"Used {skill}.", skills=[skill]
        )],
    )
    source = CandidateAdviserClarificationRecord(
        user_id=user_id,
        clarification_id=clarification_id,
        question_key="q" * 64,
        origin_assessment_fingerprint="a" * 64,
        question_text="What skill did you use?",
        question_source_references_json="[]",
        priority_index=0,
        answer_text="Rust",
        interpretation_json=interpretation.model_dump_json(),
        status="confirmed",
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(source)
    session.commit()
    return clarification_id


def _transfer_skill(session, user_id, *, item, operation="add", target=None, source_skill="Rust"):
    clarification_id = _adviser_source(session, user_id, skill=source_skill)
    proposal = CandidateAdviserProfileProposalService(session).materialize_from_confirmed_clarification(
        user_id,
        clarification_id,
        SkillProposalUpdate(
            section="skills", operation=operation, target_fingerprint=target, item=item
        ),
    )
    transfer = CandidateAdviserProfileProposalService(session).transfer_to_profile_revision(
        user_id, proposal.id, expected_revision=proposal.revision
    )
    return proposal, transfer


def _event_source(rows, section, item, source_kind):
    fingerprint = structured_profile_item_fingerprint(section, item)
    return [
        row for row in rows
        if row.section is section and row.item_fingerprint == fingerprint and row.source_kind is source_kind
    ]


def test_cv_confirmation_records_every_structured_section_from_draft_id(db_session):
    user_id = _user(db_session)
    data = CandidateCVData(
        employment=[Employment(employer="North", title="Engineer")],
        education=[Education(institution="Example U", qualification="BSc")],
        credentials=[Credential(name="Safety", credential_type=CredentialType.CERTIFICATION)],
        skills=[Skill(name="Python")],
        projects=[Project(name="Migration")],
        achievements=[Achievement(text="Improved reliability")],
    )
    draft, _ = _confirm_cv(db_session, user_id, data)
    current = CandidateStructuredItemLineageService(db_session).read_current(user_id, data)
    rows = [row for section in StructuredProfileSection for row in current[section]]
    assert len(rows) == 6
    assert {row.source_kind for row in rows} == {StructuredItemSourceKind.CV}
    assert {row.source_ref for row in rows} == {draft.id}
    assert {row.relationship for row in rows} == {StructuredItemRelationship.NEW}


def test_cv_normalized_reinforcement_preserves_incoming_representation_and_predecessor(db_session):
    user_id = _user(db_session)
    previous = Skill(name="Python")
    incoming = Skill(name="  PYTHON  ")
    _seed_current(db_session, user_id, CandidateCVData(skills=[previous]))

    draft = _new_cv(db_session, user_id, CandidateCVData(skills=[incoming]))
    CVIngestionService(db_session).confirm(user_id, draft.id)

    assert db_session.get(CandidateCVIngestionDraft, draft.id).state == "confirmed"
    current_row = db_session.scalar(
        select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id)
    )
    current = CandidateCVData.model_validate_json(current_row.structured_json)
    assert current.skills == [incoming]
    rows = _event_source(
        _lineage_rows(db_session, user_id), StructuredProfileSection.SKILLS,
        incoming, StructuredItemSourceKind.CV,
    )
    assert len(rows) == 1
    assert rows[0].source_ref == draft.id
    assert rows[0].relationship is StructuredItemRelationship.REINFORCEMENT
    assert rows[0].predecessor_item == previous
    assert rows[0].predecessor_fingerprint == structured_profile_item_fingerprint("skills", previous)
    assert rows[0].item_fingerprint == structured_profile_item_fingerprint("skills", incoming)
    assert rows[0].item_fingerprint != rows[0].predecessor_fingerprint


def test_two_cv_sources_accumulate_reinforcement_without_duplicate_current_items_or_artifact_changes(db_session):
    user_id = _user(db_session)
    data = CandidateCVData(skills=[Skill(name="Python")], evidence=[CareerEvidenceDraft(
        evidence_type="project", title="Service launch", text="Launched a synthetic service.",
        skills=["Python"], provenance=[EvidenceProvenance(
            document_sha256="f" * 64, segment_ids=["segment-1"]
        )],
    )])
    first, _ = _confirm_cv(db_session, user_id, data)
    current_row = db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
    before_json = current_row.structured_json
    before_snapshot = CanonicalCandidateReadService(db_session).read(user_id)
    before_context = CanonicalCandidateReadService.candidate_context(before_snapshot)
    before_discovery = UserJobDiscoveryService.candidate_evaluation_fingerprint(before_context)
    request = ApplicationPrepareRequest(target=ApplicationTargetInput(job_text="x" * 100))
    before_application_fp = ApplicationPreparationService._input_fingerprint(
        {"job": "synthetic"}, {"display_name": "Person"}, {}, before_snapshot.structured_profile, request
    )
    app = ApplicationPreparation(
        user_id=user_id, target_snapshot_json='{"target":"saved"}', identity_snapshot_json='{}',
        preparation_input_fingerprint="b" * 64, preparation_contract_fingerprint="c" * 64,
        preparation_result_json='{"result":"saved"}',
    )
    job = DiscoveredJob(
        identity_key="phase2-job", source="test", title="Engineer", url="https://example.test/job",
        content_hash="d" * 64, state="active", last_seen_at=datetime.now(timezone.utc),
        last_changed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([app, job])
    db_session.flush()
    evaluation = UserJobEvaluation(
        user_id=user_id, discovered_job_id=job.id, job_content_hash=job.content_hash,
        candidate_evaluation_fingerprint=before_discovery, evaluation_contract_fingerprint="e" * 64,
        job_snapshot_json='{"job":"saved"}', evaluation_json='{"evaluation":"saved"}',
    )
    db_session.add(evaluation)
    db_session.commit()
    evidence_before = [
        (row.id, row.fingerprint, row.evidence_type, row.title, row.text, row.skills_json, row.provenance_json)
        for row in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))
    ]
    artifact_before = (app.target_snapshot_json, app.identity_snapshot_json, app.preparation_input_fingerprint,
                       app.preparation_result_json, evaluation.job_snapshot_json, evaluation.evaluation_json,
                       evaluation.candidate_evaluation_fingerprint)

    second, _ = _confirm_cv(db_session, user_id, data)

    db_session.refresh(current_row)
    after_snapshot = CanonicalCandidateReadService(db_session).read(user_id)
    after_context = CanonicalCandidateReadService.candidate_context(after_snapshot)
    rows = CandidateStructuredItemLineageService(db_session).read_current(user_id, after_snapshot.structured_profile)
    python_history = rows[StructuredProfileSection.SKILLS]
    assert current_row.structured_json == before_json
    assert len(after_snapshot.structured_profile.skills) == 1
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(after_context) == before_discovery
    assert ApplicationPreparationService._input_fingerprint(
        {"job": "synthetic"}, {"display_name": "Person"}, {}, after_snapshot.structured_profile, request
    ) == before_application_fp
    assert {(row.source_ref, row.relationship) for row in python_history} == {
        (first.id, StructuredItemRelationship.NEW),
        (second.id, StructuredItemRelationship.REINFORCEMENT),
    }
    evidence_after = [
        (row.id, row.fingerprint, row.evidence_type, row.title, row.text, row.skills_json, row.provenance_json)
        for row in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))
    ]
    assert evidence_after == evidence_before
    db_session.refresh(app)
    db_session.refresh(evaluation)
    assert (app.target_snapshot_json, app.identity_snapshot_json, app.preparation_input_fingerprint,
            app.preparation_result_json, evaluation.job_snapshot_json, evaluation.evaluation_json,
            evaluation.candidate_evaluation_fingerprint) == artifact_before
    assert CVIngestionService(db_session).confirm(user_id, second.id) == 0
    assert len(CandidateStructuredItemLineageService(db_session).read_history(user_id)) == 2


def test_stage_is_caller_owned_uncommitted_and_batch_idempotent(db_session):
    user_id = _user(db_session)
    event = CandidateStructuredItemLineageService(db_session)
    source = StructuredProfileItemLineageInput(
        section="skills", item=Skill(name="Rust"), source_kind="manual_profile",
        source_ref="revision-1", relationship="new",
    )
    staged = event.stage_many(user_id, [source, source])
    assert len(staged) == 1
    assert len(event.read_history(user_id)) == 1
    db_session.rollback()
    assert event.read_history(user_id) == []


def test_stage_savepoint_recovers_unique_collision_without_rolling_back_caller_work(db_session, monkeypatch):
    user_id = _user(db_session)
    skill = Skill(name="Rust")
    event = StructuredProfileItemLineageInput(
        section="skills", item=skill, source_kind="manual_profile",
        source_ref="revision-1", relationship="new",
    )
    service = CandidateStructuredItemLineageService(db_session)
    existing = service.record(user_id, event)
    profile = CandidateProfile(user_id=user_id, display_name="Before")
    db_session.add(profile)
    db_session.commit()
    profile.display_name = "After"
    original_scalar = db_session.scalar
    miss_once = True

    def stale_lookup(statement, *args, **kwargs):
        nonlocal miss_once
        if miss_once:
            miss_once = False
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", stale_lookup, raising=False)
    recovered = service.stage_many(user_id, [event])
    assert len(recovered) == 1 and recovered[0].id == existing.id
    assert profile.display_name == "After"
    db_session.commit()
    assert db_session.get(CandidateProfile, profile.id).display_name == "After"
    assert len(service.read_history(user_id)) == 1


def test_stage_reuses_event_committed_by_another_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lineage-stage-idempotency.db'}")
    Base.metadata.create_all(bind=engine)
    first = Session(engine, expire_on_commit=False)
    second = Session(engine, expire_on_commit=False)
    try:
        user_id = _user(first, "lineage-concurrent@example.test")
        event = StructuredProfileItemLineageInput(
            section="skills", item=Skill(name="Python"), source_kind="cv",
            source_ref="draft-1", relationship="new",
        )
        first_result = CandidateStructuredItemLineageService(first).record(user_id, event)
        second_result = CandidateStructuredItemLineageService(second).stage(user_id, event)
        assert first_result.id == second_result.id
        second.commit()
        assert len(CandidateStructuredItemLineageService(first).read_history(user_id)) == 1
    finally:
        first.close()
        second.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.mark.parametrize(
    "before,incoming,expected",
    [
        (CandidateCVData(employment=[Employment(employer="Acme", title="Engineer", start_date="2021")]),
         CandidateCVData(employment=[Employment(employer="Acme", title="Engineer", start_date="Sep 2021")]),
         StructuredItemRelationship.REFINEMENT),
        (CandidateCVData(skills=[Skill(name="Python", category="Language")]),
         CandidateCVData(skills=[Skill(name="Python", category="Data")]),
         StructuredItemRelationship.CONFLICT),
        (CandidateCVData(employment=[Employment(employer="Acme", title="Engineer", start_date="2021"),
                                     Employment(employer="Acme", title="Manager", start_date="2021")]),
         CandidateCVData(employment=[Employment(employer="Acme", title="Director", start_date="2021")]),
         StructuredItemRelationship.AMBIGUOUS),
    ],
)
def test_cv_refinement_conflict_and_ambiguous_history(db_session, before, incoming, expected):
    user_id = _user(db_session, f"cv-{expected.value}@example.test")
    _seed_current(db_session, user_id, before)
    draft = _new_cv(db_session, user_id, incoming)
    overlap_service = CVOverlapReviewService(db_session)
    overlap = overlap_service.read(user_id, draft.id)
    review_item = overlap.items[0]
    selected_target = review_item.target_fingerprint or review_item.candidate_matches[0].fingerprint
    overlap_service.update(user_id, draft.id, CVOverlapReviewPatch(
        expected_review_revision=overlap.revision,
        resolutions=[CVOverlapResolution(
            item_key=review_item.item_key,
            action=CVOverlapResolutionAction.REPLACE_CURRENT,
            target_fingerprint=selected_target,
        )],
    ))
    CVIngestionService(db_session).confirm(user_id, draft.id)
    incoming_section = next(section for section in StructuredProfileSection if getattr(incoming, section.value))
    item = getattr(incoming, incoming_section.value)[0]
    rows = _event_source(_lineage_rows(db_session, user_id), incoming_section, item, StructuredItemSourceKind.CV)
    assert len(rows) == 1
    assert rows[0].source_ref == draft.id
    assert rows[0].relationship is expected
    if expected is StructuredItemRelationship.AMBIGUOUS:
        selected = next(match.item for match in review_item.candidate_matches if match.fingerprint == selected_target)
        assert rows[0].predecessor_item == selected
        assert rows[0].predecessor_fingerprint == selected_target
    else:
        old = getattr(before, incoming_section.value)[0]
        assert rows[0].predecessor_item == old
        assert rows[0].predecessor_fingerprint == structured_profile_item_fingerprint(incoming_section, old)


def test_confirmed_legacy_cv_is_not_backfilled(db_session):
    user_id = _user(db_session)
    _seed_current(db_session, user_id, CandidateCVData(skills=[Skill(name="Python")]))
    legacy = CandidateCVIngestionDraft(
        user_id=user_id, state="confirmed", documents_json="[]",
        merged_json=CandidateCVData(skills=[Skill(name="Python")]).model_dump_json(),
    )
    db_session.add(legacy)
    db_session.commit()
    assert CVIngestionService(db_session).confirm(user_id, legacy.id) == 0
    assert _lineage_rows(db_session, user_id) == []


def test_cv_lineage_failure_rolls_back_structured_evidence_and_source_state(db_session, monkeypatch):
    user_id = _user(db_session)
    before = CandidateCVData(employment=[Employment(employer="Old Co", title="Engineer")])
    structured = _seed_current(db_session, user_id, before)
    evidence_before = [
        (r.id, r.fingerprint, r.evidence_type, r.title, r.text, r.provenance_json)
        for r in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))
    ]
    incoming = CandidateCVData(employment=[Employment(employer="New Co", title="Engineer")])
    draft = _new_cv(db_session, user_id, incoming)

    def fail(*args, **kwargs):
        raise RuntimeError("injected lineage staging failure")

    monkeypatch.setattr(CandidateStructuredItemLineageService, "stage_many", fail)
    with pytest.raises(RuntimeError, match="lineage staging"):
        CVIngestionService(db_session).confirm(user_id, draft.id)
    db_session.expire_all()
    assert db_session.get(CandidateStructuredProfile, structured.id).structured_json == before.model_dump_json()
    assert db_session.get(CandidateCVIngestionDraft, draft.id).state == "review_ready"
    evidence_after = [
        (r.id, r.fingerprint, r.evidence_type, r.title, r.text, r.provenance_json)
        for r in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))
    ]
    assert evidence_after == evidence_before
    assert _lineage_rows(db_session, user_id) == []
    # The rolled-back session remains usable.
    assert db_session.scalar(select(CandidateStructuredProfile.id).where(CandidateStructuredProfile.user_id == user_id)) == structured.id


def test_manual_revision_records_only_changed_resulting_items(db_session):
    user_id = _user(db_session)
    before = CandidateCVData(skills=[Skill(name="Python")], projects=[Project(name="Portal", description="Initial")])
    _seed_current(db_session, user_id, before)
    after = EditableCandidateStructuredData(
        skills=[Skill(name="Python"), Skill(name="Rust"), Skill(name="Go")],
        projects=[Project(name="Portal", description="Rewritten")],
    )
    confirmed = _confirm_revision(db_session, user_id, structured=after)
    rows = _lineage_rows(db_session, user_id)
    assert confirmed.state == CandidateProfileRevisionState.CONFIRMED.value
    assert {(row.section, json.dumps(row.item.model_dump(mode="json"), sort_keys=True)) for row in rows} == {
        (StructuredProfileSection.SKILLS, json.dumps(Skill(name="Rust").model_dump(mode="json"), sort_keys=True)),
        (StructuredProfileSection.SKILLS, json.dumps(Skill(name="Go").model_dump(mode="json"), sort_keys=True)),
        (StructuredProfileSection.PROJECTS, json.dumps(Project(name="Portal", description="Rewritten").model_dump(mode="json"), sort_keys=True)),
    }
    assert all(row.source_kind is StructuredItemSourceKind.MANUAL_PROFILE for row in rows)
    assert {row.source_ref for row in rows} == {confirmed.id}
    project_lineage = next(row for row in rows if row.section is StructuredProfileSection.PROJECTS)
    assert project_lineage.relationship is StructuredItemRelationship.CONFLICT
    assert project_lineage.predecessor_item == Project(name="Portal", description="Initial")
    before_repeat = len(rows)
    CandidateProfileRevisionService(db_session).confirm(
        user_id, confirmed.id, expected_revision=1
    )
    assert len(_lineage_rows(db_session, user_id)) == before_repeat


def test_scalar_only_revision_creates_no_structured_lineage(db_session):
    user_id = _user(db_session)
    profile = CandidateProfile(user_id=user_id, display_name="Old")
    db_session.add(profile)
    db_session.commit()
    base = CandidateProfileRevisionService(db_session).create_or_resume(user_id)
    new_profile = base.proposed_profile.model_copy(update={"display_name": "New"})
    confirmed = _confirm_revision(db_session, user_id, profile=new_profile)
    assert confirmed.state == CandidateProfileRevisionState.CONFIRMED.value
    assert _lineage_rows(db_session, user_id) == []


def test_manual_revision_exact_duplicate_growth_is_blocked(db_session):
    user_id = _user(db_session)
    skill = Skill(name="Python")
    _seed_current(db_session, user_id, CandidateCVData(skills=[skill]))
    service = CandidateProfileRevisionService(db_session)
    created = service.create_or_resume(user_id)
    saved = service.save(
        user_id, created.id, expected_revision=created.revision,
        patch_fields={"proposed_structured"}, proposed_profile=None,
        proposed_structured=EditableCandidateStructuredData(skills=[skill, skill]),
    )
    with pytest.raises(ProfileRevisionConflict, match="same-fact duplicate"):
        service.review(user_id, created.id, expected_revision=saved.revision)
    assert CandidateCVData.model_validate_json(db_session.scalar(
        select(CandidateStructuredProfile.structured_json).where(CandidateStructuredProfile.user_id == user_id)
    )).skills == [skill]
    assert _lineage_rows(db_session, user_id) == []


def test_manual_revision_normalized_duplicate_growth_is_blocked(db_session):
    user_id = _user(db_session)
    previous = Skill(name="Python")
    _seed_current(db_session, user_id, CandidateCVData(skills=[previous]))
    service = CandidateProfileRevisionService(db_session)
    created = service.create_or_resume(user_id)
    proposed = EditableCandidateStructuredData(
        skills=[previous, Skill(name=" PYTHON ")]
    )
    saved = service.save(
        user_id, created.id, expected_revision=created.revision,
        patch_fields={"proposed_structured"}, proposed_profile=None,
        proposed_structured=proposed,
    )
    assert saved.structured_comparisons[0].comparison.relationship is StructuredItemRelationship.REINFORCEMENT
    with pytest.raises(ProfileRevisionConflict, match="same-fact duplicate"):
        service.review(user_id, created.id, expected_revision=saved.revision)
    assert _lineage_rows(db_session, user_id) == []


def test_manual_normalized_representation_replacement_is_allowed_and_projected(db_session):
    user_id = _user(db_session)
    previous = Skill(name="Python")
    incoming = Skill(name=" PYTHON ")
    _seed_current(db_session, user_id, CandidateCVData(skills=[previous]))
    service = CandidateProfileRevisionService(db_session)
    created = service.create_or_resume(user_id)
    saved = service.save(
        user_id, created.id, expected_revision=created.revision,
        patch_fields={"proposed_structured"}, proposed_profile=None,
        proposed_structured=EditableCandidateStructuredData(skills=[incoming]),
    )
    projection = saved.structured_comparisons[0].comparison
    assert projection.section is StructuredProfileSection.SKILLS
    assert projection.relationship is StructuredItemRelationship.REINFORCEMENT
    assert projection.current_item == previous
    reviewed = service.review(user_id, created.id, expected_revision=saved.revision)
    confirmed = service.confirm(user_id, created.id, expected_revision=reviewed.revision)
    assert confirmed.state == CandidateProfileRevisionState.CONFIRMED.value
    assert CandidateCVData.model_validate_json(db_session.scalar(
        select(CandidateStructuredProfile.structured_json).where(CandidateStructuredProfile.user_id == user_id)
    )).skills == [incoming]
    lineage = _lineage_rows(db_session, user_id)[0]
    assert lineage.relationship is StructuredItemRelationship.REINFORCEMENT
    assert lineage.predecessor_item == previous


def test_manual_revision_normalized_reinforcement_preserves_exact_predecessor(db_session):
    user_id = _user(db_session)
    previous = Skill(name="Python")
    incoming = Skill(name=" PYTHON ")
    _seed_current(db_session, user_id, CandidateCVData(skills=[previous]))

    confirmed = _confirm_revision(
        db_session, user_id,
        structured=EditableCandidateStructuredData(skills=[incoming]),
    )

    rows = _event_source(
        _lineage_rows(db_session, user_id), StructuredProfileSection.SKILLS,
        incoming, StructuredItemSourceKind.MANUAL_PROFILE,
    )
    assert confirmed.state == CandidateProfileRevisionState.CONFIRMED.value
    assert len(rows) == 1
    assert rows[0].source_ref == confirmed.id
    assert rows[0].relationship is StructuredItemRelationship.REINFORCEMENT
    assert rows[0].predecessor_item == previous
    assert rows[0].predecessor_fingerprint == structured_profile_item_fingerprint("skills", previous)
    assert rows[0].item_fingerprint == structured_profile_item_fingerprint("skills", incoming)
    assert rows[0].item_fingerprint != rows[0].predecessor_fingerprint


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ([Employment(employer="A", title="Engineer", start_date="2021")],
         [Employment(employer="A", title="Engineer", start_date="Sep 2021")],
         StructuredItemRelationship.REFINEMENT),
        ([Skill(name="Python", category="Language")],
         [Skill(name="Python", category="Data")],
         StructuredItemRelationship.CONFLICT),
        ([Employment(employer="A", title="Engineer", start_date="2021"),
          Employment(employer="A", title="Manager", start_date="2021")],
         [Employment(employer="A", title="Director", start_date="2021")],
         StructuredItemRelationship.AMBIGUOUS),
    ],
)
def test_manual_revision_relationship_history(db_session, before, after, expected):
    user_id = _user(db_session, f"manual-{expected.value}@example.test")
    section = "employment" if isinstance(after[0], Employment) else "skills"
    _seed_current(db_session, user_id, CandidateCVData(**{section: before}))
    editable = EditableCandidateStructuredData(**{section: after})
    confirmed = _confirm_revision(db_session, user_id, structured=editable)
    item = after[0]
    rows = _event_source(_lineage_rows(db_session, user_id), StructuredProfileSection(section), item, StructuredItemSourceKind.MANUAL_PROFILE)
    assert len(rows) == 1 and rows[0].relationship is expected
    if expected is StructuredItemRelationship.AMBIGUOUS:
        assert rows[0].predecessor_item is None
    else:
        assert rows[0].predecessor_item == before[0]
        assert rows[0].source_ref == confirmed.id


def test_manual_revision_stage_failure_rolls_back_all_confirmed_authorities(db_session, monkeypatch):
    user_id = _user(db_session)
    before = CandidateCVData(skills=[Skill(name="Python")])
    structured = _seed_current(db_session, user_id, before)
    profile = CandidateProfile(user_id=user_id, display_name="Old")
    db_session.add(profile)
    db_session.commit()
    evidence_before = [
        (r.id, r.fingerprint, r.title, r.text, r.provenance_json)
        for r in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))
    ]
    service = CandidateProfileRevisionService(db_session)
    created = service.create_or_resume(user_id)
    proposed = EditableCandidateStructuredData(skills=[Skill(name="Rust")])
    saved = service.save(user_id, created.id, expected_revision=created.revision,
                         patch_fields={"proposed_structured"}, proposed_profile=None, proposed_structured=proposed)
    reviewed = service.review(user_id, created.id, expected_revision=saved.revision)

    def fail(*args, **kwargs):
        raise RuntimeError("injected lineage failure")

    monkeypatch.setattr(CandidateStructuredItemLineageService, "stage_many", fail)
    with pytest.raises(RuntimeError, match="lineage failure"):
        service.confirm(user_id, created.id, expected_revision=reviewed.revision)
    db_session.expire_all()
    assert db_session.get(CandidateStructuredProfile, structured.id).structured_json == before.model_dump_json()
    assert db_session.get(CandidateProfile, profile.id).display_name == "Old"
    revision = db_session.get(CandidateProfileRevisionRecord, created.id)
    assert revision.state == "review_ready" and revision.active_user_id == user_id
    assert [(r.id, r.fingerprint, r.title, r.text, r.provenance_json)
            for r in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))] == evidence_before
    assert _lineage_rows(db_session, user_id) == []


def test_transferred_adviser_item_is_attributed_exactly_and_other_edits_are_manual(db_session):
    user_id = _user(db_session)
    _seed_current(db_session, user_id, CandidateCVData(skills=[Skill(name="Python")]))
    proposal, transfer = _transfer_skill(db_session, user_id, item=Skill(name="Rust"))
    revision = transfer.profile_revision
    proposed = revision.proposed_structured.model_copy(
        update={"skills": [*revision.proposed_structured.skills, Skill(name="Go")]}
    )
    service = CandidateProfileRevisionService(db_session)
    saved = service.save(user_id, revision.id, expected_revision=revision.revision,
                         patch_fields={"proposed_structured"}, proposed_profile=None, proposed_structured=proposed)
    reviewed = service.review(user_id, revision.id, expected_revision=saved.revision)
    confirmed = service.confirm(user_id, revision.id, expected_revision=reviewed.revision)
    rows = _lineage_rows(db_session, user_id)
    rust = _event_source(rows, StructuredProfileSection.SKILLS, Skill(name="Rust"), StructuredItemSourceKind.CANDIDATE_ADVISER)
    go = _event_source(rows, StructuredProfileSection.SKILLS, Skill(name="Go"), StructuredItemSourceKind.MANUAL_PROFILE)
    assert len(rust) == len(go) == 1
    assert rust[0].source_ref == proposal.id and rust[0].source_ref != proposal.source_clarification_id
    assert rust[0].relationship is StructuredItemRelationship.NEW
    assert go[0].source_ref == confirmed.id and go[0].relationship is StructuredItemRelationship.NEW
    assert not _event_source(rows, StructuredProfileSection.SKILLS, Skill(name="Python"), StructuredItemSourceKind.MANUAL_PROFILE)
    CandidateProfileRevisionService(db_session).confirm(
        user_id, confirmed.id, expected_revision=1
    )
    assert len(_lineage_rows(db_session, user_id)) == len(rows)


def test_edited_adviser_suggestion_falls_back_to_manual_profile_source(db_session):
    user_id = _user(db_session)
    _seed_current(db_session, user_id, CandidateCVData(skills=[Skill(name="Python")]))
    proposal, transfer = _transfer_skill(db_session, user_id, item=Skill(name="Rust"))
    service = CandidateProfileRevisionService(db_session)
    structured = transfer.profile_revision.proposed_structured.model_copy(
        update={"skills": [Skill(name="Python"), Skill(name="Rust", category="Programming")]}
    )
    saved = service.save(user_id, transfer.profile_revision.id,
                         expected_revision=transfer.profile_revision.revision,
                         patch_fields={"proposed_structured"}, proposed_profile=None,
                         proposed_structured=structured)
    reviewed = service.review(user_id, transfer.profile_revision.id, expected_revision=saved.revision)
    confirmed = service.confirm(user_id, transfer.profile_revision.id, expected_revision=reviewed.revision)
    rows = _lineage_rows(db_session, user_id)
    updated = Skill(name="Rust", category="Programming")
    manual = _event_source(rows, StructuredProfileSection.SKILLS, updated, StructuredItemSourceKind.MANUAL_PROFILE)
    assert len(manual) == 1 and manual[0].source_ref == confirmed.id
    assert manual[0].relationship is StructuredItemRelationship.NEW
    assert not _event_source(rows, StructuredProfileSection.SKILLS, updated, StructuredItemSourceKind.CANDIDATE_ADVISER)
    assert db_session.get(CandidateAdviserProfileProposalRecord, proposal.id).state == "transferred"


def test_adviser_refinement_uses_actual_current_predecessor(db_session):
    user_id = _user(db_session)
    old = Skill(name="Python")
    _seed_current(db_session, user_id, CandidateCVData(skills=[old]))
    proposal, transfer = _transfer_skill(
        db_session,
        user_id,
        item=Skill(name="Python", category="Programming"),
        operation="replace_exact",
        target=structured_profile_item_fingerprint("skills", old),
    )
    reviewed = CandidateProfileRevisionService(db_session).review(
        user_id, transfer.profile_revision.id, expected_revision=transfer.profile_revision.revision
    )
    CandidateProfileRevisionService(db_session).confirm(
        user_id, transfer.profile_revision.id, expected_revision=reviewed.revision
    )
    new = Skill(name="Python", category="Programming")
    lineage = _event_source(_lineage_rows(db_session, user_id), StructuredProfileSection.SKILLS,
                            new, StructuredItemSourceKind.CANDIDATE_ADVISER)
    assert len(lineage) == 1
    assert lineage[0].source_ref == proposal.id
    assert lineage[0].relationship is StructuredItemRelationship.REFINEMENT
    assert lineage[0].predecessor_item == old
    assert lineage[0].predecessor_fingerprint == structured_profile_item_fingerprint("skills", old)


def test_adviser_normalized_reinforcement_keeps_proposal_attribution(db_session):
    user_id = _user(db_session)
    previous = Skill(name="Python")
    incoming = Skill(name=" PYTHON ")
    _seed_current(db_session, user_id, CandidateCVData(skills=[previous]))
    proposal, transfer = _transfer_skill(
        db_session, user_id, item=incoming, source_skill=" PYTHON "
    )

    reviewed = CandidateProfileRevisionService(db_session).review(
        user_id, transfer.profile_revision.id,
        expected_revision=transfer.profile_revision.revision,
    )
    confirmed = CandidateProfileRevisionService(db_session).confirm(
        user_id, transfer.profile_revision.id, expected_revision=reviewed.revision,
    )

    assert confirmed.state == CandidateProfileRevisionState.CONFIRMED.value
    current_row = db_session.scalar(
        select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id)
    )
    current = CandidateCVData.model_validate_json(current_row.structured_json)
    assert current.skills == [previous]
    rows = _event_source(
        _lineage_rows(db_session, user_id), StructuredProfileSection.SKILLS,
        previous, StructuredItemSourceKind.CANDIDATE_ADVISER,
    )
    assert len(rows) == 1
    assert rows[0].source_ref == proposal.id
    assert rows[0].relationship is StructuredItemRelationship.REINFORCEMENT
    assert rows[0].predecessor_item == previous


@pytest.mark.parametrize("before,incoming,expected", [
    (Skill(name="Python"), Skill(name="Python", category="Language"), StructuredItemRelationship.REFINEMENT),
    (Skill(name="Python", category="Language"), Skill(name="Python", category="Data"), StructuredItemRelationship.CONFLICT),
    ([Skill(name="Python"), Skill(name="Python")], Skill(name="Python"), StructuredItemRelationship.AMBIGUOUS),
])
def test_adviser_add_overlap_is_provider_free_and_stays_pending(
    db_session, before, incoming, expected
):
    user_id = _user(db_session, f"adviser-overlap-{expected.value}@example.test")
    current_items = before if isinstance(before, list) else [before]
    _seed_current(db_session, user_id, CandidateCVData(skills=current_items))
    clarification_id = _adviser_source(db_session, user_id, skill=incoming.name)
    proposals = CandidateAdviserProfileProposalService(db_session)
    proposal = proposals.materialize_from_confirmed_clarification(
        user_id, clarification_id,
        SkillProposalUpdate(section="skills", operation="add", item=incoming),
    )
    read = proposals.get_for_user(user_id, proposal.id)
    assert read.comparison is not None
    assert read.comparison.relationship is expected
    with pytest.raises(ValueError, match="overlaps current Profile information"):
        proposals.transfer_to_profile_revision(user_id, proposal.id, expected_revision=proposal.revision)
    assert proposals.get_for_user(user_id, proposal.id).state.value == "pending"
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None
    assert _lineage_rows(db_session, user_id) == []


def test_adviser_add_is_reclassified_against_fresh_current_state_at_transfer(db_session):
    user_id = _user(db_session)
    clarification_id = _adviser_source(db_session, user_id, skill="Python")
    proposals = CandidateAdviserProfileProposalService(db_session)
    proposal = proposals.materialize_from_confirmed_clarification(
        user_id, clarification_id,
        SkillProposalUpdate(section="skills", operation="add", item=Skill(name="Python")),
    )
    assert proposals.get_for_user(user_id, proposal.id).comparison.relationship is StructuredItemRelationship.NEW
    _seed_current(
        db_session, user_id,
        CandidateCVData(skills=[Skill(name="Python", category="Language")]),
    )
    with pytest.raises(ValueError, match="overlaps current Profile information"):
        proposals.transfer_to_profile_revision(user_id, proposal.id, expected_revision=proposal.revision)
    assert proposals.get_for_user(user_id, proposal.id).state.value == "pending"
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None


def test_adviser_replace_exact_conflict_and_identical_target_behavior(db_session):
    user_id = _user(db_session)
    old = Skill(name="Python", category="Language")
    _seed_current(db_session, user_id, CandidateCVData(skills=[old]))
    proposal, transfer = _transfer_skill(
        db_session, user_id,
        item=Skill(name="Python", category="Data"),
        operation="replace_exact",
        target=structured_profile_item_fingerprint("skills", old),
    )
    service = CandidateProfileRevisionService(db_session)
    reviewed = service.review(user_id, transfer.profile_revision.id,
                              expected_revision=transfer.profile_revision.revision)
    service.confirm(user_id, transfer.profile_revision.id, expected_revision=reviewed.revision)
    lineage = _event_source(_lineage_rows(db_session, user_id), StructuredProfileSection.SKILLS,
                            Skill(name="Python", category="Data"), StructuredItemSourceKind.CANDIDATE_ADVISER)
    assert len(lineage) == 1 and lineage[0].relationship is StructuredItemRelationship.CONFLICT
    assert lineage[0].predecessor_item == old

    other_user = _user(db_session, "adviser-identical-target@example.test")
    identical = Skill(name="Rust")
    _seed_current(db_session, other_user, CandidateCVData(skills=[identical]))
    same_proposal, same_transfer = _transfer_skill(
        db_session, other_user, item=identical, operation="replace_exact",
        target=structured_profile_item_fingerprint("skills", identical),
    )
    same_reviewed = service.review(other_user, same_transfer.profile_revision.id,
                                   expected_revision=same_transfer.profile_revision.revision)
    service.confirm(other_user, same_transfer.profile_revision.id,
                    expected_revision=same_reviewed.revision)
    assert CandidateCVData.model_validate_json(db_session.scalar(
        select(CandidateStructuredProfile.structured_json).where(CandidateStructuredProfile.user_id == other_user)
    )).skills == [identical]
    same_lineage = _event_source(_lineage_rows(db_session, other_user), StructuredProfileSection.SKILLS,
                                 identical, StructuredItemSourceKind.CANDIDATE_ADVISER)
    assert len(same_lineage) == 1 and same_lineage[0].source_ref == same_proposal.id
    assert same_lineage[0].relationship is StructuredItemRelationship.REINFORCEMENT


def test_cv_then_adviser_normalized_add_retains_one_fact_and_both_sources(db_session):
    user_id = _user(db_session)
    cv, _ = _confirm_cv(db_session, user_id, CandidateCVData(skills=[Skill(name="Python")]))
    proposal, transfer = _transfer_skill(
        db_session, user_id, item=Skill(name=" PYTHON "), source_skill=" PYTHON "
    )
    assert transfer.profile_revision.proposed_structured.skills == [Skill(name="Python")]
    service = CandidateProfileRevisionService(db_session)
    reviewed = service.review(
        user_id, transfer.profile_revision.id, expected_revision=transfer.profile_revision.revision
    )
    service.confirm(user_id, transfer.profile_revision.id, expected_revision=reviewed.revision)
    assert CandidateCVData.model_validate_json(db_session.scalar(
        select(CandidateStructuredProfile.structured_json).where(CandidateStructuredProfile.user_id == user_id)
    )).skills == [Skill(name="Python")]
    lineage = _lineage_rows(db_session, user_id)
    python_sources = [
        value for value in lineage
        if value.item == Skill(name="Python")
        and value.source_kind in {StructuredItemSourceKind.CV, StructuredItemSourceKind.CANDIDATE_ADVISER}
    ]
    assert {(value.source_kind, value.source_ref) for value in python_sources} == {
        (StructuredItemSourceKind.CV, cv.id),
        (StructuredItemSourceKind.CANDIDATE_ADVISER, proposal.id),
    }


def test_cv_then_adviser_conflicting_add_is_blocked_without_profile_revision(db_session):
    user_id = _user(db_session)
    cv, _ = _confirm_cv(
        db_session, user_id,
        CandidateCVData(skills=[Skill(name="Python", category="Language")]),
    )
    clarification_id = _adviser_source(db_session, user_id, skill="Python")
    proposals = CandidateAdviserProfileProposalService(db_session)
    incoming = Skill(name="Python", category="Data")
    proposal = proposals.materialize_from_confirmed_clarification(
        user_id, clarification_id,
        SkillProposalUpdate(section="skills", operation="add", item=incoming),
    )
    assert proposals.get_for_user(user_id, proposal.id).comparison.relationship is StructuredItemRelationship.CONFLICT
    with pytest.raises(ValueError, match="overlaps current Profile information"):
        proposals.transfer_to_profile_revision(user_id, proposal.id, expected_revision=proposal.revision)
    assert CandidateCVData.model_validate_json(db_session.scalar(
        select(CandidateStructuredProfile.structured_json).where(CandidateStructuredProfile.user_id == user_id)
    )).skills == [Skill(name="Python", category="Language")]
    assert proposals.get_for_user(user_id, proposal.id).state.value == "pending"
    assert db_session.scalar(select(CandidateProfileRevisionRecord).where(
        CandidateProfileRevisionRecord.user_id == user_id
    )) is None
    lineage = _lineage_rows(db_session, user_id)
    assert len(lineage) == 1 and lineage[0].source_ref == cv.id


def test_corrupt_adviser_revision_link_fails_closed_and_confirmation_rollback_preserves_transfer(db_session):
    user_id = _user(db_session)
    before = CandidateCVData(skills=[Skill(name="Python")])
    _seed_current(db_session, user_id, before)
    proposal, transfer = _transfer_skill(db_session, user_id, item=Skill(name="Rust"))
    reviewed = CandidateProfileRevisionService(db_session).review(
        user_id, transfer.profile_revision.id, expected_revision=transfer.profile_revision.revision
    )
    record = db_session.get(CandidateAdviserProfileProposalRecord, proposal.id)
    record.state = "rejected"
    db_session.commit()
    service = CandidateProfileRevisionService(db_session)
    with pytest.raises(ProfileRevisionConflict, match="valid transferred source"):
        service.confirm(user_id, transfer.profile_revision.id, expected_revision=reviewed.revision)
    assert _lineage_rows(db_session, user_id) == []
    assert db_session.get(CandidateStructuredProfile, db_session.scalar(
        select(CandidateStructuredProfile.id).where(CandidateStructuredProfile.user_id == user_id)
    )).structured_json == before.model_dump_json()
    assert db_session.get(CandidateProfileRevisionRecord, transfer.profile_revision.id).state == "review_ready"


def test_adviser_lineage_failure_rolls_back_revision_but_preserves_transferred_proposal(db_session, monkeypatch):
    user_id = _user(db_session)
    before = CandidateCVData(employment=[Employment(employer="Old Co", title="Engineer")], skills=[Skill(name="Python")])
    structured = _seed_current(db_session, user_id, before)
    proposal, transfer = _transfer_skill(db_session, user_id, item=Skill(name="Rust"))
    reviewed = CandidateProfileRevisionService(db_session).review(
        user_id, transfer.profile_revision.id, expected_revision=transfer.profile_revision.revision
    )
    evidence_before = [
        (r.id, r.fingerprint, r.title, r.text, r.provenance_json)
        for r in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))
    ]

    def fail(*args, **kwargs):
        raise RuntimeError("injected Adviser lineage failure")

    monkeypatch.setattr(CandidateStructuredItemLineageService, "stage_many", fail)
    with pytest.raises(RuntimeError, match="Adviser lineage"):
        CandidateProfileRevisionService(db_session).confirm(
            user_id, transfer.profile_revision.id, expected_revision=reviewed.revision
        )
    db_session.expire_all()
    assert db_session.get(CandidateStructuredProfile, structured.id).structured_json == before.model_dump_json()
    assert db_session.get(CandidateProfileRevisionRecord, transfer.profile_revision.id).state == "review_ready"
    assert db_session.get(CandidateAdviserProfileProposalRecord, proposal.id).state == "transferred"
    assert [(r.id, r.fingerprint, r.title, r.text, r.provenance_json)
            for r in db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id))] == evidence_before
    assert _lineage_rows(db_session, user_id) == []

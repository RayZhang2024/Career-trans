import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.models.application_preparation import ApplicationPreparation
from app.models.application_tracking import ApplicationTrackingEvent, ApplicationTrackingRecord
from app.models.candidate_adviser import (
    CandidateAdviserAssessmentRecord,
    CandidateAdviserClarificationRecord,
    CandidateAdviserIntakeRecord,
)
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_cv_ingestion import (
    CandidateCVIngestionDraft,
    CandidateCVReviewBaseline,
    CandidateEvidenceRecord,
    CandidateStructuredProfile,
)
from app.models.candidate_cv_overlap_review import CandidateCVOverlapReviewRecord
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.candidate_structured_item_lineage import CandidateStructuredItemLineageRecord
from app.models.user import User
from app.models.user_job_discovery import DiscoveryRun
from app.core.database import Base
from app.schemas.candidate import CandidateEvidenceMaterializationStatus
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessmentContent,
    CandidateAdviserIntake,
    ClarificationInterpretation,
)
from app.schemas.candidate_compatibility import (
    CandidateCompatibilityStatus,
    CandidateDataReconciliationAction,
    CandidateSchemaCompatibilityRead,
    CandidateSchemaStatus,
)
from app.schemas.cv_ingestion import CandidateCVData, CareerEvidenceDraft
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_compatibility_inspector import CandidatePhysicalSchemaInspector
from app.services.candidate_legacy_compatibility_inspector import CandidateLegacyCompatibilityInspector
from app.services.candidate_legacy_data_reconciliation import (
    CandidateDataReconciliationBlocked,
    CandidateLegacyDataReconciliationService,
)
from app.services.candidate_sqlite_schema_repair import CandidateSQLiteSchemaCompatibilityRepairService
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.career_evidence_fingerprint import (
    career_evidence_fingerprint,
    legacy_career_evidence_fingerprint,
)
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.structured_profile_provenance_service import StructuredProfileProvenanceService


def _user(session, user_id: str) -> None:
    session.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="fixture"))
    session.commit()


def _factory(session):
    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)


def _service(session):
    # The fixture uses SQLite StaticPool, so close its setup/read transaction
    # before the reconciler opens its independently owned session.
    session.rollback()
    return CandidateLegacyDataReconciliationService(_factory(session))


def _data(**values) -> CandidateCVData:
    return CandidateCVData.model_validate(values)


def _draft(session, user_id, draft_id, data, *, state="confirmed", updated_at=None, runtime=None):
    stamp = updated_at or datetime(2020, 1, 1, tzinfo=timezone.utc)
    row = CandidateCVIngestionDraft(
        id=draft_id,
        user_id=user_id,
        state=state,
        documents_json='[{"filename":"historic.pdf"}]',
        merged_json=data.model_dump_json() if isinstance(data, CandidateCVData) else data,
        runtime_attribution_json=runtime,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(row)
    session.commit()
    return row


def _clarification(session, user_id, clarification_id, *, status, interpretation_json):
    row = CandidateAdviserClarificationRecord(
        user_id=user_id,
        clarification_id=clarification_id,
        question_key=f"q-{clarification_id}",
        origin_assessment_fingerprint="f" * 64,
        question_text="What did you deliver?",
        question_source_references_json="[]",
        priority_index=0,
        answer_text="A synthetic answer.",
        interpretation_json=interpretation_json,
        status=status,
    )
    session.add(row)
    session.commit()
    return row


def _clarification_interpretation(title="Confirmed delivery"):
    return ClarificationInterpretation.model_validate({
        "answer_kind": "career_fact",
        "confirmed_context_summary": "The candidate confirmed this delivery.",
        "proposed_evidence": [{
            "fact_domain": "career",
            "evidence_type": "project",
            "title": title,
            "text": "Delivered a synthetic customer service.",
            "skills": ["delivery"],
        }],
    })


def _assessment_content():
    statement = {"text": "Stored fixture.", "source_references": [
        {"source_type": "intake", "reference": "career_direction"}
    ]}
    return CandidateAdviserAssessmentContent.model_validate({
        "professional_positioning": statement,
        "transferable_strengths": [],
        "development_gaps": [],
        "role_hypotheses": [],
        "transition_assessment": statement,
        "open_questions": [],
        "career_strategy_summary": statement,
        "job_search_strategy_summary": statement,
    })


def _capture(engine):
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip())

    event.listen(engine, "before_cursor_execute", record)
    return statements, record


def _dml(statements):
    return [
        statement for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
    ]


def _dml_table(statement):
    parts = statement.replace('"', "").split()
    if parts[0].upper() == "INSERT":
        return parts[2].lower()
    if parts[0].upper() == "UPDATE":
        return parts[1].lower()
    if parts[0].upper() == "DELETE":
        return parts[2].lower()
    return ""


@pytest.mark.parametrize("status", [
    CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE,
    CandidateSchemaStatus.UNSUPPORTED_DRIFT,
])
def test_incompatible_physical_schema_blocks_before_user_mutation_and_never_repairs(db_session, monkeypatch, status):
    _user(db_session, "schema-blocked")
    before = CandidatePhysicalSchemaInspector(db_session).inspect().model_copy(
        update={"status": status}
    )
    monkeypatch.setattr(CandidatePhysicalSchemaInspector, "inspect", lambda _self: before)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Phase 3 invoked automatic schema repair")

    monkeypatch.setattr(CandidateSQLiteSchemaCompatibilityRepairService, "repair", forbidden)
    with pytest.raises(CandidateDataReconciliationBlocked, match="requires a compatible physical schema"):
        _service(db_session).reconcile_user("schema-blocked")
    assert db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == "schema-blocked"
    )) is None


def test_missing_user_is_controlled_unresolved_without_user_creation(db_session):
    result = _service(db_session).reconcile_user("not-a-user")
    assert result.actions == [CandidateDataReconciliationAction.UNRESOLVED]
    assert result.blocking_issues and "does not exist" in result.blocking_issues[0]
    assert db_session.scalar(select(User.id).where(User.id == "not-a-user")) is None


def test_current_structured_and_complete_evidence_is_noop_without_dml(db_session):
    _user(db_session, "already-current")
    data = _data(employment=[{"employer": "Example", "title": "Engineer"}],
                 evidence=[{"evidence_type": "project", "title": "Service", "text": "Built service."}])
    row = CandidateStructuredProfile(user_id="already-current", structured_json=data.model_dump_json())
    db_session.add(row)
    db_session.flush()
    ActiveCandidateEvidenceResolver(db_session).resolve("already-current", data)
    db_session.commit()
    db_session.refresh(row)
    original = (row.id, row.structured_json, row.updated_at)
    statements, capture = _capture(db_session.get_bind())

    result = _service(db_session).reconcile_user("already-current")
    event.remove(db_session.get_bind(), "before_cursor_execute", capture)

    db_session.refresh(row)
    assert result.changed is False
    assert result.actions == [CandidateDataReconciliationAction.NO_ACTION]
    assert result.status_before is result.status_after is CandidateCompatibilityStatus.ALREADY_COMPATIBLE
    assert original == (row.id, row.structured_json, row.updated_at)
    assert _dml(statements) == []


def test_evidence_only_reconciliation_reuses_legacy_id_preserves_authority_and_extra_history(db_session):
    _user(db_session, "evidence-only")
    item = CareerEvidenceDraft(evidence_type="project", title="  Legacy delivery  ",
                               text=" Delivered a service. ", skills=["Python"],
                               provenance=[{"document_sha256": "b" * 64, "segment_ids": ["legacy-seg"]}])
    data = _data(
        employment=[{"employer": "Example", "title": "Engineer"}],
        education=[{"institution": "Example University", "qualification": "MSc"}],
        credentials=[{"name": "Certificate", "credential_type": "certification"}],
        skills=[{"name": "No evidence by itself"}],
        projects=[{"name": "Project without semantic evidence"}],
        achievements=[{"text": "Achievement without semantic evidence"}],
        evidence=[item],
    )
    structured = CandidateStructuredProfile(user_id="evidence-only", structured_json=data.model_dump_json())
    legacy = CandidateEvidenceRecord(
        user_id="evidence-only", fingerprint=legacy_career_evidence_fingerprint(item),
        evidence_type="project", title="Old title", text="Old text", skills_json="[]", provenance_json="[]",
    )
    inactive = CandidateEvidenceRecord(
        user_id="evidence-only", fingerprint="historical-fingerprint", evidence_type="project",
        title="Historical extra", text="Kept but inactive.", skills_json="[]", provenance_json="[]",
    )
    db_session.add_all([structured, legacy, inactive])
    db_session.commit()
    legacy_id = legacy.id
    db_session.refresh(structured)
    structured_before = (structured.id, structured.structured_json, structured.updated_at)
    original_extra = (inactive.id, inactive.fingerprint, inactive.title, inactive.text,
                      inactive.skills_json, inactive.provenance_json)

    result = _service(db_session).reconcile_user("evidence-only")

    db_session.refresh(structured)
    db_session.refresh(legacy)
    db_session.refresh(inactive)
    assert result.changed and result.evidence_reconciled and not result.structured_reconstructed
    assert structured_before == (structured.id, structured.structured_json, structured.updated_at)
    assert legacy.id == legacy_id
    assert legacy.fingerprint == career_evidence_fingerprint(item)
    assert legacy.title == item.title and legacy.text == item.text
    assert json.loads(legacy.skills_json) == ["Python"]
    assert json.loads(legacy.provenance_json)[0]["source_kind"] == "cv"
    assert (inactive.id, inactive.fingerprint, inactive.title, inactive.text,
            inactive.skills_json, inactive.provenance_json) == original_extra
    assert result.evidence_count_after >= result.evidence_count_before
    assert result.status_after is CandidateCompatibilityStatus.ALREADY_COMPATIBLE
    snapshot = CanonicalCandidateReadService(db_session).read("evidence-only")
    assert snapshot.readiness.evidence_materialization_status is CandidateEvidenceMaterializationStatus.COMPLETE
    assert snapshot.readiness.ready_for_candidate_context
    assert "Historical extra" not in {record.title for record in snapshot.active_evidence}
    assert {record.evidence_type for record in snapshot.active_evidence} >= {
        "project", "employment", "education", "credential",
    }
def test_confirmed_clarification_materializes_only_confirmed_factual_evidence(db_session):
    _user(db_session, "clarification-evidence")
    data = _data()
    db_session.add(CandidateStructuredProfile(
        user_id="clarification-evidence", structured_json=data.model_dump_json()
    ))
    confirmed = _clarification_interpretation()
    confirmed_id = "c" * 64
    _clarification(db_session, "clarification-evidence", confirmed_id, status="confirmed",
                   interpretation_json=confirmed.model_dump_json())
    pending = _clarification_interpretation("Pending delivery")
    pending_row = _clarification(db_session, "clarification-evidence", "d" * 64, status="review_ready",
                                 interpretation_json=pending.model_dump_json())
    pending_before = (pending_row.status, pending_row.interpretation_json)
    ActiveCandidateEvidenceResolver(db_session).inspect_active("clarification-evidence", data)

    result = _service(db_session).reconcile_user("clarification-evidence")

    row = db_session.scalar(select(CandidateEvidenceRecord).where(
        CandidateEvidenceRecord.user_id == "clarification-evidence"
    ))
    db_session.refresh(pending_row)
    assert result.evidence_reconciled, result.blocking_issues
    assert row is not None and row.title == "Confirmed delivery"
    assert json.loads(row.provenance_json) == [{
        "document_sha256": None,
        "segment_ids": [],
        "source_kind": "user_confirmed",
        "source_ref": f"clarification:{confirmed_id}",
    }]
    assert (pending_row.status, pending_row.interpretation_json) == pending_before
    assert db_session.scalar(select(CandidateEvidenceRecord.id).where(
        CandidateEvidenceRecord.title == "Pending delivery"
    )) is None


def test_malformed_confirmed_clarification_is_unresolved_and_mutation_free(db_session):
    _user(db_session, "bad-clarification")
    data = _data(employment=[{"employer": "Example", "title": "Engineer"}])
    structured = CandidateStructuredProfile(user_id="bad-clarification", structured_json=data.model_dump_json())
    _clarification(db_session, "bad-clarification", "a" * 64, status="confirmed", interpretation_json="{")
    db_session.add(structured)
    db_session.commit()
    db_session.refresh(structured)
    before = (structured.id, structured.structured_json, structured.updated_at)

    result = _service(db_session).reconcile_user("bad-clarification")

    db_session.refresh(structured)
    assert result.actions == [CandidateDataReconciliationAction.UNRESOLVED]
    assert before == (structured.id, structured.structured_json, structured.updated_at)
    assert db_session.scalar(select(CandidateEvidenceRecord.id).where(
        CandidateEvidenceRecord.user_id == "bad-clarification"
    )) is None


def test_confirmed_cv_reconstruction_preserves_payload_history_and_creates_no_lineage_or_cv_review_rows(db_session):
    _user(db_session, "reconstruct")
    data = _data(
        employment=[{"employer": "Example", "title": "Engineer"},
                    {"employer": "Example", "title": "Engineer"}],
        education=[{"institution": "Example University", "qualification": "MSc"}],
        credentials=[{"name": "Certificate", "credential_type": "certification"}],
        skills=[{"name": "Python"}, {"name": "Python"}],
        projects=[{"name": "Portal", "description": "Built a portal."}],
        achievements=[{"text": "Improved reliability."}],
        evidence=[{"evidence_type": "project", "title": "Delivery", "text": "Built service.",
                   "provenance": [{"document_sha256": "a" * 64, "segment_ids": ["seg-1"]}]}],
    )
    stamp = datetime(2001, 1, 1, tzinfo=timezone.utc)
    draft = _draft(db_session, "reconstruct", "historic-confirmed", data, updated_at=stamp)
    db_session.refresh(draft)
    draft_before = (draft.state, draft.documents_json, draft.merged_json,
                    draft.runtime_attribution_json, draft.created_at, draft.updated_at)
    statements, capture = _capture(db_session.get_bind())

    result = _service(db_session).reconcile_user("reconstruct")
    event.remove(db_session.get_bind(), "before_cursor_execute", capture)

    structured = db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == "reconstruct"
    ))
    db_session.refresh(draft)
    assert structured is not None
    assert CandidateCVData.model_validate_json(structured.structured_json) == CandidateCVData.model_validate_json(
        draft_before[2]
    ) == data
    assert result.structured_reconstructed and result.evidence_reconciled
    assert result.source_cv_draft_id == "historic-confirmed"
    assert structured.updated_at.replace(tzinfo=timezone.utc) > stamp
    assert (draft.state, draft.documents_json, draft.merged_json, draft.runtime_attribution_json,
            draft.created_at, draft.updated_at) == draft_before
    assert db_session.scalar(select(CandidateStructuredItemLineageRecord.id).where(
        CandidateStructuredItemLineageRecord.user_id == "reconstruct"
    )) is None
    assert db_session.scalar(select(CandidateCVOverlapReviewRecord.id).where(
        CandidateCVOverlapReviewRecord.user_id == "reconstruct"
    )) is None
    assert db_session.scalar(select(CandidateCVReviewBaseline.id).where(
        CandidateCVReviewBaseline.draft_id == draft.id
    )) is None
    dml = _dml(statements)
    assert dml and all(
        _dml_table(statement) in {"candidate_structured_profiles", "candidate_evidence"}
        for statement in dml
    )
    assert not any(statement.upper().startswith("DELETE") for statement in dml)
    snapshot = CanonicalCandidateReadService(db_session).read("reconstruct")
    assert snapshot.readiness.structured_profile_available
    assert snapshot.readiness.evidence_materialization_status is CandidateEvidenceMaterializationStatus.COMPLETE
    assert snapshot.readiness.ready_for_candidate_context
    reader = CanonicalCandidateReadService(db_session)
    assert reader.candidate_context(
        reader.read("reconstruct"),
        require_structured_profile=True,
        require_complete_evidence=True,
    ) is not None
    classified = CandidateLegacyCompatibilityInspector(db_session).inspect_user("reconstruct")
    assert classified.status is CandidateCompatibilityStatus.ALREADY_COMPATIBLE
    projection = StructuredProfileProvenanceService(db_session).read_current("reconstruct", data)
    assert all(
        not item.source_history_available and not item.direct_events and not item.history
        for item in projection.items
    )


def test_equivalent_latest_confirmed_sources_choose_classifier_representative_once(db_session):
    _user(db_session, "equivalent-sources")
    data = _data(employment=[{"employer": "Example", "title": "Engineer"}])
    stamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    _draft(db_session, "equivalent-sources", "source-z", data, updated_at=stamp)
    _draft(db_session, "equivalent-sources", "source-a", data, updated_at=stamp)

    result = _service(db_session).reconcile_user("equivalent-sources")

    assert result.source_cv_draft_id == "source-a"
    assert len(db_session.scalars(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == "equivalent-sources"
    )).all()) == 1
    assert result.status_after is CandidateCompatibilityStatus.ALREADY_COMPATIBLE


@pytest.mark.parametrize("kind", ["ambiguous", "malformed_latest", "malformed_over_older_valid"])
def test_ambiguous_or_malformed_latest_confirmed_sources_do_not_reconstruct(db_session, kind):
    user_id = f"source-{kind}"
    _user(db_session, user_id)
    stamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    good = _data(employment=[{"employer": "Example", "title": "Engineer"}])
    if kind == "ambiguous":
        _draft(db_session, user_id, "one", good, updated_at=stamp)
        _draft(db_session, user_id, "two", _data(employment=[{"employer": "Other", "title": "Lead"}]), updated_at=stamp)
    elif kind == "malformed_latest":
        _draft(db_session, user_id, "bad", "{", updated_at=stamp)
    else:
        _draft(db_session, user_id, "older-good", good, updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        _draft(db_session, user_id, "newer-bad", "{", updated_at=stamp)

    result = _service(db_session).reconcile_user(user_id)

    assert result.actions == [CandidateDataReconciliationAction.UNRESOLVED]
    assert db_session.scalar(select(CandidateStructuredProfile.id).where(
        CandidateStructuredProfile.user_id == user_id
    )) is None
    assert db_session.scalar(select(CandidateEvidenceRecord.id).where(
        CandidateEvidenceRecord.user_id == user_id
    )) is None


@pytest.mark.parametrize("state", ["uploaded", "review_ready", "profile_only", "no_cv"])
def test_unconfirmed_or_absent_sources_and_scalar_profile_are_no_action(db_session, state):
    user_id = f"not-confirmed-{state}"
    _user(db_session, user_id)
    if state == "profile_only":
        db_session.add(CandidateProfile(user_id=user_id, headline="Legacy scalar"))
    elif state in {"uploaded", "review_ready"}:
        _draft(db_session, user_id, "draft", _data(), state=state)
    db_session.commit()
    draft_before = None
    if state in {"uploaded", "review_ready"}:
        draft = db_session.scalar(select(CandidateCVIngestionDraft).where(
            CandidateCVIngestionDraft.user_id == user_id
        ))
        draft_before = (draft.state, draft.documents_json, draft.merged_json,
                        draft.runtime_attribution_json, draft.created_at, draft.updated_at)

    result = _service(db_session).reconcile_user(user_id)

    assert result.changed is False
    assert result.actions == [CandidateDataReconciliationAction.NO_ACTION]
    assert db_session.scalar(select(CandidateStructuredProfile.id).where(
        CandidateStructuredProfile.user_id == user_id
    )) is None
    assert db_session.scalar(select(CandidateEvidenceRecord.id).where(
        CandidateEvidenceRecord.user_id == user_id
    )) is None
    if state == "profile_only":
        assert db_session.scalar(select(CandidateProfile.headline).where(
            CandidateProfile.user_id == user_id
        )) == "Legacy scalar"
    if draft_before is not None:
        draft = db_session.scalar(select(CandidateCVIngestionDraft).where(
            CandidateCVIngestionDraft.user_id == user_id
        ))
        assert (draft.state, draft.documents_json, draft.merged_json,
                draft.runtime_attribution_json, draft.created_at, draft.updated_at) == draft_before


def test_current_authority_wins_over_historical_confirmed_cv_and_invalid_current_stays_unresolved(db_session):
    _user(db_session, "current-wins")
    current = _data(employment=[{"employer": "Current Co", "title": "Director"}])
    history = _data(employment=[{"employer": "Old Co", "title": "Engineer"}])
    structured = CandidateStructuredProfile(user_id="current-wins", structured_json=current.model_dump_json())
    _draft(db_session, "current-wins", "old-confirmed", history)
    db_session.add(structured)
    db_session.commit()
    db_session.refresh(structured)
    before = (structured.id, structured.structured_json, structured.updated_at)

    result = _service(db_session).reconcile_user("current-wins")

    db_session.refresh(structured)
    assert result.structured_reconstructed is False and result.evidence_reconciled
    assert (structured.id, structured.structured_json, structured.updated_at) == before
    assert db_session.scalar(select(CandidateEvidenceRecord.title).where(
        CandidateEvidenceRecord.user_id == "current-wins"
    )).startswith("Director")
    _user(db_session, "invalid-current")
    invalid = CandidateStructuredProfile(user_id="invalid-current", structured_json='{"unknown":true}')
    db_session.add(invalid)
    _draft(db_session, "invalid-current", "valid-history", history)
    db_session.commit()

    unresolved = _service(db_session).reconcile_user("invalid-current")
    assert unresolved.actions == [CandidateDataReconciliationAction.UNRESOLVED]
    assert db_session.scalar(select(CandidateEvidenceRecord.id).where(
        CandidateEvidenceRecord.user_id == "invalid-current"
    )) is None
    assert db_session.scalar(select(CandidateStructuredProfile.structured_json).where(
        CandidateStructuredProfile.user_id == "invalid-current"
    )) == '{"unknown":true}'


def test_pending_revision_proposal_adviser_intake_assessment_and_historical_artifacts_are_untouched(db_session):
    _user(db_session, "history-inert")
    data = _data(employment=[{"employer": "Example", "title": "Engineer"}])
    db_session.add(CandidateStructuredProfile(user_id="history-inert", structured_json=data.model_dump_json()))
    revision = CandidateProfileRevisionRecord(
        user_id="history-inert", active_user_id="history-inert", state="review_ready", revision=2,
        base_profile_fingerprint="p" * 64, base_structured_fingerprint="s" * 64,
        base_editable_structured_fingerprint="e" * 64, proposed_profile_json="{}",
    )
    proposal = CandidateAdviserProfileProposalRecord(
        user_id="history-inert", proposal_key="proposal", state="pending", revision=3,
        source_clarification_id="clarification", source_assessment_fingerprint="a" * 64,
        original_update_json="{}", proposed_update_json="{}",
    )
    intake = CandidateAdviserIntakeRecord(
        user_id="history-inert", intake_json=CandidateAdviserIntake(
            career_direction="Stored direction", work_preferences=[], constraints=[], eligibility={}
        ).model_dump_json(),
    )
    assessment = CandidateAdviserAssessmentRecord(
        user_id="history-inert", input_fingerprint="a" * 64, status="confirmed",
        assessment_json=_assessment_content().model_dump_json(),
    )
    discovery = DiscoveryRun(
        user_id="history-inert", search_input_json='{"term":"x"}', search_input_fingerprint="b" * 64,
        candidate_evaluation_fingerprint="c" * 64, evaluation_contract_fingerprint="d" * 64,
        status="complete", funnel_json='{"keep":true}',
    )
    application = ApplicationPreparation(
        id="history-preparation",
        user_id="history-inert", target_snapshot_json='{"keep":true}',
        identity_snapshot_json='{"identity":"old"}', preparation_input_fingerprint="e" * 64,
        preparation_contract_fingerprint="f" * 64, preparation_result_json='{"draft":"old"}',
    )
    tracking = ApplicationTrackingRecord(
        id="history-tracking", preparation_id=application.id, current_status="applied", revision=1
    )
    tracking_event = ApplicationTrackingEvent(
        tracking_id=tracking.id, revision=1, from_status=None, to_status="applied",
    )
    scalar_profile = CandidateProfile(
        user_id="history-inert", display_name="Stored Name", preferred_email="stored@example.test",
        job_search_criteria="Stored criteria",
    )
    lineage = CandidateStructuredItemLineageRecord(
        user_id="history-inert", lineage_key="l" * 64, section="skills",
        item_fingerprint="i" * 64, item_json='{"name":"Existing"}',
        source_kind="manual_profile", source_ref="existing-history", relationship="new",
    )
    db_session.add_all([
        revision, proposal, intake, assessment, discovery, application,
        tracking, tracking_event, scalar_profile, lineage,
    ])
    db_session.commit()
    for row in [revision, proposal, intake, assessment, discovery, application,
                tracking, tracking_event, scalar_profile, lineage]:
        db_session.refresh(row)
    before = [(row, {column.name: getattr(row, column.name) for column in row.__table__.columns})
              for row in [revision, proposal, intake, assessment, discovery, application,
                          tracking, tracking_event, scalar_profile, lineage]]
    read_before = CanonicalCandidateReadService(db_session).read("history-inert")
    assert read_before.adviser_assessment_status.value == "unavailable"

    result = _service(db_session).reconcile_user("history-inert")

    for row, values in before:
        db_session.refresh(row)
        assert {column.name: getattr(row, column.name) for column in row.__table__.columns} == values
    assert result.evidence_reconciled
    read_after = CanonicalCandidateReadService(db_session).read("history-inert")
    assert read_after.adviser_assessment_status.value == "stale"


def test_batch_is_sorted_isolated_and_repeatable_after_malformed_user(db_session):
    _user(db_session, "a-reconstruct")
    _draft(db_session, "a-reconstruct", "cv-a", _data(employment=[{"employer": "A", "title": "Engineer"}]))
    _user(db_session, "b-malformed")
    _draft(db_session, "b-malformed", "cv-b", "{")
    _user(db_session, "c-evidence")
    data_c = _data(employment=[{"employer": "C", "title": "Engineer"}])
    db_session.add(CandidateStructuredProfile(user_id="c-evidence", structured_json=data_c.model_dump_json()))
    db_session.commit()
    service = _service(db_session)

    first = service.reconcile_users(["c-evidence", "b-malformed", "a-reconstruct", "a-reconstruct"])
    second = service.reconcile_users(["a-reconstruct", "b-malformed", "c-evidence"])

    assert [row.user_id for row in first.users] == ["a-reconstruct", "b-malformed", "c-evidence"]
    assert first.changed_count == 2 and first.unresolved_count == 1
    assert first.users[0].changed and first.users[1].actions == [CandidateDataReconciliationAction.UNRESOLVED]
    assert first.users[2].changed
    assert second.changed_count == 0 and second.unresolved_count == 1
    assert second.users[0].actions == [CandidateDataReconciliationAction.NO_ACTION]
    assert second.users[2].actions == [CandidateDataReconciliationAction.NO_ACTION]
    assert len(db_session.scalars(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == "a-reconstruct"
    )).all()) == 1


def test_fresh_classification_preserves_structured_profile_created_after_stale_phase1_read(db_session):
    _user(db_session, "race-current-wins")
    historical = _data(employment=[{"employer": "Historical", "title": "Engineer"}])
    _draft(db_session, "race-current-wins", "old-cv", historical)
    stale_plan = CandidateLegacyCompatibilityInspector(db_session).inspect_user("race-current-wins")
    assert stale_plan.latest_confirmed_cv_id == "old-cv"
    current = _data(employment=[{"employer": "New Authority", "title": "Director"}])
    second_factory = _factory(db_session)
    with second_factory.begin() as session_b:
        session_b.add(CandidateStructuredProfile(
            user_id="race-current-wins", structured_json=current.model_dump_json()
        ))

    result = _service(db_session).reconcile_user("race-current-wins")

    current_row = db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == "race-current-wins"
    ))
    assert current_row is not None
    assert CandidateCVData.model_validate_json(current_row.structured_json) == current
    assert not result.structured_reconstructed
    assert result.evidence_reconciled
    assert db_session.scalar(select(CandidateEvidenceRecord.title).where(
        CandidateEvidenceRecord.user_id == "race-current-wins"
    )).startswith("Director")


@pytest.mark.parametrize("reconstruct", [True, False])
def test_resolver_failure_rolls_back_whole_user_and_preserves_historical_source(db_session, monkeypatch, reconstruct):
    user_id = "rollback-reconstruct" if reconstruct else "rollback-evidence-only"
    _user(db_session, user_id)
    data = _data(employment=[{"employer": "Example", "title": "Engineer"}])
    draft = _draft(db_session, user_id, "cv", data) if reconstruct else None
    structured = None if reconstruct else CandidateStructuredProfile(
        user_id=user_id, structured_json=data.model_dump_json()
    )
    if structured is not None:
        db_session.add(structured)
        db_session.commit()
    evidence_before = [(row.id, row.fingerprint, row.title, row.text)
                       for row in db_session.scalars(select(CandidateEvidenceRecord).where(
                           CandidateEvidenceRecord.user_id == user_id
                       )).all()]
    if draft is not None:
        db_session.refresh(draft)
    source_before = None if draft is None else (
        draft.state, draft.documents_json, draft.merged_json, draft.runtime_attribution_json,
        draft.created_at, draft.updated_at,
    )
    original_resolve = ActiveCandidateEvidenceResolver.resolve

    def failing_resolve(self, owner, payload):
        original_resolve(self, owner, payload)
        raise RuntimeError("injected resolver failure")

    monkeypatch.setattr(ActiveCandidateEvidenceResolver, "resolve", failing_resolve)
    result = _service(db_session).reconcile_user(user_id)
    db_session.rollback()

    assert result.actions == [CandidateDataReconciliationAction.UNRESOLVED]
    assert [(row.id, row.fingerprint, row.title, row.text)
            for row in db_session.scalars(select(CandidateEvidenceRecord).where(
                CandidateEvidenceRecord.user_id == user_id
            )).all()] == evidence_before
    if reconstruct:
        assert db_session.scalar(select(CandidateStructuredProfile.id).where(
            CandidateStructuredProfile.user_id == user_id
        )) is None
        db_session.refresh(draft)
        assert (draft.state, draft.documents_json, draft.merged_json, draft.runtime_attribution_json,
                draft.created_at, draft.updated_at) == source_before
    else:
        db_session.refresh(structured)
        assert CandidateCVData.model_validate_json(structured.structured_json) == data


def test_postflight_failure_rolls_back_reconstructed_profile_and_evidence(db_session, monkeypatch):
    _user(db_session, "postflight-failure")
    _draft(db_session, "postflight-failure", "cv", _data(
        employment=[{"employer": "Example", "title": "Engineer"}]
    ))
    original = CandidateLegacyCompatibilityInspector.inspect_user
    calls = 0

    def fail_postflight(self, user_id):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise RuntimeError("injected postflight failure")
        return original(self, user_id)

    monkeypatch.setattr(CandidateLegacyCompatibilityInspector, "inspect_user", fail_postflight)
    result = _service(db_session).reconcile_user("postflight-failure")
    db_session.rollback()

    assert result.actions == [CandidateDataReconciliationAction.UNRESOLVED]
    assert db_session.scalar(select(CandidateStructuredProfile.id).where(
        CandidateStructuredProfile.user_id == "postflight-failure"
    )) is None
    assert db_session.scalar(select(CandidateEvidenceRecord.id).where(
        CandidateEvidenceRecord.user_id == "postflight-failure"
    )) is None


def test_provider_free_schema_safe_and_historical_sources_have_no_write_paths(db_session, monkeypatch):
    _user(db_session, "provider-free")
    _draft(db_session, "provider-free", "cv", _data(evidence=[{
        "evidence_type": "project", "title": "Project", "text": "Built a service."
    }]))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a forbidden workflow was invoked")

    monkeypatch.setattr(CandidateSQLiteSchemaCompatibilityRepairService, "repair", forbidden)
    from app.services.cv_ingestion_service import CVIngestionService
    from app.services.profile_revision_service import CandidateProfileRevisionService
    from app.services.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalService
    from app.agents.candidate_adviser import SemanticCandidateAdviser
    from app.agents.candidate_adviser_clarification import SemanticCandidateAdviserClarificationInterpreter
    from app.services.cv_interpretation_service import SemanticCVInterpreter
    monkeypatch.setattr(CVIngestionService, "confirm", forbidden)
    monkeypatch.setattr(CandidateProfileRevisionService, "confirm", forbidden)
    monkeypatch.setattr(CandidateAdviserProfileProposalService, "transfer_to_profile_revision", forbidden)
    monkeypatch.setattr(SemanticCandidateAdviser, "__init__", forbidden)
    monkeypatch.setattr(SemanticCVInterpreter, "__init__", forbidden)
    monkeypatch.setattr(SemanticCandidateAdviserClarificationInterpreter, "__init__", forbidden)
    statements, capture = _capture(db_session.get_bind())

    result = _service(db_session).reconcile_user("provider-free")
    event.remove(db_session.get_bind(), "before_cursor_execute", capture)

    assert result.changed
    assert not any(statement.upper().lstrip().startswith(("CREATE TABLE", "CREATE INDEX", "ALTER", "DROP"))
                   for statement in statements)
    writes = _dml(statements)
    assert all(_dml_table(statement) in {"candidate_structured_profiles", "candidate_evidence"}
               for statement in writes)
    assert not any(statement.upper().startswith("DELETE") for statement in writes)


def test_two_sqlite_sessions_serialize_same_reconstruction_and_evidence_fingerprints(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reconciliation-race.db'}",
        connect_args={"timeout": 15},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    data = _data(
        employment=[{"employer": "Example", "title": "Engineer"}],
        evidence=[{"evidence_type": "project", "title": "Race", "text": "Built a service."}],
    )
    with factory.begin() as setup:
        setup.add(User(id="parallel", email="parallel@example.test", password_hash="fixture"))
        setup.add(CandidateCVIngestionDraft(
            id="parallel-cv", user_id="parallel", state="confirmed", documents_json="[]",
            merged_json=data.model_dump_json(),
        ))
    service = CandidateLegacyDataReconciliationService(factory)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.reconcile_user, ["parallel", "parallel"]))

    assert sum(result.changed for result in results) == 1
    assert sum(
        CandidateDataReconciliationAction.NO_ACTION in result.actions for result in results
    ) == 1
    with factory() as verify:
        assert len(verify.scalars(select(CandidateStructuredProfile).where(
            CandidateStructuredProfile.user_id == "parallel"
        )).all()) == 1
        rows = verify.scalars(select(CandidateEvidenceRecord).where(
            CandidateEvidenceRecord.user_id == "parallel"
        )).all()
        assert len(rows) == len({row.fingerprint for row in rows}) == 2


def test_reconciler_does_not_commit_unrelated_caller_pending_state(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'owned-session.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    data = _data(employment=[{"employer": "Example", "title": "Engineer"}])
    with factory.begin() as setup:
        setup.add(User(id="owned", email="owned@example.test", password_hash="fixture"))
        setup.add(CandidateCVIngestionDraft(
            id="owned-cv", user_id="owned", state="confirmed", documents_json="[]",
            merged_json=data.model_dump_json(),
        ))
    caller = factory()
    pending = User(id="unrelated-pending", email="pending@example.test", password_hash="fixture")
    caller.add(pending)

    result = CandidateLegacyDataReconciliationService(factory).reconcile_user("owned")

    assert result.changed
    assert pending in caller.new
    with factory() as verify:
        assert verify.get(User, "unrelated-pending") is None
        assert verify.scalar(select(CandidateStructuredProfile.id).where(
            CandidateStructuredProfile.user_id == "owned"
        )) is not None
    caller.rollback()
    caller.close()

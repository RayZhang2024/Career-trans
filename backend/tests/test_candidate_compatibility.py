from datetime import datetime, timezone
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import User  # noqa: F401
from app.models.candidate_cv_ingestion import (
    CandidateCVIngestionDraft,
    CandidateEvidenceRecord,
    CandidateStructuredProfile,
)
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.schemas.candidate_compatibility import (
    CandidateCompatibilityAction,
    CandidateCompatibilityStatus,
    CandidateEvidenceCompatibilityStatus,
    CandidateSchemaStatus,
    CandidateStructuredAuthorityStatus,
)
from app.schemas.cv_ingestion import CandidateCVData, CareerEvidenceDraft
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessmentContent,
    CandidateAdviserIntake,
    ClarificationAnswerKind,
    ClarificationInterpretation,
)
from app.models.candidate_adviser import (
    CandidateAdviserAssessmentRecord,
    CandidateAdviserClarificationRecord,
    CandidateAdviserIntakeRecord,
)
from app.models.user_job_discovery import DiscoveryRun
from app.models.application_preparation import ApplicationPreparation
from app.services.candidate_compatibility_inspector import CandidatePhysicalSchemaInspector
from app.services.candidate_sqlite_schema_repair import CandidateSQLiteSchemaCompatibilityRepairService
from app.services.candidate_legacy_compatibility_inspector import (
    CandidateCompatibilityDryRunService,
    CandidateLegacyCompatibilityInspector,
)
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.career_evidence_fingerprint import legacy_career_evidence_fingerprint


def _user(session, user_id: str):
    from app.models.user import User

    row = User(id=user_id, email=f"{user_id}@example.test", password_hash="fixture")
    session.add(row)
    session.flush()
    return row


def _cv_data(*, employment=(), evidence=()) -> CandidateCVData:
    return CandidateCVData(
        employment=list(employment),
        evidence=list(evidence),
    )


def _adviser_assessment_content() -> CandidateAdviserAssessmentContent:
    insight = {
        "text": "Synthetic persisted assessment.",
        "source_references": [{"source_type": "intake", "reference": "career_direction"}],
    }
    return CandidateAdviserAssessmentContent.model_validate({
        "professional_positioning": insight,
        "transferable_strengths": [],
        "development_gaps": [],
        "role_hypotheses": [],
        "transition_assessment": insight,
        "open_questions": [],
        "career_strategy_summary": insight,
        "job_search_strategy_summary": insight,
    })


def _persist_adviser_intake_and_assessment(session, user_id: str, fingerprint: str):
    intake = CandidateAdviserIntake(
        career_direction="Synthetic career direction",
        work_preferences=[],
        constraints=[],
        eligibility={},
    )
    session.add(CandidateAdviserIntakeRecord(
        user_id=user_id,
        intake_json=intake.model_dump_json(),
    ))
    record = CandidateAdviserAssessmentRecord(
        user_id=user_id,
        input_fingerprint=fingerprint,
        status="confirmed",
        assessment_json=_adviser_assessment_content().model_dump_json(),
    )
    session.add(record)
    session.flush()
    return record


def _draft(session, user_id: str, state: str, data: CandidateCVData | None, *, draft_id: str, updated_at=None):
    session.add(CandidateCVIngestionDraft(
        id=draft_id,
        user_id=user_id,
        state=state,
        documents_json="[]",
        merged_json=data.model_dump_json() if data is not None else None,
        created_at=updated_at or datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=updated_at or datetime(2024, 1, 1, tzinfo=timezone.utc),
    ))
    session.flush()


def test_current_physical_schema_is_compatible_and_repeatable(db_session):
    first = CandidatePhysicalSchemaInspector(db_session).inspect()
    second = CandidatePhysicalSchemaInspector(db_session).inspect()

    assert first.status is CandidateSchemaStatus.COMPATIBLE
    assert first.planned_actions == [CandidateCompatibilityAction.NO_ACTION]
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_historical_profile_schema_reports_nullable_additions_without_writes():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("""
            CREATE TABLE candidate_profiles (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                headline VARCHAR(200), summary TEXT, current_role VARCHAR(200),
                location VARCHAR(200), career_goal TEXT,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT uq_candidate_profiles_user_id UNIQUE (user_id),
                CONSTRAINT fk_candidate_profiles_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", capture)
    report = CandidatePhysicalSchemaInspector(engine).inspect()
    event.remove(engine, "before_cursor_execute", capture)

    profile = next(table for table in report.tables if table.table == "candidate_profiles")
    assert report.status is CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE
    assert {item.name for item in profile.missing_columns} == {
        "job_search_criteria", "display_name", "preferred_email", "phone",
        "linkedin_url", "github_url", "portfolio_url",
    }
    assert profile.status is CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE
    assert CandidateCompatibilityAction.ADD_MISSING_SCHEMA_COLUMN in profile.planned_actions
    assert all(statement not in {"INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "CREATE", "DROP"} for statement in statements)
    assert "candidate_profiles" in inspect(engine).get_table_names()


def test_august_profile_schema_has_job_criteria_but_lacks_identity_fields():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("""
            CREATE TABLE candidate_profiles (
                id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,
                headline VARCHAR(200), summary TEXT, current_role VARCHAR(200),
                location VARCHAR(200), career_goal TEXT, job_search_criteria TEXT,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT uq_candidate_profiles_user_id UNIQUE (user_id),
                CONSTRAINT fk_candidate_profiles_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
    profile = next(
        table for table in CandidatePhysicalSchemaInspector(engine).inspect().tables
        if table.table == "candidate_profiles"
    )
    assert {column.name for column in profile.missing_columns} == {
        "display_name", "preferred_email", "phone", "linkedin_url", "github_url", "portfolio_url",
    }


def test_historical_cv_and_adviser_tables_report_their_actual_missing_columns():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("""
            CREATE TABLE candidate_cv_ingestion_drafts (
                id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,
                state VARCHAR(32) NOT NULL, documents_json TEXT NOT NULL,
                merged_json TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT fk_candidate_cv_ingestion_drafts_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
        connection.execute(text("CREATE INDEX ix_candidate_cv_ingestion_drafts_user_id ON candidate_cv_ingestion_drafts(user_id)"))
        connection.execute(text("""
            CREATE TABLE candidate_profile_revisions (
                id VARCHAR(36) PRIMARY KEY
            )
        """))
        connection.execute(text("""
            CREATE TABLE candidate_adviser_profile_proposals (
                id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,
                proposal_key VARCHAR(64) NOT NULL, state VARCHAR(16) NOT NULL,
                revision INTEGER NOT NULL, source_clarification_id VARCHAR(64) NOT NULL,
                source_assessment_fingerprint VARCHAR(64) NOT NULL,
                original_update_json TEXT NOT NULL, proposed_update_json TEXT NOT NULL,
                transferred_profile_revision_id VARCHAR(36) REFERENCES candidate_profile_revisions(id), created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL, rejected_at TIMESTAMP, transferred_at TIMESTAMP,
                    CONSTRAINT uq_candidate_adviser_profile_proposals_user_key UNIQUE (user_id, proposal_key),
                    CONSTRAINT fk_candidate_adviser_profile_proposals_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT ck_candidate_adviser_profile_proposals_state
                    CHECK (state IN ('pending', 'rejected', 'transferred'))
            )
        """))
        connection.execute(text("CREATE INDEX ix_candidate_adviser_profile_proposals_user_id ON candidate_adviser_profile_proposals(user_id)"))
        connection.execute(text("CREATE INDEX ix_candidate_adviser_profile_proposals_source_clarification_id ON candidate_adviser_profile_proposals(source_clarification_id)"))

    result = CandidatePhysicalSchemaInspector(engine).inspect()
    by_name = {table.table: table for table in result.tables}
    assert {column.name for column in by_name["candidate_cv_ingestion_drafts"].missing_columns} == {
        "runtime_attribution_json",
    }
    assert {column.name for column in by_name["candidate_adviser_profile_proposals"].missing_columns} == {
        "overlap_resolution_json",
        "applied_at",
    }
    assert CandidateCompatibilityAction.ADD_MISSING_SCHEMA_COLUMN in by_name["candidate_cv_ingestion_drafts"].planned_actions
    assert CandidateCompatibilityAction.RECONSTRUCT_HISTORICAL_ADVISER_PROPOSAL_TABLE in by_name["candidate_adviser_profile_proposals"].planned_actions


def test_later_whole_candidate_tables_are_reported_individually_when_absent():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
    report = CandidatePhysicalSchemaInspector(engine).inspect()
    by_name = {table.table: table for table in report.tables}
    for name in (
        "candidate_cv_review_baselines",
        "candidate_adviser_clarifications",
        "candidate_profile_revisions",
        "candidate_structured_item_lineage",
        "candidate_cv_overlap_reviews",
    ):
        assert not by_name[name].table_exists
        assert by_name[name].status is CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE
        assert by_name[name].planned_actions == [CandidateCompatibilityAction.CREATE_MISSING_SCHEMA_TABLE]


def test_create_all_adds_absent_tables_but_does_not_repair_old_columns():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("""
            CREATE TABLE candidate_profiles (
                id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,
                headline VARCHAR(200), summary TEXT, current_role VARCHAR(200),
                location VARCHAR(200), career_goal TEXT,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            )
        """))

    Base.metadata.create_all(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("candidate_profiles")}
    assert "display_name" not in columns
    assert inspect(engine).has_table("candidate_cv_review_baselines")
    assert inspect(engine).has_table("candidate_adviser_clarifications")
    assert inspect(engine).has_table("candidate_profile_revisions")


def test_retained_pre_276_sqlite_repair_adds_option_columns_without_changing_legacy_rows():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(email="retained-276@example.com", password_hash="unused")
        session.add(user)
        session.flush()
        row = CandidateAdviserClarificationRecord(
            user_id=user.id,
            clarification_id="a" * 64,
            question_key="b" * 64,
            origin_assessment_fingerprint="c" * 64,
            question_text="A historical question?",
            question_source_references_json="[]",
            priority_index=0,
            answer_text="Historical free-text answer",
            status="review_ready",
        )
        session.add(row)
        session.commit()

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE candidate_adviser_clarifications DROP COLUMN suggested_answers_json"
        )
        connection.exec_driver_sql(
            "ALTER TABLE candidate_adviser_clarifications DROP COLUMN structured_response_json"
        )
    result = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()
    assert result.changed
    assert result.after.status is CandidateSchemaStatus.COMPATIBLE
    columns = {column["name"] for column in inspect(engine).get_columns("candidate_adviser_clarifications")}
    assert {"suggested_answers_json", "structured_response_json"}.issubset(columns)
    with engine.connect() as connection:
        saved = connection.execute(text(
            "SELECT question_text, answer_text, status, suggested_answers_json, structured_response_json "
            "FROM candidate_adviser_clarifications WHERE clarification_id = :id"
        ), {"id": "a" * 64}).one()
    assert tuple(saved) == ("A historical question?", "Historical free-text answer", "review_ready", None, None)
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "20261005_candidate_adviser_clarification_options_sqlite.py"
    spec = importlib.util.spec_from_file_location("clarification_options_sqlite", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.raw_connection() as connection:
        module.upgrade(connection)
    repeated = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()
    assert not repeated.changed
    assert repeated.after.status is CandidateSchemaStatus.COMPATIBLE


def test_missing_ownership_column_is_unsupported_physical_drift():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE candidate_profiles (id VARCHAR(36) PRIMARY KEY)"))

    result = CandidatePhysicalSchemaInspector(engine).inspect()
    profile = next(table for table in result.tables if table.table == "candidate_profiles")
    assert result.status is CandidateSchemaStatus.UNSUPPORTED_DRIFT
    assert profile.status is CandidateSchemaStatus.UNSUPPORTED_DRIFT
    assert CandidateCompatibilityAction.MANUAL_RESOLUTION_REQUIRED in profile.planned_actions
    assert any("user_id" in diagnostic for diagnostic in profile.diagnostics)


def test_profile_only_and_unconfirmed_cv_are_not_promoted(db_session):
    _user(db_session, "profile-only")
    db_session.add(CandidateProfile(user_id="profile-only", headline="Readable legacy profile"))
    _user(db_session, "unconfirmed")
    _draft(db_session, "unconfirmed", "review_ready", _cv_data(), draft_id="review-cv")
    db_session.flush()

    results = CandidateLegacyCompatibilityInspector(db_session).inspect_users(["unconfirmed", "profile-only"])
    by_id = {result.user_id: result for result in results}
    assert [result.user_id for result in results] == ["profile-only", "unconfirmed"]
    assert by_id["profile-only"].status is CandidateCompatibilityStatus.NOT_YET_CONFIRMED
    assert by_id["profile-only"].profile_exists
    assert by_id["profile-only"].structured_authority is CandidateStructuredAuthorityStatus.MISSING_UNCONFIRMED
    assert by_id["unconfirmed"].status is CandidateCompatibilityStatus.NOT_YET_CONFIRMED
    assert by_id["unconfirmed"].unconfirmed_cv_count == 1
    assert CandidateCompatibilityAction.RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV not in by_id["unconfirmed"].planned_actions


def test_confirmed_cv_is_reported_as_reconstructable_without_mutation(db_session):
    _user(db_session, "confirmed")
    data = _cv_data(employment=[{"employer": "Example", "title": "Engineer"}])
    _draft(db_session, "confirmed", "confirmed", data, draft_id="confirmed-cv")

    result = CandidateLegacyCompatibilityInspector(db_session).inspect_user("confirmed")

    assert result.status is CandidateCompatibilityStatus.REPAIRABLE
    assert result.structured_authority is CandidateStructuredAuthorityStatus.RECONSTRUCTABLE_FROM_CONFIRMED_CV
    assert result.latest_confirmed_cv_id == "confirmed-cv"
    assert CandidateCompatibilityAction.RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV in result.planned_actions
    assert db_session.get(CandidateStructuredProfile, result.user_id) is None


def test_invalid_confirmed_cv_and_ambiguous_latest_tie_fail_closed(db_session):
    _user(db_session, "bad-cv")
    _draft(db_session, "bad-cv", "confirmed", None, draft_id="bad")
    _user(db_session, "tie")
    stamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    _draft(db_session, "tie", "confirmed", _cv_data(employment=[{"employer": "A", "title": "One"}]), draft_id="tie-a", updated_at=stamp)
    _draft(db_session, "tie", "confirmed", _cv_data(employment=[{"employer": "B", "title": "Two"}]), draft_id="tie-b", updated_at=stamp)
    db_session.flush()

    by_id = {row.user_id: row for row in CandidateLegacyCompatibilityInspector(db_session).inspect_users(["bad-cv", "tie"])}
    assert by_id["bad-cv"].status is CandidateCompatibilityStatus.UNRESOLVED
    assert by_id["tie"].status is CandidateCompatibilityStatus.UNRESOLVED
    assert any(issue.blocking for issue in by_id["tie"].issues)
    assert by_id["tie"].structured_authority is CandidateStructuredAuthorityStatus.UNRESOLVED


def test_current_structured_authority_wins_over_ambiguous_historical_cv_sources(db_session):
    _user(db_session, "current-with-ambiguous-history")
    current = _cv_data(employment=[{"employer": "Current Co", "title": "Lead"}])
    db_session.add(CandidateStructuredProfile(
        user_id="current-with-ambiguous-history", structured_json=current.model_dump_json()
    ))
    db_session.flush()
    ActiveCandidateEvidenceResolver(db_session).resolve("current-with-ambiguous-history", current)
    stamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    _draft(
        db_session, "current-with-ambiguous-history", "confirmed",
        _cv_data(employment=[{"employer": "Historical A", "title": "Engineer"}]),
        draft_id="historical-a", updated_at=stamp,
    )
    _draft(
        db_session, "current-with-ambiguous-history", "confirmed",
        _cv_data(employment=[{"employer": "Historical B", "title": "Director"}]),
        draft_id="historical-b", updated_at=stamp,
    )
    _user(db_session, "current-with-ambiguous-history-and-missing-evidence")
    db_session.add(CandidateStructuredProfile(
        user_id="current-with-ambiguous-history-and-missing-evidence",
        structured_json=current.model_dump_json(),
    ))
    _draft(
        db_session, "current-with-ambiguous-history-and-missing-evidence", "confirmed",
        _cv_data(employment=[{"employer": "Historical A", "title": "Engineer"}]),
        draft_id="historical-c", updated_at=stamp,
    )
    _draft(
        db_session, "current-with-ambiguous-history-and-missing-evidence", "confirmed",
        _cv_data(employment=[{"employer": "Historical B", "title": "Director"}]),
        draft_id="historical-d", updated_at=stamp,
    )
    db_session.flush()

    results = {
        result.user_id: result
        for result in CandidateLegacyCompatibilityInspector(db_session).inspect_users([
            "current-with-ambiguous-history",
            "current-with-ambiguous-history-and-missing-evidence",
        ])
    }
    result = results["current-with-ambiguous-history"]
    ambiguity = next(issue for issue in result.issues if issue.code.value == "unresolved_ambiguous_confirmed_sources")

    assert result.structured_authority is CandidateStructuredAuthorityStatus.PRESERVE
    assert result.status is CandidateCompatibilityStatus.ALREADY_COMPATIBLE
    assert not ambiguity.blocking
    assert CandidateCompatibilityAction.RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV not in result.planned_actions
    incomplete = results["current-with-ambiguous-history-and-missing-evidence"]
    incomplete_ambiguity = next(
        issue for issue in incomplete.issues
        if issue.code.value == "unresolved_ambiguous_confirmed_sources"
    )
    assert incomplete.status is CandidateCompatibilityStatus.REPAIRABLE
    assert incomplete.structured_authority is CandidateStructuredAuthorityStatus.PRESERVE
    assert not incomplete_ambiguity.blocking
    assert incomplete.planned_actions == [
        CandidateCompatibilityAction.PRESERVE_CURRENT_STRUCTURED,
        CandidateCompatibilityAction.RECONCILE_ACTIVE_EVIDENCE,
    ]


def test_malformed_user_does_not_abort_valid_user_or_cross_user_scope(db_session):
    _user(db_session, "valid-owner")
    _draft(db_session, "valid-owner", "confirmed", _cv_data(), draft_id="valid-source")
    _user(db_session, "malformed-owner")
    _draft(db_session, "malformed-owner", "confirmed", None, draft_id="malformed-source")
    db_session.flush()

    result = CandidateLegacyCompatibilityInspector(db_session).inspect_users(["malformed-owner", "valid-owner"])
    by_id = {row.user_id: row for row in result}
    assert by_id["valid-owner"].status is CandidateCompatibilityStatus.REPAIRABLE
    assert by_id["valid-owner"].latest_confirmed_cv_id == "valid-source"
    assert by_id["malformed-owner"].status is CandidateCompatibilityStatus.UNRESOLVED
    assert by_id["malformed-owner"].latest_confirmed_cv_id is None


def test_absent_lineage_and_duplicate_current_facts_are_informational_only(db_session):
    _user(db_session, "legacy-duplicates")
    data = CandidateCVData(skills=[{"name": "Python"}, {"name": "Python"}])
    row = CandidateStructuredProfile(user_id="legacy-duplicates", structured_json=data.model_dump_json())
    db_session.add(row)
    db_session.flush()
    original = row.structured_json

    result = CandidateLegacyCompatibilityInspector(db_session).inspect_user("legacy-duplicates")

    assert result.status is CandidateCompatibilityStatus.ALREADY_COMPATIBLE
    assert result.lineage_event_count == 0
    assert result.duplicate_current_fact_count == 1
    assert any(issue.code.value == "legacy_lineage_unavailable" for issue in result.issues)
    assert any(issue.code.value == "legacy_duplicate_current_fact" for issue in result.issues)
    assert row.structured_json == original
    assert CandidateCompatibilityAction.MANUAL_RESOLUTION_REQUIRED not in result.planned_actions


def test_historical_artifact_snapshots_are_not_changed_by_dry_run(db_session):
    _user(db_session, "history-artifacts")
    run = DiscoveryRun(
        user_id="history-artifacts", search_input_json='{"term":"example"}',
        search_input_fingerprint="a" * 64, candidate_evaluation_fingerprint="b" * 64,
        evaluation_contract_fingerprint="c" * 64, status="complete", funnel_json='{"kept": 1}',
    )
    preparation = ApplicationPreparation(
        user_id="history-artifacts", target_snapshot_json='{"job":"snapshot"}',
        identity_snapshot_json='{"name":"Synthetic"}', preparation_input_fingerprint="d" * 64,
        preparation_contract_fingerprint="e" * 64, preparation_result_json='{"draft":"stored"}',
    )
    db_session.add_all([run, preparation])
    db_session.flush()
    before = (run.funnel_json, preparation.target_snapshot_json, preparation.preparation_result_json)

    result = CandidateLegacyCompatibilityInspector(db_session).inspect_user("history-artifacts")

    after = (run.funnel_json, preparation.target_snapshot_json, preparation.preparation_result_json)
    assert result.discovery_artifact_count == 1
    assert result.application_artifact_count == 1
    assert before == after


def test_existing_structured_authority_wins_over_different_confirmed_cv(db_session):
    _user(db_session, "current")
    current = _cv_data(employment=[{"employer": "Current", "title": "Lead"}])
    historical = _cv_data(employment=[{"employer": "Historical", "title": "Engineer"}])
    db_session.add(CandidateStructuredProfile(user_id="current", structured_json=current.model_dump_json()))
    _draft(db_session, "current", "confirmed", historical, draft_id="historical")
    db_session.flush()

    result = CandidateLegacyCompatibilityInspector(db_session).inspect_user("current")

    assert result.structured_authority is CandidateStructuredAuthorityStatus.PRESERVE
    assert result.status is CandidateCompatibilityStatus.REPAIRABLE  # evidence coverage is independently reported
    assert CandidateCompatibilityAction.PRESERVE_CURRENT_STRUCTURED in result.planned_actions
    assert CandidateCompatibilityAction.RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV not in result.planned_actions
    assert any(issue.code.value == "historical_source_differs_from_current_authority" for issue in result.issues)


def test_invalid_current_structured_authority_does_not_fall_back_to_confirmed_cv(db_session):
    _user(db_session, "invalid-structure")
    db_session.add(CandidateStructuredProfile(user_id="invalid-structure", structured_json='{"unexpected": true}'))
    _draft(db_session, "invalid-structure", "confirmed", _cv_data(), draft_id="valid-cv")
    db_session.flush()

    result = CandidateLegacyCompatibilityInspector(db_session).inspect_user("invalid-structure")

    assert result.status is CandidateCompatibilityStatus.UNRESOLVED
    assert result.structured_authority is CandidateStructuredAuthorityStatus.UNRESOLVED
    assert CandidateCompatibilityAction.RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV not in result.planned_actions


def test_legacy_evidence_bridge_is_classified_stale_not_rebuilt(db_session):
    _user(db_session, "legacy-evidence")
    item = CareerEvidenceDraft(evidence_type="project", title="Migration  ", text="Built a migration")
    data = _cv_data(evidence=[item])
    db_session.add(CandidateStructuredProfile(user_id="legacy-evidence", structured_json=data.model_dump_json()))
    db_session.add(CandidateEvidenceRecord(
        user_id="legacy-evidence",
        fingerprint=legacy_career_evidence_fingerprint(item),
        evidence_type=item.evidence_type,
        title=item.title,
        text=item.text,
        skills_json="[]",
        provenance_json="[]",
    ))
    db_session.flush()

    result = CandidateLegacyCompatibilityInspector(db_session).inspect_user("legacy-evidence")

    assert result.evidence_status is CandidateEvidenceCompatibilityStatus.STALE
    assert result.stale_evidence_count == 1
    assert any(issue.code.value == "legacy_evidence_reusable_but_stale" for issue in result.issues)
    assert CandidateCompatibilityAction.RECONCILE_ACTIVE_EVIDENCE in result.planned_actions


def test_evidence_status_distinguishes_missing_and_missing_plus_stale(db_session):
    _user(db_session, "missing-evidence")
    missing_data = _cv_data(employment=[{"employer": "Example", "title": "Engineer"}])
    db_session.add(CandidateStructuredProfile(
        user_id="missing-evidence", structured_json=missing_data.model_dump_json()
    ))
    _user(db_session, "mixed-evidence")
    stale_item = CareerEvidenceDraft(evidence_type="project", title="Launch  ", text="A launch")
    mixed_data = _cv_data(
        employment=[{"employer": "Example", "title": "Engineer"}],
        evidence=[stale_item],
    )
    db_session.add(CandidateStructuredProfile(
        user_id="mixed-evidence", structured_json=mixed_data.model_dump_json()
    ))
    db_session.add(CandidateEvidenceRecord(
        user_id="mixed-evidence",
        fingerprint=legacy_career_evidence_fingerprint(stale_item),
        evidence_type=stale_item.evidence_type,
        title=stale_item.title,
        text=stale_item.text,
        skills_json="[]",
        provenance_json="[]",
    ))
    db_session.flush()

    results = {
        row.user_id: row
        for row in CandidateLegacyCompatibilityInspector(db_session).inspect_users(
            ["missing-evidence", "mixed-evidence"]
        )
    }
    assert results["missing-evidence"].evidence_status is CandidateEvidenceCompatibilityStatus.MISSING
    assert results["mixed-evidence"].evidence_status is CandidateEvidenceCompatibilityStatus.MISSING_AND_STALE


def test_clarifications_and_revision_proposal_history_are_inspected_without_promotion(db_session):
    _user(db_session, "history")
    confirmed = ClarificationInterpretation(
        answer_kind=ClarificationAnswerKind.CAREER_FACT,
        confirmed_context_summary="The candidate confirmed project work.",
        proposed_evidence=[{
            "fact_domain": "career", "evidence_type": "project", "title": "Launch",
            "text": "Launched a customer portal", "skills": ["delivery"],
        }],
    )
    db_session.add_all([
        CandidateAdviserClarificationRecord(
            user_id="history", clarification_id="a" * 64, question_key="confirmed",
            origin_assessment_fingerprint="f" * 64, question_text="What did you deliver?",
            question_source_references_json="[]", priority_index=0, answer_text="A portal",
            interpretation_json=confirmed.model_dump_json(), status="confirmed",
        ),
        CandidateAdviserClarificationRecord(
            user_id="history", clarification_id="b" * 64, question_key="pending",
            origin_assessment_fingerprint="f" * 64, question_text="What else?",
            question_source_references_json="[]", priority_index=1, answer_text="Pending",
            status="review_ready",
        ),
        CandidateProfileRevisionRecord(
            user_id="history", active_user_id="history", state="review_ready", revision=1,
            base_profile_fingerprint="p" * 64, base_structured_fingerprint="s" * 64,
            base_editable_structured_fingerprint="e" * 64,
        ),
        CandidateAdviserProfileProposalRecord(
            user_id="history", proposal_key="proposal", state="pending", revision=1,
            source_clarification_id="a" * 64, source_assessment_fingerprint="f" * 64,
            original_update_json="{}", proposed_update_json="{}",
        ),
    ])
    db_session.flush()
    before = (
        db_session.query(CandidateAdviserClarificationRecord).count(),
        db_session.query(CandidateProfileRevisionRecord).count(),
        db_session.query(CandidateAdviserProfileProposalRecord).count(),
    )

    result = CandidateLegacyCompatibilityInspector(db_session).inspect_user("history")

    after = (
        db_session.query(CandidateAdviserClarificationRecord).count(),
        db_session.query(CandidateProfileRevisionRecord).count(),
        db_session.query(CandidateAdviserProfileProposalRecord).count(),
    )
    assert before == after == (2, 1, 1)
    assert result.confirmed_factual_clarification_count == 1
    assert result.unconfirmed_clarification_count == 1
    assert result.expected_evidence_count == 1
    assert result.pending_profile_revision_count == 1
    assert result.pending_adviser_proposal_count == 1
    assert CandidateCompatibilityAction.RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV not in result.planned_actions


def test_incomplete_evidence_makes_persisted_adviser_currentness_unavailable(db_session):
    _user(db_session, "incomplete-adviser")
    data = _cv_data(employment=[{"employer": "Example", "title": "Engineer"}])
    db_session.add(CandidateStructuredProfile(
        user_id="incomplete-adviser", structured_json=data.model_dump_json()
    ))
    db_session.flush()
    persisted = _persist_adviser_intake_and_assessment(db_session, "incomplete-adviser", "a" * 64)
    original = (persisted.status, persisted.input_fingerprint, persisted.assessment_json)

    result = CandidateLegacyCompatibilityInspector(db_session).inspect_user("incomplete-adviser")

    db_session.refresh(persisted)
    assert result.evidence_status is CandidateEvidenceCompatibilityStatus.MISSING
    assert result.adviser_assessment_status == "unavailable"
    assert CandidateCompatibilityAction.RECONCILE_ACTIVE_EVIDENCE in result.planned_actions
    assert any(
        "currentness is unavailable until active evidence is complete" in issue.detail
        for issue in result.issues
    )
    assert original == (persisted.status, persisted.input_fingerprint, persisted.assessment_json)


def test_complete_evidence_assessment_currentness_is_provider_free_and_read_only(db_session, monkeypatch):
    _user(db_session, "complete-adviser")
    data = _cv_data(employment=[{"employer": "Example", "title": "Engineer"}])
    db_session.add(CandidateStructuredProfile(
        user_id="complete-adviser", structured_json=data.model_dump_json()
    ))
    db_session.flush()
    ActiveCandidateEvidenceResolver(db_session).resolve("complete-adviser", data)
    intake = CandidateAdviserIntake(
        career_direction="Synthetic career direction",
        work_preferences=[], constraints=[], eligibility={},
    )
    db_session.add(CandidateAdviserIntakeRecord(
        user_id="complete-adviser", intake_json=intake.model_dump_json()
    ))
    db_session.flush()
    fingerprint = CandidateAdviserService(db_session).input_fingerprint(
        "complete-adviser", read_only=True
    )
    assessment = CandidateAdviserAssessmentRecord(
        user_id="complete-adviser",
        input_fingerprint=fingerprint,
        status="confirmed",
        assessment_json=_adviser_assessment_content().model_dump_json(),
    )
    db_session.add(assessment)
    db_session.flush()
    original = (assessment.status, assessment.input_fingerprint, assessment.assessment_json)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split()[0].upper())

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)

    def provider_forbidden(*_args, **_kwargs):
        raise AssertionError("compatibility dry run invoked semantic provider")

    monkeypatch.setattr(CandidateAdviserService, "_semantic_agent", provider_forbidden)
    monkeypatch.setattr(CandidateAdviserService, "_clarification_agent", provider_forbidden)
    monkeypatch.setattr(ActiveCandidateEvidenceResolver, "resolve", provider_forbidden)
    first = CandidateCompatibilityDryRunService(db_session).inspect(["complete-adviser"])
    second = CandidateCompatibilityDryRunService(db_session).inspect(["complete-adviser"])
    event.remove(bind, "before_cursor_execute", capture)
    db_session.refresh(assessment)

    assert first.model_dump(mode="json", by_alias=True) == second.model_dump(mode="json", by_alias=True)
    result = first.users[0]
    assert result.evidence_status is CandidateEvidenceCompatibilityStatus.COMPLETE
    assert result.adviser_assessment_status == "confirmed"
    assert original == (assessment.status, assessment.input_fingerprint, assessment.assessment_json)
    assert all(statement not in {"INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "CREATE", "DROP"} for statement in statements)

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import User  # noqa: F401
from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft
from app.schemas.candidate_compatibility import (
    CandidateSchemaRepairAction,
    CandidateSchemaStatus,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.services.candidate_compatibility_inspector import (
    CANDIDATE_DOMAIN_TABLES,
    CandidatePhysicalSchemaInspector,
)
from app.services.candidate_sqlite_schema_repair import (
    CandidateSQLiteSchemaCompatibilityRepairService,
    CandidateSchemaCompatibilityRepairBlocked,
    CandidateSchemaCompatibilityRepairFailed,
)


def _engine():
    return create_engine("sqlite://", poolclass=StaticPool)


def _users_table(engine):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))


def _insert_user(engine, user_id="legacy-user"):
    present = {column["name"] for column in inspect(engine).get_columns("users")}
    values = {"id": user_id}
    if "email" in present:
        values["email"] = f"{user_id}@example.test"
    if "password_hash" in present:
        values["password_hash"] = "synthetic-hash"
    if "created_at" in present:
        values["created_at"] = "2026-09-01 00:00:00"
    if "updated_at" in present:
        values["updated_at"] = "2026-09-01 00:00:00"
    columns = list(values)
    names = ", ".join(columns)
    parameters = ", ".join(f":{name}" for name in columns)
    with engine.begin() as connection:
        connection.execute(text(f"INSERT INTO users ({names}) VALUES ({parameters})"), values)


def _create_profile_history(engine, *, include_job_search_criteria=False, broken_owner_fk=False):
    fields = """
        id VARCHAR(36) NOT NULL,
        user_id VARCHAR(36) NOT NULL,
        headline VARCHAR(200), summary TEXT, current_role VARCHAR(200),
        location VARCHAR(200), career_goal TEXT,
    """
    if include_job_search_criteria:
        fields += " job_search_criteria TEXT,"
    fields += " created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL"
    if not broken_owner_fk:
        fields += ", CONSTRAINT fk_candidate_profiles_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
    ddl = f"""
        CREATE TABLE candidate_profiles (
            {fields},
            CONSTRAINT pk_candidate_profiles PRIMARY KEY (id),
            CONSTRAINT uq_candidate_profiles_user_id UNIQUE (user_id)
        )
    """
    with engine.begin() as connection:
        connection.execute(text(ddl))
        if not broken_owner_fk:
            connection.execute(text("CREATE INDEX ix_candidate_profiles_user_id ON candidate_profiles(user_id)"))


def _create_cv_history(engine):
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE candidate_cv_ingestion_drafts (
                id VARCHAR(36) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                state VARCHAR(32) NOT NULL,
                documents_json TEXT NOT NULL,
                merged_json TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT pk_candidate_cv_ingestion_drafts PRIMARY KEY (id),
                CONSTRAINT fk_candidate_cv_ingestion_drafts_user
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
        connection.execute(text("CREATE INDEX ix_candidate_cv_ingestion_drafts_user_id ON candidate_cv_ingestion_drafts(user_id)"))


def _create_revision_table(engine):
    table = Base.metadata.tables["candidate_profile_revisions"]
    with engine.begin() as connection:
        table.create(connection, checkfirst=True)


def _create_pre_overlap_proposal_history(engine):
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE candidate_adviser_profile_proposals (
                id VARCHAR(36) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                proposal_key VARCHAR(64) NOT NULL,
                state VARCHAR(16) NOT NULL,
                revision INTEGER NOT NULL,
                source_clarification_id VARCHAR(64) NOT NULL,
                source_assessment_fingerprint VARCHAR(64) NOT NULL,
                original_update_json TEXT NOT NULL,
                proposed_update_json TEXT NOT NULL,
                transferred_profile_revision_id VARCHAR(36),
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                rejected_at TIMESTAMP,
                transferred_at TIMESTAMP,
                CONSTRAINT pk_candidate_adviser_profile_proposals PRIMARY KEY (id),
                CONSTRAINT uq_candidate_adviser_profile_proposals_user_key UNIQUE (user_id, proposal_key),
                CONSTRAINT ck_candidate_adviser_profile_proposals_state
                    CHECK (state IN ('pending', 'rejected', 'transferred')),
                CONSTRAINT fk_candidate_adviser_profile_proposals_user
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_candidate_adviser_profile_proposals_revision
                    FOREIGN KEY (transferred_profile_revision_id) REFERENCES candidate_profile_revisions(id)
            )
        """))
        connection.execute(text("CREATE INDEX ix_candidate_adviser_profile_proposals_user_id ON candidate_adviser_profile_proposals(user_id)"))
        connection.execute(text("CREATE INDEX ix_candidate_adviser_profile_proposals_source_clarification_id ON candidate_adviser_profile_proposals(source_clarification_id)"))


def _create_proposal_rows(engine):
    _create_revision_table(engine)
    now = "2026-09-01 00:00:00"
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO candidate_profile_revisions (
                id, user_id, active_user_id, state, revision,
                base_profile_fingerprint, base_structured_fingerprint,
                base_editable_structured_fingerprint, proposed_profile_json,
                proposed_structured_json, created_at, updated_at, confirmed_at, discarded_at
            ) VALUES (
                'revision-1', 'legacy-user', NULL, 'confirmed', 1,
                'p', 's', 'e', NULL, NULL, :now, :now, :now, NULL
            )
        """), {"now": now})
    _create_pre_overlap_proposal_history(engine)
    with engine.begin() as connection:
        for proposal_id, state, revision_id in (
            ("proposal-pending", "pending", None),
            ("proposal-rejected", "rejected", None),
            ("proposal-transferred", "transferred", "revision-1"),
        ):
            connection.execute(text("""
                INSERT INTO candidate_adviser_profile_proposals (
                    id, user_id, proposal_key, state, revision,
                    source_clarification_id, source_assessment_fingerprint,
                    original_update_json, proposed_update_json,
                    transferred_profile_revision_id, created_at, updated_at,
                    rejected_at, transferred_at
                ) VALUES (
                    :id, 'legacy-user', :id, :state, 3,
                    'clarification', 'fingerprint', :original_json, :proposed_json,
                    :revision_id, :now, :now, :rejected_at, :transferred_at
                )
            """), {
                "id": proposal_id,
                "state": state,
                "original_json": '{"old":1}',
                "proposed_json": '{"new":2}',
                "revision_id": revision_id,
                "now": now,
                "rejected_at": now if state == "rejected" else None,
                "transferred_at": now if state == "transferred" else None,
            })


def _rows(engine, table, columns):
    quoted = ", ".join(inspect(engine).bind.dialect.identifier_preparer.quote(name) for name in columns)
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(f"SELECT {quoted} FROM {table}"))]


def _captured(engine):
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip())

    event.listen(engine, "before_cursor_execute", capture)
    return statements, capture


def _assert_no_data_dml(statements):
    forbidden = {"INSERT", "UPDATE", "DELETE", "REPLACE", "DROP"}
    assert all(statement.split(None, 1)[0].upper() not in forbidden for statement in statements)


def _assert_only_bounded_ddl(statements):
    for statement in statements:
        normalized = statement.upper()
        if normalized.startswith((
            "CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX",
            "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
        )):
            continue
        if normalized.startswith(("PRAGMA", "SELECT")):
            continue
        if normalized.startswith("ALTER TABLE"):
            assert " ADD COLUMN " in normalized
            continue
        pytest.fail(f"unexpected SQL during bounded schema repair: {statement}")


def test_current_schema_repair_is_idempotent_noop_without_ddl():
    engine = _engine()
    Base.metadata.create_all(engine)
    _insert_user(engine, "u")
    statements, capture = _captured(engine)

    first = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()
    second = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()
    event.remove(engine, "before_cursor_execute", capture)

    assert first.changed is second.changed is False
    assert not first.applied_repairs and not second.applied_repairs
    assert first.before.status is first.after.status is CandidateSchemaStatus.COMPATIBLE
    assert second.before.model_dump() == second.after.model_dump()
    assert not any(statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for statement in statements)
    assert _rows(engine, "users", ["id", "email", "password_hash"])[0] == ("u", "u@example.test", "synthetic-hash")


def test_initial_candidate_profile_schema_adds_seven_null_columns_and_preserves_row():
    engine = _engine()
    _users_table(engine)
    _insert_user(engine)
    _create_profile_history(engine)
    original_columns = ["id", "user_id", "headline", "summary", "current_role", "location", "career_goal", "created_at", "updated_at"]
    original = ("profile-1", "legacy-user", "Head", "Summary", "Role", "Place", "Goal", "2020-01-01", "2020-01-02")
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO candidate_profiles (id, user_id, headline, summary, current_role, location, career_goal, created_at, updated_at)
            VALUES (:id, :user_id, :headline, :summary, :role, :location, :goal, :created, :updated)
        """), dict(zip(("id", "user_id", "headline", "summary", "role", "location", "goal", "created", "updated"), original)))
        connection.execute(text("DROP INDEX ix_candidate_profiles_user_id"))
    before = _rows(engine, "candidate_profiles", original_columns)
    statements, capture = _captured(engine)

    result = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()
    event.remove(engine, "before_cursor_execute", capture)

    columns = {column["name"] for column in inspect(engine).get_columns("candidate_profiles")}
    added = ["job_search_criteria", "display_name", "preferred_email", "phone", "linkedin_url", "github_url", "portfolio_url"]
    assert set(added) <= columns
    assert _rows(engine, "candidate_profiles", original_columns) == before
    assert _rows(engine, "candidate_profiles", added) == [(None,) * len(added)]
    assert result.after.status is CandidateSchemaStatus.COMPATIBLE
    assert {repair.column for repair in result.applied_repairs if repair.action is CandidateSchemaRepairAction.ADD_COLUMN} == set(added)
    action_positions = [repair.action for repair in result.applied_repairs]
    last_table = max(index for index, action in enumerate(action_positions) if action is CandidateSchemaRepairAction.CREATE_TABLE)
    first_column = min(index for index, action in enumerate(action_positions) if action is CandidateSchemaRepairAction.ADD_COLUMN)
    first_index = min(index for index, action in enumerate(action_positions) if action is CandidateSchemaRepairAction.CREATE_INDEX)
    assert last_table < first_column < first_index
    _assert_no_data_dml(statements)
    _assert_only_bounded_ddl(statements)


def test_august_profile_schema_preserves_existing_job_search_criteria():
    engine = _engine()
    _users_table(engine)
    _insert_user(engine)
    _create_profile_history(engine, include_job_search_criteria=True)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO candidate_profiles (
                id, user_id, headline, summary, current_role, location, career_goal,
                job_search_criteria, created_at, updated_at
            ) VALUES ('profile-aug', 'legacy-user', 'h', 's', 'r', 'l', 'g', 'keep criteria', '2020', '2021')
        """))

    result = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()

    columns = {column["name"] for column in inspect(engine).get_columns("candidate_profiles")}
    added = {"display_name", "preferred_email", "phone", "linkedin_url", "github_url", "portfolio_url"}
    assert added <= columns
    assert _rows(engine, "candidate_profiles", ["job_search_criteria", *sorted(added)]) == [("keep criteria", *([None] * len(added)))]
    assert {repair.column for repair in result.applied_repairs if repair.action is CandidateSchemaRepairAction.ADD_COLUMN} == added
    assert result.after.status is CandidateSchemaStatus.COMPATIBLE


def test_incompatible_existing_column_type_blocks_schema_repair():
    engine = _engine()
    _users_table(engine)
    _insert_user(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE candidate_profiles (
                id VARCHAR(36) NOT NULL, user_id VARCHAR(36) NOT NULL,
                headline VARCHAR(40), summary TEXT, current_role VARCHAR(200),
                location VARCHAR(200), career_goal TEXT,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT pk_candidate_profiles PRIMARY KEY (id),
                CONSTRAINT uq_candidate_profiles_user_id UNIQUE (user_id),
                CONSTRAINT fk_candidate_profiles_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
        connection.execute(text("CREATE INDEX ix_candidate_profiles_user_id ON candidate_profiles(user_id)"))
    before = _rows(engine, "candidate_profiles", ["id", "user_id", "headline"])

    with pytest.raises(CandidateSchemaCompatibilityRepairBlocked):
        CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()

    assert _rows(engine, "candidate_profiles", ["id", "user_id", "headline"]) == before
    assert "display_name" not in {column["name"] for column in inspect(engine).get_columns("candidate_profiles")}


def test_missing_existing_table_unique_constraint_is_not_retrofitted():
    engine = _engine()
    _users_table(engine)
    _insert_user(engine)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE candidate_profiles (
                id VARCHAR(36) NOT NULL, user_id VARCHAR(36) NOT NULL,
                headline VARCHAR(200), summary TEXT, current_role VARCHAR(200),
                location VARCHAR(200), career_goal TEXT,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
                CONSTRAINT pk_candidate_profiles PRIMARY KEY (id),
                CONSTRAINT fk_candidate_profiles_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
        connection.execute(text("CREATE INDEX ix_candidate_profiles_user_id ON candidate_profiles(user_id)"))
    before = CandidatePhysicalSchemaInspector(engine).inspect()
    profile = next(table for table in before.tables if table.table == "candidate_profiles")
    assert profile.status is CandidateSchemaStatus.UNSUPPORTED_DRIFT
    assert profile.missing_constraints
    with pytest.raises(CandidateSchemaCompatibilityRepairBlocked):
        CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()


def test_legacy_cv_drafts_get_null_runtime_attribution_and_read_through_current_service():
    engine = _engine()
    _users_table(engine)
    _insert_user(engine)
    _create_cv_history(engine)
    originals = [
        ("draft-uploaded", "uploaded", None),
        ("draft-review", "review_ready", CandidateCVData().model_dump_json()),
        ("draft-confirmed", "confirmed", CandidateCVData().model_dump_json()),
    ]
    with engine.begin() as connection:
        for draft_id, state, payload in originals:
            connection.execute(text("""
                INSERT INTO candidate_cv_ingestion_drafts
                    (id, user_id, state, documents_json, merged_json, created_at, updated_at)
                    VALUES (:id, 'legacy-user', :state, '[]', :payload,
                            '2020-01-01 00:00:00', '2021-01-01 00:00:00')
            """), {"id": draft_id, "state": state, "payload": payload})
    columns = ["id", "user_id", "state", "documents_json", "merged_json", "created_at", "updated_at"]
    before = _rows(engine, "candidate_cv_ingestion_drafts", columns)

    result = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()

    assert _rows(engine, "candidate_cv_ingestion_drafts", columns) == before
    assert _rows(engine, "candidate_cv_ingestion_drafts", ["runtime_attribution_json"] * 1) == [(None,), (None,), (None,)]
    assert result.after.status is CandidateSchemaStatus.COMPATIBLE
    from sqlalchemy.orm import Session
    from app.services.cv_ingestion_service import CVIngestionService

    with Session(engine) as session:
        read = CVIngestionService(session).read("legacy-user", "draft-confirmed")
        assert read.state.value == "confirmed"
        assert read.runtime_attribution is not None
        assert read.runtime_attribution.status.value == "legacy_unavailable"


def test_pre_overlap_adviser_proposals_add_null_resolution_without_changing_lifecycle():
    engine = _engine()
    _users_table(engine)
    _insert_user(engine)
    _create_proposal_rows(engine)
    columns = ["id", "state", "revision", "source_clarification_id", "transferred_profile_revision_id", "rejected_at", "transferred_at"]
    before = _rows(engine, "candidate_adviser_profile_proposals", columns)

    result = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()

    assert _rows(engine, "candidate_adviser_profile_proposals", columns) == before
    assert _rows(engine, "candidate_adviser_profile_proposals", ["overlap_resolution_json"] * 1) == [(None,), (None,), (None,)]
    assert result.after.status is CandidateSchemaStatus.COMPATIBLE
    assert {row[1] for row in before} == {"pending", "rejected", "transferred"}


def test_missing_candidate_tables_created_in_metadata_order_and_verified_empty():
    engine = _engine()
    _users_table(engine)
    _insert_user(engine)

    result = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()

    assert result.before.status is CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE
    assert result.after.status is CandidateSchemaStatus.COMPATIBLE
    created = [repair.table for repair in result.applied_repairs if repair.action is CandidateSchemaRepairAction.CREATE_TABLE]
    assert set(created) == set(CANDIDATE_DOMAIN_TABLES)
    assert created.index("candidate_profile_revisions") < created.index("candidate_adviser_profile_proposals")
    assert created.index("candidate_cv_ingestion_drafts") < created.index("candidate_cv_review_baselines")
    assert CandidatePhysicalSchemaInspector(engine).inspect().status is CandidateSchemaStatus.COMPATIBLE
    for table_name in (
        "candidate_cv_review_baselines", "candidate_adviser_clarifications",
        "candidate_profile_revisions", "candidate_adviser_profile_proposals",
        "candidate_structured_item_lineage", "candidate_cv_overlap_reviews",
    ):
        with engine.connect() as connection:
            assert connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) == 0
    second = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()
    assert second.changed is False
    assert second.applied_repairs == []


def test_missing_nonunique_index_is_restored_from_current_metadata():
    engine = _engine()
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_candidate_profiles_user_id"))
    before = CandidatePhysicalSchemaInspector(engine).inspect()
    profile = next(table for table in before.tables if table.table == "candidate_profiles")
    assert profile.status is CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE
    assert "ix_candidate_profiles_user_id" in profile.missing_indexes

    result = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()

    assert result.after.status is CandidateSchemaStatus.COMPATIBLE
    assert any(repair.index == "ix_candidate_profiles_user_id" for repair in result.applied_repairs)
    assert any(index["name"] == "ix_candidate_profiles_user_id" for index in inspect(engine).get_indexes("candidate_profiles"))


def test_unsupported_drift_blocks_all_safe_repairs_and_executes_zero_ddl():
    engine = _engine()
    _users_table(engine)
    _insert_user(engine)
    _create_profile_history(engine, broken_owner_fk=True)
    statements, capture = _captured(engine)

    with pytest.raises(CandidateSchemaCompatibilityRepairBlocked):
        CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()
    event.remove(engine, "before_cursor_execute", capture)

    assert not any(statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for statement in statements)
    assert "display_name" not in {column["name"] for column in inspect(engine).get_columns("candidate_profiles")}
    assert not inspect(engine).has_table("candidate_cv_review_baselines")
    _assert_no_data_dml(statements)


def test_executor_rejects_unreviewed_nullable_missing_column(db_session, monkeypatch):
    from app.schemas.candidate_compatibility import (
        CandidateColumnCompatibility,
        CandidateSchemaCompatibilityRead,
        CandidateTableCompatibility,
    )

    bind = db_session.get_bind()
    before = CandidatePhysicalSchemaInspector(bind).inspect()
    tables = []
    for table in before.tables:
        if table.table == "candidate_profiles":
            table = table.model_copy(update={
                "missing_columns": [CandidateColumnCompatibility(
                    name="summary", present=False, expected_type="TEXT", expected_nullable=True,
                )],
                "status": CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE,
            })
        tables.append(table)
    synthetic = CandidateSchemaCompatibilityRead(
        dialect="sqlite", tables=tables,
        status=CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE,
    )
    monkeypatch.setattr(CandidatePhysicalSchemaInspector, "inspect", lambda self: synthetic)

    with pytest.raises(CandidateSchemaCompatibilityRepairBlocked, match="outside the reviewed"):
        CandidateSQLiteSchemaCompatibilityRepairService(bind).repair()


def test_missing_users_table_blocks_repair_without_ddl():
    engine = _engine()
    statements, capture = _captured(engine)
    with pytest.raises(CandidateSchemaCompatibilityRepairBlocked, match="users table prerequisite"):
        CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()
    event.remove(engine, "before_cursor_execute", capture)
    assert not any(statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for statement in statements)
    assert not any(inspect(engine).has_table(table) for table in CANDIDATE_DOMAIN_TABLES)


def test_non_sqlite_dialect_is_rejected_before_connecting():
    class NonSQLiteEngine:
        dialect = type("Dialect", (), {"name": "postgresql"})()

    with pytest.raises(ValueError, match="SQLite only"):
        CandidateSQLiteSchemaCompatibilityRepairService(NonSQLiteEngine()).repair()  # type: ignore[arg-type]


def test_repair_does_not_call_candidate_data_mutation_paths(db_session, monkeypatch):
    from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
    from app.services.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalService
    from app.services.candidate_adviser_service import CandidateAdviserService
    from app.services.candidate_structured_item_lineage import CandidateStructuredItemLineageService
    from app.services.cv_ingestion_service import CVIngestionService
    from app.services.profile_revision_service import CandidateProfileRevisionService

    def forbidden(*_args, **_kwargs):
        raise AssertionError("schema repair invoked candidate data reconciliation")

    for target, name in (
        (ActiveCandidateEvidenceResolver, "resolve"),
        (CVIngestionService, "confirm"),
        (CandidateProfileRevisionService, "confirm"),
        (CandidateAdviserProfileProposalService, "transfer_to_profile_revision"),
        (CandidateAdviserService, "confirm_clarification"),
        (CandidateStructuredItemLineageService, "record"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    engine = _engine()
    _users_table(engine)

    result = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()

    assert result.after.status is CandidateSchemaStatus.COMPATIBLE
    assert result.changed


class _FailOnSecondOperation(CandidateSQLiteSchemaCompatibilityRepairService):
    def __init__(self, engine):
        super().__init__(engine)
        self.calls = 0

    def _apply_operation(self, connection, operation):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("injected DDL failure")
        return super()._apply_operation(connection, operation)


def test_failed_ddl_rolls_back_and_retry_matches_clean_repair():
    interrupted = _engine()
    _users_table(interrupted)
    with pytest.raises(CandidateSchemaCompatibilityRepairFailed) as captured:
        _FailOnSecondOperation(interrupted).repair()
    failure = captured.value
    assert failure.attempted_repairs
    observed_after_failure = CandidatePhysicalSchemaInspector(interrupted).inspect()

    resumed = CandidateSQLiteSchemaCompatibilityRepairService(interrupted).repair()
    clean = _engine()
    _users_table(clean)
    one_shot = CandidateSQLiteSchemaCompatibilityRepairService(clean).repair()

    assert resumed.after.status is one_shot.after.status is CandidateSchemaStatus.COMPATIBLE
    assert resumed.after.model_dump(mode="json") == one_shot.after.model_dump(mode="json")
    assert observed_after_failure.status in {
        CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE,
        CandidateSchemaStatus.COMPATIBLE,
    }
    assert not any(observed_after_failure_table.table_exists for observed_after_failure_table in observed_after_failure.tables)
    second = CandidateSQLiteSchemaCompatibilityRepairService(interrupted).repair()
    assert second.changed is False


def test_failure_attempted_repairs_report_only_completed_operations():
    engine = _engine()
    _users_table(engine)
    with pytest.raises(CandidateSchemaCompatibilityRepairFailed) as captured:
        _FailOnSecondOperation(engine).repair()
    assert len(captured.value.attempted_repairs) == 1
    assert captured.value.attempted_repairs[0].action is CandidateSchemaRepairAction.CREATE_TABLE


def test_candidate_repair_does_not_plan_unrelated_artifact_tables():
    engine = _engine()
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE candidate_cv_review_baselines"))
    _insert_user(engine, "u")
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO discovery_runs (
                id, user_id, search_input_json, search_input_fingerprint,
                candidate_evaluation_fingerprint, evaluation_contract_fingerprint,
                status, funnel_json, failure_summary_json, started_at
            ) VALUES ('run', 'u', '{}', 'a', 'b', 'c', 'complete', 'saved-funnel', '{}', '2026-01-01')
        """))
        connection.execute(text("""
            INSERT INTO application_preparations (
                id, user_id, target_snapshot_json, identity_snapshot_json,
                preparation_input_fingerprint, preparation_contract_fingerprint,
                preparation_result_json, created_at
            ) VALUES ('prep', 'u', 'old-target', '{}', 'd', 'e', 'old-result', '2026-01-01')
        """))
    artifact_before = (
        _rows(engine, "discovery_runs", ["id", "funnel_json"]),
        _rows(engine, "application_preparations", ["id", "target_snapshot_json", "preparation_result_json"]),
    )
    statements, capture = _captured(engine)

    result = CandidateSQLiteSchemaCompatibilityRepairService(engine).repair()
    event.remove(engine, "before_cursor_execute", capture)

    assert result.after.status is CandidateSchemaStatus.COMPATIBLE
    assert {repair.table for repair in result.applied_repairs} == {"candidate_cv_review_baselines"}
    artifact_after = (
        _rows(engine, "discovery_runs", ["id", "funnel_json"]),
        _rows(engine, "application_preparations", ["id", "target_snapshot_json", "preparation_result_json"]),
    )
    assert artifact_after == artifact_before
    _assert_no_data_dml(statements)
    assert not any(
        any(name in statement.lower() for name in ("discovery_runs", "user_job_evaluations", "application_preparations"))
        for statement in statements
    )


def test_phase1_dry_run_remains_read_only_and_does_not_call_repair(db_session, monkeypatch):
    from app.services.candidate_legacy_compatibility_inspector import CandidateCompatibilityDryRunService

    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run must not apply schema repair")

    monkeypatch.setattr(CandidateSQLiteSchemaCompatibilityRepairService, "repair", forbidden)
    plan = CandidateCompatibilityDryRunService(db_session).inspect([])
    assert plan.schema_report.status is CandidateSchemaStatus.COMPATIBLE

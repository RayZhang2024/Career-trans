import asyncio
import logging

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import User  # noqa: F401
from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.schemas.candidate_compatibility import (
    CandidateCompatibilityStatus,
    CandidateCompatibilityDatabaseState,
    CandidateCompatibilityOperation,
    CandidateBatchReconciliationRead,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.services.candidate_compatibility_runtime import (
    CandidateCompatibilityRuntime,
    CandidateCompatibilityRuntimeBlocked,
    run_candidate_compatibility_startup,
)
from app.services.candidate_legacy_data_reconciliation import CandidateLegacyDataReconciliationService


def _engine():
    return create_engine("sqlite://", poolclass=StaticPool)


def _mixed_user_engine():
    engine = _engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all([
            User(id="a-reconstruct", email="a@example.test", password_hash="fixture"),
            User(id="b-malformed", email="b@example.test", password_hash="fixture"),
            User(id="c-not-confirmed", email="c@example.test", password_hash="fixture"),
        ])
        data = CandidateCVData.model_validate({
            "employment": [{"employer": "Example", "title": "Engineer"}],
        })
        session.add_all([
            CandidateCVIngestionDraft(
                id="confirmed-cv",
                user_id="a-reconstruct",
                state="confirmed",
                documents_json="[]",
                merged_json=data.model_dump_json(),
            ),
            CandidateCVIngestionDraft(
                id="malformed-cv",
                user_id="b-malformed",
                state="confirmed",
                documents_json="[]",
                merged_json="{",
            ),
            CandidateProfile(user_id="c-not-confirmed", headline="Keep this scalar profile"),
        ])
    return engine


def _non_sqlite_engine():
    engine = _engine()
    engine.dialect.name = "postgresql"
    connections = []
    event.listen(engine, "connect", lambda *args: connections.append(True))
    return engine, connections


def test_inspect_empty_database_is_read_only_and_reports_fresh_state():
    engine = _engine()
    result = CandidateCompatibilityRuntime(engine).inspect()
    assert result.operation is CandidateCompatibilityOperation.INSPECT
    assert result.database_state is CandidateCompatibilityDatabaseState.FRESH_UNINITIALIZED
    assert inspect(engine).get_table_names() == []


def test_inspect_retained_database_without_users_is_read_only():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE retained_data (id INTEGER PRIMARY KEY)"))
    before = set(inspect(engine).get_table_names())
    result = CandidateCompatibilityRuntime(engine).inspect()
    assert result.database_state is CandidateCompatibilityDatabaseState.RETAINED_WITHOUT_USERS
    assert set(inspect(engine).get_table_names()) == before


def test_apply_empty_database_bootstraps_and_returns_zero_user_result():
    engine = _engine()
    result = CandidateCompatibilityRuntime(engine).apply()
    assert result.database_state is CandidateCompatibilityDatabaseState.FRESH_UNINITIALIZED
    assert result.operation is CandidateCompatibilityOperation.APPLY
    assert result.user_count == result.changed_count == result.unresolved_count == 0
    assert not result.repair_changed
    assert result.schema_after.status.value == "compatible"
    assert "users" in inspect(engine).get_table_names()


def test_apply_retained_database_without_users_blocks_before_create_all(monkeypatch):
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE retained_data (id INTEGER PRIMARY KEY)"))
    calls = []
    original = Base.metadata.create_all

    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(Base.metadata, "create_all", tracked)
    with pytest.raises(CandidateCompatibilityRuntimeBlocked, match="no users table"):
        CandidateCompatibilityRuntime(engine).apply()
    assert calls == []
    assert set(inspect(engine).get_table_names()) == {"retained_data"}


def test_apply_retained_users_repairs_candidate_schema_then_reconciles():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users (id) VALUES ('legacy-user')"))
    result = CandidateCompatibilityRuntime(engine).apply()
    assert result.database_state is CandidateCompatibilityDatabaseState.RETAINED_WITH_USERS
    assert result.schema_after.status.value == "compatible"
    assert result.user_count == 1
    assert result.reconciliation.users[0].user_id == "legacy-user"
    assert result.unresolved_count == result.reconciliation.unresolved_count


def test_startup_repairs_exact_pre_transfer_proposal_schema_and_preserves_rows():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users (id) VALUES ('legacy-user')"))
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
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                rejected_at DATETIME,
                CONSTRAINT pk_candidate_adviser_profile_proposals PRIMARY KEY (id),
                CONSTRAINT uq_candidate_adviser_profile_proposals_user_key UNIQUE (user_id, proposal_key),
                CONSTRAINT ck_candidate_adviser_profile_proposals_state CHECK (state IN ('pending', 'rejected')),
                CONSTRAINT fk_candidate_adviser_profile_proposals_user
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """))
        connection.execute(text("CREATE INDEX ix_candidate_adviser_profile_proposals_user_id ON candidate_adviser_profile_proposals(user_id)"))
        connection.execute(text("CREATE INDEX ix_candidate_adviser_profile_proposals_source_clarification_id ON candidate_adviser_profile_proposals(source_clarification_id)"))
        connection.execute(text("""
            INSERT INTO candidate_adviser_profile_proposals (
                id, user_id, proposal_key, state, revision, source_clarification_id,
                source_assessment_fingerprint, original_update_json, proposed_update_json,
                created_at, updated_at
            ) VALUES (
                'kept-proposal', 'legacy-user', 'kept-key', 'pending', 9, 'source-1',
                'source-fingerprint', '{"old": true}', '{"new": true}',
                '2026-01-01 00:00:00', '2026-01-02 00:00:00'
            )
        """))

    result = CandidateCompatibilityRuntime(engine).apply()

    assert result.schema_after.status.value == "compatible"
    assert result.repair_changed
    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT id, user_id, proposal_key, state, revision, source_clarification_id,
                   source_assessment_fingerprint, original_update_json, proposed_update_json,
                   created_at, updated_at, rejected_at, overlap_resolution_json,
                   transferred_profile_revision_id, transferred_at
            FROM candidate_adviser_profile_proposals WHERE id = 'kept-proposal'
        """)).one()
    assert tuple(row) == (
        "kept-proposal", "legacy-user", "kept-key", "pending", 9, "source-1",
        "source-fingerprint", '{"old": true}', '{"new": true}',
        "2026-01-01 00:00:00", "2026-01-02 00:00:00", None, None, None, None,
    )


def test_mixed_user_apply_counts_reconciliation_statuses_and_preserves_not_confirmed_user():
    engine = _mixed_user_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    result = CandidateCompatibilityRuntime(engine).apply()

    assert result.user_count == 3
    assert result.changed_count == 1
    assert result.unresolved_count == 1
    assert result.not_yet_confirmed_count == 1
    by_id = {user.user_id: user for user in result.reconciliation.users}
    assert by_id["a-reconstruct"].changed
    assert by_id["b-malformed"].status_after is CandidateCompatibilityStatus.UNRESOLVED
    assert by_id["c-not-confirmed"].status_after is CandidateCompatibilityStatus.NOT_YET_CONFIRMED
    with factory() as session:
        scalar = session.query(CandidateProfile).filter_by(user_id="c-not-confirmed").one()
        assert scalar.headline == "Keep this scalar profile"
        assert session.query(CandidateStructuredProfile).filter_by(user_id="c-not-confirmed").first() is None


def test_startup_returns_and_logs_not_yet_confirmed_count(caplog, monkeypatch):
    from app import main

    engine = _mixed_user_engine()
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "configure_langsmith_environment", lambda settings: None)
    original_startup = main.run_candidate_compatibility_startup
    observed = []

    def startup(target_engine):
        result = original_startup(target_engine)
        observed.append(result)
        return result

    monkeypatch.setattr(main, "run_candidate_compatibility_startup", startup)
    caplog.set_level(logging.INFO)

    async def run_lifespan():
        async with main.lifespan(main.app):
            pass

    asyncio.run(run_lifespan())
    assert observed[0].not_yet_confirmed_count == 1
    assert "users=3 changed=1 unresolved=1 not_yet_confirmed=1" in caplog.text
    assert "c-not-confirmed" not in caplog.text
    assert "left 1 user record(s) unresolved" in caplog.text


def test_human_cli_summary_reports_not_yet_confirmed_count(capsys):
    from app.cli import _print_compatibility_result

    result = CandidateCompatibilityRuntime(_mixed_user_engine()).apply()
    _print_compatibility_result(result, as_json=False)
    assert (
        "apply: state=retained_with_users users=3 changed=1 unresolved=1 not_yet_confirmed=1"
        in capsys.readouterr().out
    )


def test_apply_stops_before_broad_create_all_on_unsupported_candidate_drift(monkeypatch):
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("""
            CREATE TABLE candidate_profiles (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL
            )
        """))
    calls = []
    original = Base.metadata.create_all

    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(Base.metadata, "create_all", tracked)
    with pytest.raises(CandidateCompatibilityRuntimeBlocked):
        CandidateCompatibilityRuntime(engine).apply()
    assert calls == []


def test_startup_non_sqlite_preserves_existing_create_all_path(monkeypatch):
    engine = create_engine("sqlite://")
    engine.dialect.name = "postgresql"
    calls = []
    monkeypatch.setattr(Base.metadata, "create_all", lambda **kwargs: calls.append(kwargs))
    assert run_candidate_compatibility_startup(engine) is None
    assert calls == [{"bind": engine}]


@pytest.mark.parametrize("operation", ["inspect", "apply"])
def test_runtime_rejects_non_sqlite_without_connecting(operation):
    engine, connections = _non_sqlite_engine()
    runtime = CandidateCompatibilityRuntime(engine)
    with pytest.raises(
        CandidateCompatibilityRuntimeBlocked,
        match="inspect/apply supports SQLite databases only",
    ):
        getattr(runtime, operation)()
    assert connections == []


@pytest.mark.parametrize(("operation", "approval"), [("inspect", []), ("apply", ["--yes"])])
def test_cli_non_sqlite_operations_fail_without_connecting(monkeypatch, capsys, operation, approval):
    import app.cli as cli

    engine, connections = _non_sqlite_engine()
    monkeypatch.setattr(cli, "create_engine", lambda *args, **kwargs: engine)
    result = cli.main([
        "dev",
        "candidate-compatibility",
        operation,
        "--database-url",
        "postgresql://offline.invalid/career_trans",
        *approval,
    ])
    assert result == 2
    assert "inspect/apply supports SQLite databases only" in capsys.readouterr().err
    assert connections == []


def test_startup_does_not_fail_for_unresolved_user_results(monkeypatch):
    engine = _engine()
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id, email, password_hash, created_at, updated_at) "
            "VALUES ('legacy-user', 'legacy@example.test', 'fixture', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
    observed = []

    def unresolved(self, user_ids):
        observed.extend(user_ids)
        return CandidateBatchReconciliationRead(
            users=[], user_count=len(observed), changed_count=0, unresolved_count=len(observed)
        )

    monkeypatch.setattr(CandidateLegacyDataReconciliationService, "reconcile_users", unresolved)
    result = run_candidate_compatibility_startup(engine)
    assert result.unresolved_count == 1
    assert observed == ["legacy-user"]


def test_application_lifespan_runs_sqlite_compatibility_workflow(monkeypatch):
    from app import main

    engine = _engine()
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "configure_langsmith_environment", lambda settings: None)

    async def run():
        async with main.lifespan(main.app):
            assert "users" in inspect(engine).get_table_names()

    asyncio.run(run())


def test_cli_inspect_does_not_create_missing_database_file(tmp_path, capsys):
    from app.cli import main

    database = tmp_path / "not-created.db"
    assert main(
        ["dev", "candidate-compatibility", "inspect", "--database-url", f"sqlite:///{database}", "--json"]
    ) == 0
    assert not database.exists()
    assert '"database_state": "fresh_uninitialized"' in capsys.readouterr().out


def test_cli_apply_requires_explicit_approval(tmp_path, capsys):
    from app.cli import main

    database = tmp_path / "not-created.db"
    assert main(["dev", "candidate-compatibility", "apply", "--database-url", f"sqlite:///{database}"]) == 2
    assert not database.exists()
    assert "requires --yes" in capsys.readouterr().err


def test_cli_inspect_existing_sqlite_file_uses_read_only_connection(tmp_path, capsys):
    from app.cli import main

    database = tmp_path / "existing.db"
    file_engine = create_engine(f"sqlite:///{database}")
    with file_engine.begin() as connection:
        connection.execute(text("CREATE TABLE retained_data (id INTEGER PRIMARY KEY)"))
    file_engine.dispose()
    before = database.read_bytes()
    assert main(
        ["dev", "candidate-compatibility", "inspect", "--database-url", f"sqlite:///{database}", "--json"]
    ) == 0
    assert database.read_bytes() == before
    assert '"retained_without_users"' in capsys.readouterr().out

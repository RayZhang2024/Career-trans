import asyncio

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import User  # noqa: F401
from app.schemas.candidate_compatibility import (
    CandidateCompatibilityDatabaseState,
    CandidateCompatibilityOperation,
    CandidateBatchReconciliationRead,
)
from app.services.candidate_compatibility_runtime import (
    CandidateCompatibilityRuntime,
    CandidateCompatibilityRuntimeBlocked,
    run_candidate_compatibility_startup,
)
from app.services.candidate_legacy_data_reconciliation import CandidateLegacyDataReconciliationService


def _engine():
    return create_engine("sqlite://", poolclass=StaticPool)


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

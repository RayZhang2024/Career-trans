import importlib.util
import json
import sqlite3
from pathlib import Path


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_sqlite_options_migration_preserves_legacy_rows_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "retained.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.executescript("""
            CREATE TABLE candidate_adviser_clarifications (
                user_id TEXT NOT NULL, clarification_id TEXT NOT NULL,
                question_text TEXT NOT NULL, answer_text TEXT,
                interpretation_json TEXT, status TEXT NOT NULL
            );
            INSERT INTO candidate_adviser_clarifications
            VALUES ('user-a', 'clarification-a', 'Question?', 'Old answer', NULL, 'review_ready');
        """)
        spec = importlib.util.spec_from_file_location(
            "candidate_adviser_clarification_options_sqlite",
            MIGRATIONS / "20261005_candidate_adviser_clarification_options_sqlite.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.upgrade(connection)
        module.upgrade(connection)
        columns = {row[1]: row for row in connection.execute(
            "PRAGMA table_info(candidate_adviser_clarifications)"
        )}
        row = connection.execute(
            "SELECT user_id, clarification_id, answer_text, suggested_answers_json, structured_response_json "
            "FROM candidate_adviser_clarifications"
        ).fetchone()

    assert columns["suggested_answers_json"][3] == 0
    assert columns["structured_response_json"][3] == 0
    assert row == ("user-a", "clarification-a", "Old answer", None, None)


def test_postgresql_migration_uses_additive_nullable_columns() -> None:
    sql = (MIGRATIONS / "20261005_candidate_adviser_clarification_options_postgresql.sql").read_text()
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 2
    assert "suggested_answers_json TEXT NULL" in sql
    assert "structured_response_json TEXT NULL" in sql

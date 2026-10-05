import runpy
import sqlite3
from pathlib import Path


def test_sqlite_refinement_migration_upgrades_existing_adviser_rows_idempotently(tmp_path: Path) -> None:
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "migrations" / "20261006_candidate_adviser_bounded_refinement_sqlite.py"))
    database = tmp_path / "legacy-adviser.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE users (id VARCHAR(36) PRIMARY KEY);
        INSERT INTO users (id) VALUES ('synthetic-user');
        CREATE TABLE candidate_adviser_assessments (
            id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,
            input_fingerprint VARCHAR(64) NOT NULL, status VARCHAR(16) NOT NULL,
            assessment_json TEXT NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
        );
        INSERT INTO candidate_adviser_assessments VALUES
            ('assessment', 'synthetic-user', 'fingerprint', 'confirmed', '{"open_questions":[]}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        CREATE TABLE candidate_adviser_clarifications (
            id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,
            clarification_id VARCHAR(64) NOT NULL, question_key VARCHAR(64) NOT NULL,
            origin_assessment_fingerprint VARCHAR(64) NOT NULL, question_text TEXT NOT NULL,
            question_source_references_json TEXT NOT NULL, status VARCHAR(16) NOT NULL,
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
        );
        INSERT INTO candidate_adviser_clarifications VALUES
            ('clarification', 'synthetic-user', 'question-id', 'question-key', 'fingerprint',
             'Legacy question?', '[]', 'confirmed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
    """)
    migration["upgrade"](connection)
    migration["upgrade"](connection)

    assessment = connection.execute(
        "SELECT contract_version, assessment_json, status FROM candidate_adviser_assessments WHERE id='assessment'"
    ).fetchone()
    clarification = connection.execute(
        "SELECT parent_area_id, round_number, question_text, status FROM candidate_adviser_clarifications WHERE id='clarification'"
    ).fetchone()
    assert assessment == ("legacy_questions", '{"open_questions":[]}', "confirmed")
    assert clarification == (None, None, "Legacy question?", "confirmed")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"candidate_adviser_refinement_journeys", "candidate_adviser_clarification_areas"} <= tables
    connection.close()

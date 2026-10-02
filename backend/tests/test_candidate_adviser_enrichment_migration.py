import json
import runpy
import sqlite3
from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "20261002_candidate_adviser_enrichments_sqlite.py"


def test_backfill_is_conservative_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "adviser-enrichment.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE users (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE candidate_adviser_clarifications (
                user_id VARCHAR(36) NOT NULL, clarification_id VARCHAR(64) NOT NULL,
                origin_assessment_fingerprint VARCHAR(64) NOT NULL, interpretation_json TEXT,
                status VARCHAR(16) NOT NULL, confirmed_at DATETIME, created_at DATETIME
            );
            CREATE TABLE candidate_adviser_profile_proposals (
                user_id VARCHAR(36) NOT NULL, source_clarification_id VARCHAR(64) NOT NULL
            );
            INSERT INTO users VALUES ('user-a');
        """)
        for clarification_id, status, kind in (
            ("proposal-source", "confirmed", "career_fact"),
            ("pending-source", "confirmed", "mixed"),
            ("preference-source", "confirmed", "preference_intent"),
            ("unconfirmed-source", "review_ready", "career_fact"),
        ):
            connection.execute(
                "INSERT INTO candidate_adviser_clarifications VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                ("user-a", clarification_id, "f" * 64, json.dumps({"answer_kind": kind, "proposed_evidence": []}), status),
            )
        connection.execute(
            "INSERT INTO candidate_adviser_profile_proposals VALUES ('user-a', 'proposal-source')"
        )

        upgrade = runpy.run_path(str(MIGRATION))["upgrade"]
        upgrade(connection)
        upgrade(connection)
        states = dict(connection.execute(
            "SELECT clarification_id, state FROM candidate_adviser_enrichments"
        ))

    assert states == {"proposal-source": "proposals_created", "pending-source": "pending"}
    assert "reviewed_no_update" not in states.values()

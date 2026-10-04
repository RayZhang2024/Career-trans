import sqlite3
from pathlib import Path
import runpy


MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "20261005_candidate_adviser_applied_proposals_sqlite.py"


def test_sqlite_upgrade_preserves_transferred_history_and_accepts_applied(tmp_path: Path) -> None:
    database = tmp_path / "candidate-adviser-proposals.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE users (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE candidate_profile_revisions (id VARCHAR(36) PRIMARY KEY);
            CREATE TABLE candidate_adviser_profile_proposals (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                proposal_key VARCHAR(64) NOT NULL,
                state VARCHAR(16) NOT NULL CHECK (state IN ('pending', 'rejected', 'transferred')),
                revision INTEGER NOT NULL,
                source_clarification_id VARCHAR(64) NOT NULL,
                source_assessment_fingerprint VARCHAR(64) NOT NULL,
                original_update_json TEXT NOT NULL,
                proposed_update_json TEXT NOT NULL,
                overlap_resolution_json TEXT,
                transferred_profile_revision_id VARCHAR(36) REFERENCES candidate_profile_revisions(id),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                rejected_at DATETIME,
                transferred_at DATETIME,
                CONSTRAINT uq_candidate_adviser_profile_proposals_user_key UNIQUE (user_id, proposal_key)
            );
            CREATE INDEX ix_candidate_adviser_profile_proposals_user_id ON candidate_adviser_profile_proposals(user_id);
            CREATE INDEX ix_candidate_adviser_profile_proposals_source_clarification_id ON candidate_adviser_profile_proposals(source_clarification_id);
            INSERT INTO users VALUES ('user-a');
            INSERT INTO candidate_profile_revisions VALUES ('revision-a');
            INSERT INTO candidate_adviser_profile_proposals VALUES (
              'proposal-a', 'user-a', 'key-a', 'transferred', 4, 'clarification-a', 'f',
              '{"section":"skills"}', '{"section":"skills"}', NULL, 'revision-a',
              'created', 'updated', NULL, 'transferred'
            );
        """)

        upgrade = runpy.run_path(str(MIGRATION))["upgrade"]
        upgrade(connection)
        upgrade(connection)
        preserved = connection.execute(
            "SELECT id, state, revision, transferred_profile_revision_id, transferred_at, applied_at "
            "FROM candidate_adviser_profile_proposals WHERE id='proposal-a'"
        ).fetchone()
        assert preserved == ("proposal-a", "transferred", 4, "revision-a", "transferred", None)
        connection.execute("""INSERT INTO candidate_adviser_profile_proposals (
          id,user_id,proposal_key,state,revision,source_clarification_id,source_assessment_fingerprint,
          original_update_json,proposed_update_json,created_at,updated_at,applied_at
        ) VALUES ('proposal-b','user-a','key-b','applied',2,'clarification-b','g','{}','{}','created','updated','applied')""")
        assert connection.execute(
            "SELECT state, applied_at FROM candidate_adviser_profile_proposals WHERE id='proposal-b'"
        ).fetchone() == ("applied", "applied")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

"""Allow durable direct-applied Adviser proposals in existing SQLite databases."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url


def upgrade(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='candidate_adviser_profile_proposals'"
        ).fetchone()
        if table is None:
            raise RuntimeError("candidate_adviser_profile_proposals does not exist")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(candidate_adviser_profile_proposals)")}
        schema = table[0] or ""
        if "applied_at" in columns and "'applied'" in schema:
            return
        applied_column = "applied_at" if "applied_at" in columns else "NULL AS applied_at"
        connection.execute("""CREATE TABLE candidate_adviser_profile_proposals_issue275 (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            proposal_key VARCHAR(64) NOT NULL,
            state VARCHAR(16) NOT NULL CHECK (state IN ('pending', 'rejected', 'transferred', 'applied')),
            revision INTEGER NOT NULL,
            source_clarification_id VARCHAR(64) NOT NULL,
            source_assessment_fingerprint VARCHAR(64) NOT NULL,
            original_update_json TEXT NOT NULL,
            proposed_update_json TEXT NOT NULL,
            overlap_resolution_json TEXT NULL,
            transferred_profile_revision_id VARCHAR(36) REFERENCES candidate_profile_revisions(id),
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            rejected_at DATETIME NULL,
            transferred_at DATETIME NULL,
            applied_at DATETIME NULL,
            CONSTRAINT uq_candidate_adviser_profile_proposals_user_key UNIQUE (user_id, proposal_key)
        )""")
        connection.execute(f"""INSERT INTO candidate_adviser_profile_proposals_issue275 (
            id, user_id, proposal_key, state, revision, source_clarification_id,
            source_assessment_fingerprint, original_update_json, proposed_update_json,
            overlap_resolution_json, transferred_profile_revision_id, created_at,
            updated_at, rejected_at, transferred_at, applied_at
        ) SELECT id, user_id, proposal_key, state, revision, source_clarification_id,
            source_assessment_fingerprint, original_update_json, proposed_update_json,
            overlap_resolution_json, transferred_profile_revision_id, created_at,
            updated_at, rejected_at, transferred_at, {applied_column}
          FROM candidate_adviser_profile_proposals""")
        connection.execute("DROP TABLE candidate_adviser_profile_proposals")
        connection.execute("ALTER TABLE candidate_adviser_profile_proposals_issue275 RENAME TO candidate_adviser_profile_proposals")
        connection.execute("CREATE INDEX ix_candidate_adviser_profile_proposals_user_id ON candidate_adviser_profile_proposals(user_id)")
        connection.execute("CREATE INDEX ix_candidate_adviser_profile_proposals_source_clarification_id ON candidate_adviser_profile_proposals(source_clarification_id)")
        connection.commit()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign-key violations after Issue #275 migration: {violations}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database_url", help="SQLite SQLAlchemy database URL")
    args = parser.parse_args()
    url = make_url(args.database_url)
    if url.get_backend_name() != "sqlite" or url.database in {None, ":memory:"}:
        parser.error("provide a file-backed SQLite database URL")
    database = Path(url.database)
    if not database.is_absolute():
        database = Path.cwd() / database
    connection = sqlite3.connect(database)
    try:
        upgrade(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()

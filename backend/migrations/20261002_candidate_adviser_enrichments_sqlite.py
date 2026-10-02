"""Create durable Adviser enrichment state and conservatively backfill SQLite."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url


def upgrade(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS candidate_adviser_enrichments (
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            clarification_id VARCHAR(64) NOT NULL,
            source_assessment_fingerprint VARCHAR(64) NOT NULL,
            state VARCHAR(24) NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'reviewed_no_update', 'proposals_created', 'deferred')),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, clarification_id)
        )"""
    )
    clarification_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(candidate_adviser_clarifications)")
    }
    proposal_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_adviser_profile_proposals'"
    ).fetchone()
    if not clarification_columns:
        raise RuntimeError("candidate_adviser_clarifications does not exist")

    rows = connection.execute(
        "SELECT user_id, clarification_id, origin_assessment_fingerprint, interpretation_json "
        "FROM candidate_adviser_clarifications WHERE status = 'confirmed' "
        "AND interpretation_json IS NOT NULL ORDER BY confirmed_at, created_at, clarification_id"
    ).fetchall()
    for user_id, clarification_id, fingerprint, raw_interpretation in rows:
        try:
            interpretation = json.loads(raw_interpretation)
        except (TypeError, json.JSONDecodeError):
            continue
        if interpretation.get("answer_kind") not in {"career_fact", "mixed"}:
            continue
        has_proposals = False
        if proposal_table:
            has_proposals = connection.execute(
                "SELECT 1 FROM candidate_adviser_profile_proposals "
                "WHERE user_id = ? AND source_clarification_id = ? LIMIT 1",
                (user_id, clarification_id),
            ).fetchone() is not None
        connection.execute(
            "INSERT OR IGNORE INTO candidate_adviser_enrichments "
            "(user_id, clarification_id, source_assessment_fingerprint, state) VALUES (?, ?, ?, ?)",
            (user_id, clarification_id, fingerprint, "proposals_created" if has_proposals else "pending"),
        )
    connection.commit()


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

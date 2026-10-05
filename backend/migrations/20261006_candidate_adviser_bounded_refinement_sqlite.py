"""Add durable two-round Career Adviser refinement state to SQLite."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url


def upgrade(connection: sqlite3.Connection) -> None:
    assessment_columns = {row[1] for row in connection.execute("PRAGMA table_info(candidate_adviser_assessments)")}
    clarification_columns = {row[1] for row in connection.execute("PRAGMA table_info(candidate_adviser_clarifications)")}
    if not assessment_columns or not clarification_columns:
        raise RuntimeError("Candidate Adviser assessment and clarification tables must exist")
    if "contract_version" not in assessment_columns:
        connection.execute("ALTER TABLE candidate_adviser_assessments ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'legacy_questions'")
    if "parent_area_id" not in clarification_columns:
        connection.execute("ALTER TABLE candidate_adviser_clarifications ADD COLUMN parent_area_id TEXT NULL")
    if "round_number" not in clarification_columns:
        connection.execute("ALTER TABLE candidate_adviser_clarifications ADD COLUMN round_number INTEGER NULL")
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS candidate_adviser_refinement_journeys (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        journey_key VARCHAR(36) NOT NULL,
        round_number INTEGER NOT NULL CHECK (round_number IN (1, 2)),
        rounds_completed INTEGER NOT NULL CHECK (rounds_completed BETWEEN 0 AND 2),
        state VARCHAR(40) NOT NULL,
        origin_context_fingerprint VARCHAR(64) NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        CONSTRAINT uq_candidate_adviser_refinement_journey_key UNIQUE (user_id, journey_key)
    );
    CREATE INDEX IF NOT EXISTS ix_candidate_adviser_refinement_journeys_user_id ON candidate_adviser_refinement_journeys(user_id);
    CREATE TABLE IF NOT EXISTS candidate_adviser_clarification_areas (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        journey_key VARCHAR(36) NOT NULL,
        round_number INTEGER NOT NULL CHECK (round_number IN (1, 2)),
        area_key VARCHAR(80) NOT NULL,
        title VARCHAR(160) NOT NULL,
        rationale TEXT NOT NULL,
        priority_index INTEGER NOT NULL,
        source_references_json TEXT NOT NULL,
        selection_state VARCHAR(16) NOT NULL CHECK (selection_state IN ('proposed', 'selected', 'skipped')),
        origin_assessment_fingerprint VARCHAR(64) NOT NULL,
        created_at TIMESTAMP NOT NULL,
        CONSTRAINT uq_candidate_adviser_area_journey_round_key UNIQUE (journey_key, round_number, area_key)
    );
    CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarification_areas_user_id ON candidate_adviser_clarification_areas(user_id);
    CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarification_areas_journey_key ON candidate_adviser_clarification_areas(journey_key);
    CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarification_areas_origin_assessment_fingerprint ON candidate_adviser_clarification_areas(origin_assessment_fingerprint);
    CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarifications_parent_area_id ON candidate_adviser_clarifications(parent_area_id);
    """)
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

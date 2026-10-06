"""Add durable two-round Career Adviser refinement state to SQLite."""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url


_ASSESSMENTS = "candidate_adviser_assessments"
_CLARIFICATIONS = "candidate_adviser_clarifications"
_SAVEPOINT = "candidate_adviser_bounded_refinement"


def upgrade(connection: sqlite3.Connection) -> None:
    """Apply the bounded-refinement schema atomically and safely on reruns.

    The initial #280 migration used SQLite ``TEXT`` declarations for two ORM
    ``String`` columns, and made ``contract_version`` non-nullable. SQLite
    cannot alter those declarations in place, so already-upgraded databases
    need a row-preserving table reconstruction.
    """
    assessment_columns = _column_info(connection, _ASSESSMENTS)
    clarification_columns = _column_info(connection, _CLARIFICATIONS)
    if not assessment_columns or not clarification_columns:
        raise RuntimeError("Candidate Adviser assessment and clarification tables must exist")

    connection.execute(f'SAVEPOINT "{_SAVEPOINT}"')
    try:
        _ensure_column(
            connection,
            _ASSESSMENTS,
            "contract_version",
            declared_type="VARCHAR(32)",
            nullable=True,
            add_definition="VARCHAR(32) NULL DEFAULT 'legacy_questions'",
            broken_definition=r"TEXT\s+NOT\s+NULL\s+DEFAULT\s+'legacy_questions'",
            repaired_definition="VARCHAR(32) NULL DEFAULT 'legacy_questions'",
        )
        _ensure_column(
            connection,
            _CLARIFICATIONS,
            "parent_area_id",
            declared_type="VARCHAR(36)",
            nullable=True,
            add_definition="VARCHAR(36) NULL",
            broken_definition=r"TEXT(?:\s+NULL)?",
            repaired_definition="VARCHAR(36) NULL",
        )
        clarification_columns = _column_info(connection, _CLARIFICATIONS)
        if "round_number" not in clarification_columns:
            connection.execute(
                f'ALTER TABLE "{_CLARIFICATIONS}" ADD COLUMN round_number INTEGER NULL'
            )

        for statement in _REFINEMENT_DDL:
            connection.execute(statement)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS "
            "ix_candidate_adviser_clarifications_parent_area_id "
            "ON candidate_adviser_clarifications(parent_area_id)"
        )
        connection.execute(f'RELEASE SAVEPOINT "{_SAVEPOINT}"')
    except Exception:
        connection.execute(f'ROLLBACK TO SAVEPOINT "{_SAVEPOINT}"')
        connection.execute(f'RELEASE SAVEPOINT "{_SAVEPOINT}"')
        raise


def _column_info(connection: sqlite3.Connection, table: str) -> dict[str, tuple]:
    if not _table_exists(connection, table):
        return {}
    return {row[1]: row for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    *,
    declared_type: str,
    nullable: bool,
    add_definition: str,
    broken_definition: str,
    repaired_definition: str,
) -> None:
    columns = _column_info(connection, table)
    column_info = columns.get(column)
    if column_info is None:
        connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {add_definition}')
        return

    actual_type = "".join(str(column_info[2]).upper().split())
    actual_nullable = not bool(column_info[3])
    expected_type = "".join(declared_type.upper().split())
    if actual_type == expected_type and actual_nullable is nullable:
        return

    _rebuild_table_column(
        connection,
        table,
        column,
        broken_definition=broken_definition,
        repaired_definition=repaired_definition,
    )


def _rebuild_table_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    *,
    broken_definition: str,
    repaired_definition: str,
) -> None:
    create_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if create_sql_row is None or not create_sql_row[0]:
        raise RuntimeError(f"Cannot safely reconstruct {table}: CREATE TABLE SQL is unavailable")
    create_sql = create_sql_row[0]

    replacement = re.compile(
        rf'(?P<name>["`\[]?{re.escape(column)}["`\]]?)\s+'
        rf"(?:{broken_definition})(?=\s*[,\)])",
        re.IGNORECASE,
    )
    repaired_sql, replacements = replacement.subn(
        lambda match: f"{match.group('name')} {repaired_definition}",
        create_sql,
        count=1,
    )
    if replacements != 1:
        raise RuntimeError(
            f"Cannot safely reconstruct {table}.{column}: unrecognized physical declaration"
        )

    _reject_inbound_foreign_keys(connection, table)
    objects = connection.execute(
        "SELECT type, sql FROM sqlite_master "
        "WHERE tbl_name=? AND type IN ('index', 'trigger') AND sql IS NOT NULL "
        "ORDER BY type, name",
        (table,),
    ).fetchall()
    temp_table = f"_migration_rebuilt_{table}"
    if _table_exists(connection, temp_table):
        raise RuntimeError(f"Cannot safely reconstruct {table}: temporary table already exists")

    column_names = list(_column_info(connection, table))
    copy_columns = ", ".join(_quote_identifier(name) for name in column_names)
    temp_sql = re.sub(
        rf"(?i)(^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)[\"`\[]?{re.escape(table)}[\"`\]]?(?=\s*\()",
        lambda match: f"{match.group(1)}{_quote_identifier(temp_table)}",
        repaired_sql,
        count=1,
    )
    if temp_sql == repaired_sql:
        raise RuntimeError(f"Cannot safely reconstruct {table}: table declaration is unrecognized")

    connection.execute(temp_sql)
    connection.execute(
        f"INSERT INTO {_quote_identifier(temp_table)} ({copy_columns}) "
        f"SELECT {copy_columns} FROM {_quote_identifier(table)}"
    )
    connection.execute(f"DROP TABLE {_quote_identifier(table)}")
    connection.execute(
        f"ALTER TABLE {_quote_identifier(temp_table)} RENAME TO {_quote_identifier(table)}"
    )
    for _object_type, sql in objects:
        connection.execute(sql)


def _reject_inbound_foreign_keys(connection: sqlite3.Connection, table: str) -> None:
    for (other_table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        if other_table == table:
            continue
        if any(
            row[2] == table
            for row in connection.execute(f"PRAGMA foreign_key_list({_quote_identifier(other_table)})")
        ):
            raise RuntimeError(
                f"Cannot safely reconstruct {table}: {other_table} has an inbound foreign key"
            )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


_REFINEMENT_DDL = (
    """CREATE TABLE IF NOT EXISTS candidate_adviser_refinement_journeys (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        journey_key VARCHAR(36) NOT NULL,
        round_number INTEGER NOT NULL CHECK (round_number IN (1, 2)),
        rounds_completed INTEGER NOT NULL CHECK (rounds_completed BETWEEN 0 AND 2),
        state VARCHAR(40) NOT NULL,
        origin_context_fingerprint VARCHAR(64) NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        CONSTRAINT uq_candidate_adviser_refinement_journey_key UNIQUE (user_id, journey_key)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_candidate_adviser_refinement_journeys_user_id ON candidate_adviser_refinement_journeys(user_id)",
    """CREATE TABLE IF NOT EXISTS candidate_adviser_clarification_areas (
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
    )""",
    "CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarification_areas_user_id ON candidate_adviser_clarification_areas(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarification_areas_journey_key ON candidate_adviser_clarification_areas(journey_key)",
    "CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarification_areas_origin_assessment_fingerprint ON candidate_adviser_clarification_areas(origin_assessment_fingerprint)",
)


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

"""Add nullable structured-answer columns to retained SQLite databases."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url


def upgrade(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute(
        "PRAGMA table_info(candidate_adviser_clarifications)"
    )}
    if not columns:
        raise RuntimeError("candidate_adviser_clarifications does not exist")
    for name in ("suggested_answers_json", "structured_response_json"):
        if name not in columns:
            connection.execute(
                f"ALTER TABLE candidate_adviser_clarifications ADD COLUMN {name} TEXT NULL"
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

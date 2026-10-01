"""Add a durable idempotency key to discovery runs in existing SQLite databases."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url


def upgrade(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(discovery_runs)")}
    if not columns:
        raise RuntimeError("discovery_runs does not exist")
    if "client_request_id" not in columns:
        connection.execute("ALTER TABLE discovery_runs ADD COLUMN client_request_id VARCHAR(36)")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_run_client_request "
        "ON discovery_runs(user_id, client_request_id)"
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

"""Safely add the Local Codex provider to existing SQLite settings databases."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url


def upgrade(connection: sqlite3.Connection) -> None:
    """Rebuild the constrained table while preserving every persisted setting."""
    foreign_keys_were_enabled = bool(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_job_discovery_settings'"
        ).fetchone()
        if not exists:
            raise RuntimeError("user_job_discovery_settings does not exist")
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_job_discovery_settings'"
        ).fetchone()[0]
        if "local_codex" not in sql:
            connection.execute(
                """CREATE TABLE user_job_discovery_settings__local_codex (
                    user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    provider_override VARCHAR(24) NULL CHECK (
                        provider_override IS NULL OR provider_override IN ('tavily', 'openai', 'local_codex', 'disabled')
                    ),
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """INSERT INTO user_job_discovery_settings__local_codex
                   (user_id, provider_override, revision, created_at, updated_at)
                   SELECT user_id, provider_override, revision, created_at, updated_at
                   FROM user_job_discovery_settings"""
            )
            connection.execute("DROP TABLE user_job_discovery_settings")
            connection.execute(
                "ALTER TABLE user_job_discovery_settings__local_codex RENAME TO user_job_discovery_settings"
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("foreign-key validation failed after Local Codex settings migration")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys_were_enabled else 'OFF'}")


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

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


SQLITE_MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "20261002_discovery_run_request_id_sqlite.py"


def test_sqlite_upgrade_preserves_existing_runs_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "existing-career-trans.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE discovery_runs ("
            "id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL, "
            "search_input_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO discovery_runs (id, user_id, search_input_json) VALUES (?, ?, ?)",
            ("legacy-run", "user-a", '{"query":{"keywords":["legacy"]}}'),
        )

    database_url = f"sqlite:///{database_path.as_posix()}"
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, str(SQLITE_MIGRATION), database_url],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    with sqlite3.connect(database_path) as connection:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(discovery_runs)")}
        indexes = {row[1]: row for row in connection.execute("PRAGMA index_list(discovery_runs)")}
        assert columns["client_request_id"][3] == 0  # nullable
        assert indexes["uq_discovery_run_client_request"][2] == 1  # unique
        assert connection.execute(
            "SELECT id, user_id, search_input_json, client_request_id FROM discovery_runs"
        ).fetchone() == ("legacy-run", "user-a", '{"query":{"keywords":["legacy"]}}', None)
        connection.execute(
            "INSERT INTO discovery_runs (id, user_id, search_input_json, client_request_id) VALUES (?, ?, ?, ?)",
            ("new-run", "user-a", "{}", "request-a"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO discovery_runs (id, user_id, search_input_json, client_request_id) VALUES (?, ?, ?, ?)",
                ("duplicate-run", "user-a", "{}", "request-a"),
            )

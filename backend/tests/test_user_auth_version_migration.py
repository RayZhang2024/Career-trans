import importlib.util
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import Base
from app.core.security import hash_password, verify_password
from app.models.user import User


MIGRATION_PATH = Path(__file__).parents[1] / "migrations" / "20261014_user_auth_version_sqlite.py"
SPEC = importlib.util.spec_from_file_location("user_auth_version_sqlite", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_migration_adds_generation_to_existing_users_without_changing_rows_or_other_data(tmp_path: Path) -> None:
    database = tmp_path / "before-auth-generation.sqlite3"
    raw = sqlite3.connect(database)
    raw.executescript(
        """
        CREATE TABLE users (
            id VARCHAR(36) PRIMARY KEY,
            email VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        CREATE TABLE retained_profile_data (id INTEGER PRIMARY KEY, user_id VARCHAR(36), profile_text TEXT);
        INSERT INTO retained_profile_data VALUES (7, 'u-1', 'profile remains unchanged');
        """
    )
    first_hash = hash_password("old-pass")
    second_hash = hash_password("legacy8x")
    raw.executemany(
        "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
        [
            ("u-1", "old@example.test", first_hash, "2024-01-02", "2024-03-04"),
            ("u-2", "short-password@example.test", second_hash, "2025-01-02", "2025-03-04"),
        ],
    )
    raw.commit()
    original_users = raw.execute("SELECT id, email, password_hash, created_at, updated_at FROM users ORDER BY id").fetchall()
    raw.close()
    url = f"sqlite:///{database.as_posix()}"

    MIGRATION.upgrade(url)
    MIGRATION.upgrade(url)

    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id, email, password_hash, created_at, updated_at FROM users ORDER BY id")).fetchall() == original_users
        assert connection.execute(text("SELECT id, auth_version FROM users ORDER BY id")).fetchall() == [("u-1", 0), ("u-2", 0)]
        assert connection.execute(text("SELECT id, user_id, profile_text FROM retained_profile_data")).fetchall() == [
            (7, "u-1", "profile remains unchanged")
        ]
        columns = {column["name"]: column for column in inspect(connection).get_columns("users")}
        assert columns["auth_version"]["nullable"] is False
        assert str(columns["auth_version"]["type"]).upper() == "INTEGER"
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE users SET auth_version = -1 WHERE id = 'u-1'"))
        connection.rollback()
    engine.dispose()
    assert verify_password("old-pass", first_hash)
    assert verify_password("legacy8x", second_hash)


def test_fresh_orm_schema_includes_nonnegative_generation(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        user = User(id=str(uuid4()), email="new@example.test", password_hash="hash")
        connection.execute(
            text("INSERT INTO users (id, email, password_hash, created_at, updated_at) VALUES (:id, :email, :password_hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"id": user.id, "email": user.email, "password_hash": user.password_hash},
        )
        assert connection.execute(text("SELECT auth_version FROM users WHERE id = :id"), {"id": user.id}).scalar_one() == 0
    engine.dispose()

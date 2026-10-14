"""Add the shared auth generation to existing SQLite user tables."""

import sys

from sqlalchemy import create_engine, inspect, text


def upgrade(url: str) -> None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                inspector = inspect(connection)
                if not inspector.has_table("users"):
                    raise RuntimeError("The users table must exist before applying this migration.")
                columns = {column["name"] for column in inspector.get_columns("users")}
                if "auth_version" not in columns:
                    connection.execute(
                        text(
                            "ALTER TABLE users ADD COLUMN auth_version INTEGER "
                            "NOT NULL DEFAULT 0 CHECK (auth_version >= 0)"
                        )
                    )
                else:
                    invalid = connection.execute(
                        text("SELECT 1 FROM users WHERE auth_version IS NULL OR auth_version < 0 LIMIT 1")
                    ).first()
                    if invalid is not None:
                        raise RuntimeError("Existing auth_version values must be nonnegative integers.")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    finally:
        engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python 20261014_user_auth_version_sqlite.py <SQLAlchemy SQLite URL>")
    upgrade(sys.argv[1])

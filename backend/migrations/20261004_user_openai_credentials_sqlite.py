"""Create the encrypted per-user OpenAI credential table for SQLite."""

import sys

from sqlalchemy import create_engine, text


def upgrade(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS user_openai_credentials (
                user_id VARCHAR(36) NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                nonce BLOB NOT NULL CHECK (length(nonce) = 12),
                ciphertext BLOB NOT NULL,
                format_version INTEGER NOT NULL CHECK (format_version = 1),
                revision INTEGER NOT NULL CHECK (revision >= 1),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python 20261004_user_openai_credentials_sqlite.py <SQLAlchemy database URL>")
    upgrade(sys.argv[1])

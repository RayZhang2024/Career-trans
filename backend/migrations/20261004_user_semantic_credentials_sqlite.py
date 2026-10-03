"""Create provider-neutral encrypted user semantic credentials for SQLite."""

import sys

from sqlalchemy import create_engine, text


def upgrade(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS user_semantic_credentials (
                user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider VARCHAR(32) NOT NULL CHECK (provider IN ('openai')),
                nonce BLOB NOT NULL CHECK (length(nonce) = 12),
                ciphertext BLOB NOT NULL,
                format_version INTEGER NOT NULL CHECK (format_version = 1),
                revision INTEGER NOT NULL CHECK (revision >= 1),
                display_suffix VARCHAR(4) NULL CHECK (display_suffix IS NULL OR length(display_suffix) = 4),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, provider)
            )
        """))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python 20261004_user_semantic_credentials_sqlite.py <SQLAlchemy database URL>")
    upgrade(sys.argv[1])

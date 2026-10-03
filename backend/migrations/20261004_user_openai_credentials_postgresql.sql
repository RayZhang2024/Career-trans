-- Additive encrypted per-user OpenAI semantic credentials.
CREATE TABLE IF NOT EXISTS user_openai_credentials (
    user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    nonce BYTEA NOT NULL CHECK (octet_length(nonce) = 12),
    ciphertext BYTEA NOT NULL,
    format_version INTEGER NOT NULL CHECK (format_version = 1),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

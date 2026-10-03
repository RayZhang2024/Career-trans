-- Additive provider-neutral encrypted per-user semantic credentials.
CREATE TABLE IF NOT EXISTS user_semantic_credentials (
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL CHECK (provider IN ('openai')),
    nonce BYTEA NOT NULL CHECK (octet_length(nonce) = 12),
    ciphertext BYTEA NOT NULL,
    format_version INTEGER NOT NULL CHECK (format_version = 1),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    display_suffix VARCHAR(4) NULL CHECK (display_suffix IS NULL OR length(display_suffix) = 4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, provider)
);

-- Issue #256: separate user-scoped browser discovery settings and encrypted Tavily credentials.
CREATE TABLE user_job_discovery_settings (
    user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    provider_override VARCHAR(24) NULL CHECK (
        provider_override IS NULL OR provider_override IN ('tavily', 'openai', 'disabled')
    ),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_tavily_credentials (
    user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    nonce BYTEA NOT NULL CHECK (length(nonce) = 12),
    ciphertext BYTEA NOT NULL,
    format_version INTEGER NOT NULL CHECK (format_version = 1),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE scheduled_discovery_executions
    ADD COLUMN web_search_metadata_json TEXT NOT NULL DEFAULT '{}';

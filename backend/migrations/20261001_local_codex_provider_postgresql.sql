-- Add Local Codex while preserving existing user provider overrides.
ALTER TABLE user_job_discovery_settings
    DROP CONSTRAINT IF EXISTS user_job_discovery_settings_provider_override_check;
ALTER TABLE user_job_discovery_settings
    DROP CONSTRAINT IF EXISTS ck_user_job_discovery_provider_override;
ALTER TABLE user_job_discovery_settings
    ADD CONSTRAINT ck_user_job_discovery_provider_override
    CHECK (provider_override IS NULL OR provider_override IN ('tavily', 'openai', 'local_codex', 'disabled'));

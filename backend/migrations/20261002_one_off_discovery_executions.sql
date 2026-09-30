CREATE TABLE IF NOT EXISTS one_off_discovery_executions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_request_id VARCHAR(36) NOT NULL,
    request_fingerprint VARCHAR(64) NOT NULL,
    query_snapshot_json TEXT NOT NULL,
    policy_snapshot_json TEXT NOT NULL,
    provider_metadata_json TEXT NOT NULL DEFAULT '{}',
    status VARCHAR(24) NOT NULL DEFAULT 'running',
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP NULL,
    acquisition_summary_json TEXT NOT NULL DEFAULT '{}',
    failure_summary_json TEXT NOT NULL DEFAULT '{}',
    discovery_run_id VARCHAR(36) NULL REFERENCES discovery_runs(id) ON DELETE SET NULL,
    CONSTRAINT uq_one_off_discovery_request UNIQUE (user_id, client_request_id)
);
CREATE INDEX IF NOT EXISTS ix_one_off_discovery_executions_user_id ON one_off_discovery_executions(user_id);
CREATE INDEX IF NOT EXISTS ix_one_off_discovery_executions_status ON one_off_discovery_executions(status);

ALTER TABLE discovery_runs ADD COLUMN IF NOT EXISTS client_request_id VARCHAR(36) NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_run_client_request ON discovery_runs(user_id, client_request_id);

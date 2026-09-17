CREATE TABLE IF NOT EXISTS discovery_schedules (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT 1,
    schedule_spec_json TEXT NOT NULL,
    query_json TEXT NOT NULL,
    acquisition_config_json TEXT NOT NULL,
    evaluation_config_json TEXT NOT NULL,
    next_run_at DATETIME NULL,
    last_execution_at DATETIME NULL,
    active_execution_id VARCHAR(36) NULL UNIQUE,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_discovery_schedules_user_id ON discovery_schedules(user_id);
CREATE INDEX IF NOT EXISTS ix_discovery_schedules_next_run_at ON discovery_schedules(next_run_at);

CREATE TABLE IF NOT EXISTS scheduled_discovery_executions (
    id VARCHAR(36) PRIMARY KEY,
    schedule_id VARCHAR(36) NOT NULL REFERENCES discovery_schedules(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trigger_kind VARCHAR(16) NOT NULL,
    scheduled_for DATETIME NULL,
    config_snapshot_json TEXT NOT NULL,
    status VARCHAR(24) NOT NULL,
    started_at DATETIME NOT NULL,
    completed_at DATETIME NULL,
    discovery_run_id VARCHAR(36) NULL REFERENCES discovery_runs(id) ON DELETE SET NULL,
    acquisition_summary_json TEXT NOT NULL DEFAULT '{}',
    failure_summary_json TEXT NOT NULL DEFAULT '{}',
    CONSTRAINT uq_scheduled_discovery_slot UNIQUE (schedule_id, scheduled_for)
);
CREATE INDEX IF NOT EXISTS ix_scheduled_discovery_executions_schedule_id ON scheduled_discovery_executions(schedule_id);
CREATE INDEX IF NOT EXISTS ix_scheduled_discovery_executions_user_id ON scheduled_discovery_executions(user_id);
CREATE INDEX IF NOT EXISTS ix_scheduled_discovery_executions_status ON scheduled_discovery_executions(status);

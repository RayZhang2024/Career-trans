-- Issue #154: additive, user-owned run/evaluation history.
CREATE TABLE IF NOT EXISTS discovery_runs (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    search_input_json TEXT NOT NULL,
    search_input_fingerprint VARCHAR(64) NOT NULL,
    candidate_evaluation_fingerprint VARCHAR(64) NOT NULL,
    evaluation_contract_fingerprint VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    funnel_json TEXT NOT NULL DEFAULT '{}',
    failure_summary_json TEXT NOT NULL DEFAULT '{}',
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_discovery_runs_user_id ON discovery_runs(user_id);
CREATE INDEX IF NOT EXISTS ix_discovery_runs_search_input_fingerprint ON discovery_runs(search_input_fingerprint);
CREATE INDEX IF NOT EXISTS ix_discovery_runs_candidate_evaluation_fingerprint ON discovery_runs(candidate_evaluation_fingerprint);
CREATE INDEX IF NOT EXISTS ix_discovery_runs_evaluation_contract_fingerprint ON discovery_runs(evaluation_contract_fingerprint);

CREATE TABLE IF NOT EXISTS user_job_evaluations (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    discovered_job_id VARCHAR(36) NOT NULL REFERENCES discovered_jobs(id) ON DELETE CASCADE,
    job_content_hash VARCHAR(64) NOT NULL,
    candidate_evaluation_fingerprint VARCHAR(64) NOT NULL,
    evaluation_contract_fingerprint VARCHAR(64) NOT NULL,
    job_snapshot_json TEXT NOT NULL,
    evaluation_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_user_job_evaluation_identity UNIQUE (user_id, discovered_job_id, job_content_hash, candidate_evaluation_fingerprint, evaluation_contract_fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_user_job_evaluations_user_id ON user_job_evaluations(user_id);
CREATE INDEX IF NOT EXISTS ix_user_job_evaluations_discovered_job_id ON user_job_evaluations(discovered_job_id);
CREATE INDEX IF NOT EXISTS ix_user_job_evaluations_job_content_hash ON user_job_evaluations(job_content_hash);
CREATE INDEX IF NOT EXISTS ix_user_job_evaluations_candidate_evaluation_fingerprint ON user_job_evaluations(candidate_evaluation_fingerprint);
CREATE INDEX IF NOT EXISTS ix_user_job_evaluations_evaluation_contract_fingerprint ON user_job_evaluations(evaluation_contract_fingerprint);

CREATE TABLE IF NOT EXISTS discovery_run_jobs (
    id VARCHAR(36) PRIMARY KEY,
    discovery_run_id VARCHAR(36) NOT NULL REFERENCES discovery_runs(id) ON DELETE CASCADE,
    discovered_job_id VARCHAR(36) NOT NULL REFERENCES discovered_jobs(id) ON DELETE CASCADE,
    evaluation_id VARCHAR(36) REFERENCES user_job_evaluations(id) ON DELETE SET NULL,
    outcome VARCHAR(48) NOT NULL,
    failure_stage VARCHAR(64),
    failure_kind VARCHAR(128),
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_discovery_run_job UNIQUE (discovery_run_id, discovered_job_id)
);
CREATE INDEX IF NOT EXISTS ix_discovery_run_jobs_discovery_run_id ON discovery_run_jobs(discovery_run_id);
CREATE INDEX IF NOT EXISTS ix_discovery_run_jobs_discovered_job_id ON discovery_run_jobs(discovered_job_id);
CREATE INDEX IF NOT EXISTS ix_discovery_run_jobs_evaluation_id ON discovery_run_jobs(evaluation_id);

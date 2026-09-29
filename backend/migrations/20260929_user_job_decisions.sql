-- Issue #240 Phase 7: durable private user decisions for canonical jobs.
CREATE TABLE user_job_decisions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    discovered_job_id VARCHAR(36) NOT NULL REFERENCES discovered_jobs(id) ON DELETE CASCADE,
    decision VARCHAR(16) NOT NULL CHECK (decision IN ('undecided', 'shortlisted', 'dismissed')),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT uq_user_job_decision_user_job UNIQUE (user_id, discovered_job_id)
);
CREATE INDEX ix_user_job_decisions_user_id ON user_job_decisions(user_id);
CREATE INDEX ix_user_job_decisions_discovered_job_id ON user_job_decisions(discovered_job_id);
CREATE INDEX ix_user_job_decisions_decision ON user_job_decisions(decision);
CREATE INDEX ix_user_job_decisions_user_decision_updated ON user_job_decisions(user_id, decision, updated_at DESC, id ASC);

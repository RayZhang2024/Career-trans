-- Issue #132: deterministic verification/actionability state for external job leads.
ALTER TABLE discovered_jobs ADD COLUMN detail_authority VARCHAR(32) NOT NULL DEFAULT 'provider_detail';
ALTER TABLE discovered_jobs ADD COLUMN verification_status VARCHAR(16) NOT NULL DEFAULT 'verified';
ALTER TABLE discovered_jobs ADD COLUMN verification_reason VARCHAR(64);
CREATE INDEX ix_discovered_jobs_verification_status ON discovered_jobs(verification_status);

-- Legacy external summaries were not verified before persistence.
UPDATE discovered_jobs
SET detail_authority = 'external_summary',
    verification_status = 'unverified',
    verification_reason = 'legacy_external_lead'
WHERE source = 'agent_runtime';

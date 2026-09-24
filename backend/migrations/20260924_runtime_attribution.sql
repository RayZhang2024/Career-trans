-- Issue #191: privacy-safe historical semantic runtime attribution.
-- Additive only: pre-existing rows intentionally remain NULL and are projected
-- as legacy_unavailable where a historical output already exists.
ALTER TABLE candidate_cv_ingestion_drafts ADD COLUMN runtime_attribution_json TEXT NULL;
ALTER TABLE user_job_evaluations ADD COLUMN runtime_attribution_json TEXT NULL;
ALTER TABLE application_preparations ADD COLUMN runtime_attribution_json TEXT NULL;

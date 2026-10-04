-- Issue #275: direct application is a separate terminal state from legacy transfer.
BEGIN;
ALTER TABLE candidate_adviser_profile_proposals
    ADD COLUMN IF NOT EXISTS applied_at TIMESTAMP NULL;
ALTER TABLE candidate_adviser_profile_proposals
    DROP CONSTRAINT IF EXISTS ck_candidate_adviser_profile_proposals_state;
ALTER TABLE candidate_adviser_profile_proposals
    ADD CONSTRAINT ck_candidate_adviser_profile_proposals_state
    CHECK (state IN ('pending', 'rejected', 'transferred', 'applied'));
COMMIT;

-- Issue #208 Phase 1: source-linked, noncanonical Adviser Profile proposals.
-- Local development/tests register the SQLAlchemy model through Base.metadata.create_all.
CREATE TABLE candidate_adviser_profile_proposals (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    proposal_key VARCHAR(64) NOT NULL,
    state VARCHAR(16) NOT NULL,
    revision INTEGER NOT NULL,
    source_clarification_id VARCHAR(64) NOT NULL,
    source_assessment_fingerprint VARCHAR(64) NOT NULL,
    original_update_json TEXT NOT NULL,
    proposed_update_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    rejected_at TIMESTAMP,
    CONSTRAINT uq_candidate_adviser_profile_proposals_user_key UNIQUE (user_id, proposal_key),
    CONSTRAINT ck_candidate_adviser_profile_proposals_state CHECK (state IN ('pending', 'rejected'))
);
CREATE INDEX ix_candidate_adviser_profile_proposals_user_id
    ON candidate_adviser_profile_proposals(user_id);
CREATE INDEX ix_candidate_adviser_profile_proposals_source_clarification_id
    ON candidate_adviser_profile_proposals(source_clarification_id);

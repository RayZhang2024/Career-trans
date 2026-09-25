-- Issue #207 Phase 1: persisted manual Profile proposals and one-active slot.
-- Local development/tests register the SQLAlchemy model through Base.metadata.create_all.
CREATE TABLE candidate_profile_revisions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    active_user_id VARCHAR(36),
    state VARCHAR(32) NOT NULL,
    revision INTEGER NOT NULL,
    base_profile_fingerprint VARCHAR(64) NOT NULL,
    base_structured_fingerprint VARCHAR(64) NOT NULL,
    base_editable_structured_fingerprint VARCHAR(64) NOT NULL,
    proposed_profile_json TEXT,
    proposed_structured_json TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    confirmed_at TIMESTAMP,
    discarded_at TIMESTAMP,
    CONSTRAINT uq_candidate_profile_revisions_active_user UNIQUE (active_user_id),
    CONSTRAINT ck_candidate_profile_revisions_active_slot CHECK (
        (state IN ('draft', 'review_ready') AND active_user_id = user_id)
        OR (state IN ('confirmed', 'discarded') AND active_user_id IS NULL)
    )
);
CREATE INDEX ix_candidate_profile_revisions_user_id ON candidate_profile_revisions(user_id);

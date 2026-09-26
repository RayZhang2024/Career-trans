-- Issue #209 Phase 3: persisted user choices for one exact CV/base snapshot.
CREATE TABLE candidate_cv_overlap_reviews (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    draft_id VARCHAR(36) NOT NULL UNIQUE REFERENCES candidate_cv_ingestion_drafts(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    base_structured_fingerprint VARCHAR(64) NOT NULL,
    draft_fingerprint VARCHAR(64) NOT NULL,
    resolutions_json TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE INDEX ix_candidate_cv_overlap_reviews_user_id
    ON candidate_cv_overlap_reviews(user_id);
CREATE INDEX ix_candidate_cv_overlap_reviews_draft_id
    ON candidate_cv_overlap_reviews(draft_id);

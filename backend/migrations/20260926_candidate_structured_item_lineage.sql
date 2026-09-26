CREATE TABLE candidate_structured_item_lineage (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lineage_key VARCHAR(64) NOT NULL,
    section VARCHAR(32) NOT NULL CHECK (section IN ('employment', 'education', 'credentials', 'skills', 'projects', 'achievements')),
    item_fingerprint VARCHAR(64) NOT NULL,
    item_json TEXT NOT NULL,
    source_kind VARCHAR(32) NOT NULL CHECK (source_kind IN ('cv', 'manual_profile', 'candidate_adviser')),
    source_ref VARCHAR(256) NOT NULL,
    relationship VARCHAR(32) NOT NULL CHECK (relationship IN ('new', 'reinforcement', 'refinement', 'conflict', 'ambiguous')),
    predecessor_fingerprint VARCHAR(64),
    predecessor_item_json TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT uq_structured_item_lineage_user_key UNIQUE (user_id, lineage_key)
);

CREATE INDEX ix_candidate_structured_item_lineage_user_id
    ON candidate_structured_item_lineage(user_id);
CREATE INDEX ix_candidate_structured_item_lineage_item_fingerprint
    ON candidate_structured_item_lineage(item_fingerprint);
CREATE INDEX ix_structured_item_lineage_user_item
    ON candidate_structured_item_lineage(user_id, section, item_fingerprint);

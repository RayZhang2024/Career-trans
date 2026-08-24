-- Issue #40 deployment migration. Local development/tests continue to register
-- these SQLAlchemy models through Base.metadata.create_all.
CREATE TABLE candidate_cv_ingestion_drafts (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state VARCHAR(32) NOT NULL,
    documents_json TEXT NOT NULL,
    merged_json TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX ix_candidate_cv_ingestion_drafts_user_id ON candidate_cv_ingestion_drafts(user_id);

CREATE TABLE candidate_structured_profiles (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    structured_json TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE candidate_evidence (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fingerprint VARCHAR(64) NOT NULL,
    evidence_type VARCHAR(32) NOT NULL,
    title VARCHAR(500) NOT NULL,
    text TEXT NOT NULL,
    skills_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_candidate_evidence_user_fingerprint UNIQUE (user_id, fingerprint)
);
CREATE INDEX ix_candidate_evidence_user_id ON candidate_evidence(user_id);
CREATE INDEX ix_candidate_evidence_fingerprint ON candidate_evidence(fingerprint);

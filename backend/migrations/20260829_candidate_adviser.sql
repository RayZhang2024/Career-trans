-- Issue #129 deployment migration. Local development/tests register these
-- SQLAlchemy models through Base.metadata.create_all.
CREATE TABLE candidate_adviser_intakes (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    intake_json TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX ix_candidate_adviser_intakes_user_id ON candidate_adviser_intakes(user_id);

CREATE TABLE candidate_adviser_assessments (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    input_fingerprint VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    assessment_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX ix_candidate_adviser_assessments_user_id ON candidate_adviser_assessments(user_id);
CREATE INDEX ix_candidate_adviser_assessments_input_fingerprint ON candidate_adviser_assessments(input_fingerprint);

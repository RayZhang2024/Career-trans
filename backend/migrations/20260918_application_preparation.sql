-- Issue #21: application identity/contact fields and immutable preparation history.
ALTER TABLE candidate_profiles ADD COLUMN display_name VARCHAR(200);
ALTER TABLE candidate_profiles ADD COLUMN preferred_email VARCHAR(320);
ALTER TABLE candidate_profiles ADD COLUMN phone VARCHAR(100);
ALTER TABLE candidate_profiles ADD COLUMN linkedin_url VARCHAR(500);
ALTER TABLE candidate_profiles ADD COLUMN github_url VARCHAR(500);
ALTER TABLE candidate_profiles ADD COLUMN portfolio_url VARCHAR(500);

CREATE TABLE application_preparations (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_snapshot_json TEXT NOT NULL,
    identity_snapshot_json TEXT NOT NULL,
    preparation_input_fingerprint VARCHAR(64) NOT NULL,
    preparation_contract_fingerprint VARCHAR(64) NOT NULL,
    preparation_result_json TEXT NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE INDEX ix_application_preparations_user_created
    ON application_preparations(user_id, created_at);

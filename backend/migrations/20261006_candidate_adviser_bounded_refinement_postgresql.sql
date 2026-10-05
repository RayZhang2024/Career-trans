-- Issue #280: versioned assessments and durable bounded refinement journeys.
ALTER TABLE candidate_adviser_assessments
    ADD COLUMN IF NOT EXISTS contract_version VARCHAR(32) NOT NULL DEFAULT 'legacy_questions';
ALTER TABLE candidate_adviser_clarifications
    ADD COLUMN IF NOT EXISTS parent_area_id VARCHAR(36) NULL;
ALTER TABLE candidate_adviser_clarifications
    ADD COLUMN IF NOT EXISTS round_number INTEGER NULL;
CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarifications_parent_area_id
    ON candidate_adviser_clarifications(parent_area_id);

CREATE TABLE IF NOT EXISTS candidate_adviser_refinement_journeys (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    journey_key VARCHAR(36) NOT NULL,
    round_number INTEGER NOT NULL CHECK (round_number IN (1, 2)),
    rounds_completed INTEGER NOT NULL CHECK (rounds_completed BETWEEN 0 AND 2),
    state VARCHAR(40) NOT NULL,
    origin_context_fingerprint VARCHAR(64) NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_candidate_adviser_refinement_journey_key UNIQUE (user_id, journey_key)
);
CREATE INDEX IF NOT EXISTS ix_candidate_adviser_refinement_journeys_user_id
    ON candidate_adviser_refinement_journeys(user_id);

CREATE TABLE IF NOT EXISTS candidate_adviser_clarification_areas (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    journey_key VARCHAR(36) NOT NULL,
    round_number INTEGER NOT NULL CHECK (round_number IN (1, 2)),
    area_key VARCHAR(80) NOT NULL,
    title VARCHAR(160) NOT NULL,
    rationale TEXT NOT NULL,
    priority_index INTEGER NOT NULL,
    source_references_json TEXT NOT NULL,
    selection_state VARCHAR(16) NOT NULL CHECK (selection_state IN ('proposed', 'selected', 'skipped')),
    origin_assessment_fingerprint VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_candidate_adviser_area_journey_round_key UNIQUE (journey_key, round_number, area_key)
);
CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarification_areas_user_id
    ON candidate_adviser_clarification_areas(user_id);
CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarification_areas_journey_key
    ON candidate_adviser_clarification_areas(journey_key);
CREATE INDEX IF NOT EXISTS ix_candidate_adviser_clarification_areas_origin_assessment_fingerprint
    ON candidate_adviser_clarification_areas(origin_assessment_fingerprint);

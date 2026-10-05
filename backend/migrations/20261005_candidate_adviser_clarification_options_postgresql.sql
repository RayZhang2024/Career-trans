-- Issue #276: materialized selectable clarification options and structured answer authority.
ALTER TABLE candidate_adviser_clarifications
    ADD COLUMN IF NOT EXISTS suggested_answers_json TEXT NULL;
ALTER TABLE candidate_adviser_clarifications
    ADD COLUMN IF NOT EXISTS structured_response_json TEXT NULL;

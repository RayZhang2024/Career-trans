-- Issue #266: persist resolution of optional Profile enrichment per confirmed clarification.
CREATE TABLE IF NOT EXISTS candidate_adviser_enrichments (
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    clarification_id VARCHAR(64) NOT NULL,
    source_assessment_fingerprint VARCHAR(64) NOT NULL,
    state VARCHAR(24) NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'reviewed_no_update', 'proposals_created', 'deferred')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, clarification_id)
);

-- Existing proposal rows are authoritative positive history. In the absence of
-- a proposal row, backfill as pending; never infer a historical no-update.
INSERT INTO candidate_adviser_enrichments (
    user_id, clarification_id, source_assessment_fingerprint, state
)
SELECT clarification.user_id,
       clarification.clarification_id,
       clarification.origin_assessment_fingerprint,
       CASE WHEN EXISTS (
           SELECT 1
           FROM candidate_adviser_profile_proposals AS proposal
           WHERE proposal.user_id = clarification.user_id
             AND proposal.source_clarification_id = clarification.clarification_id
       ) THEN 'proposals_created' ELSE 'pending' END
FROM candidate_adviser_clarifications AS clarification
WHERE clarification.status = 'confirmed'
  AND clarification.interpretation_json IS NOT NULL
  AND clarification.interpretation_json::jsonb->>'answer_kind' IN ('career_fact', 'mixed')
ON CONFLICT (user_id, clarification_id) DO NOTHING;

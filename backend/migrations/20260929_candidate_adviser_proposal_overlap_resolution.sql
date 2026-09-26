-- Issue #209 Phase 4A: persisted human decision for an ambiguous Adviser add.
ALTER TABLE candidate_adviser_profile_proposals
    ADD COLUMN overlap_resolution_json TEXT NULL;

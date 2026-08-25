-- Issue #71 deployment migration. Local development/tests register this
-- nullable SQLAlchemy column through Base.metadata.create_all.
ALTER TABLE candidate_profiles ADD COLUMN job_search_criteria TEXT;

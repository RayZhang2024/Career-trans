# Candidate Legacy Compatibility Dry Run

This note describes the read-only Phase 1 compatibility inventory for candidate data. It does not change the point-in-time contents of `CANONICAL_CANDIDATE_PROFILE_AUDIT.md`.

## Why databases can differ

The application currently calls SQLAlchemy `Base.metadata.create_all()` during startup. That can create a table that is wholly absent, but it does not add new columns to an existing table. Candidate schema history includes these additive changes:

| Existing table | Historical change | Phase 1 classification |
| --- | --- | --- |
| `candidate_profiles` | `job_search_criteria`, then nullable identity/contact fields | Missing nullable columns are additive candidates; missing identity, ownership, uniqueness, or incompatible physical definitions require manual resolution. |
| `candidate_cv_ingestion_drafts` | `runtime_attribution_json` | Missing nullable column is an additive candidate. |
| `candidate_adviser_profile_proposals` | `overlap_resolution_json` | Missing nullable column is an additive candidate. |
| CV review baselines, Adviser clarifications, Profile revisions, structured-item lineage, CV overlap reviews | Whole tables added over time | An absent table is reported as creatable by current metadata; the dry run does not create it. |

The physical inspector reads database catalog metadata. It compares the twelve candidate-domain tables against current SQLAlchemy metadata, checks columns, nullability/type shape, primary keys, ownership foreign keys, unique/check constraints, and indexes, then reports one of `compatible`, `additive_repair_available`, or `unsupported_drift`. It does not inspect unrelated job/discovery tables.

## Per-user data classification

The dry run only inspects explicitly supplied user IDs and always scopes each record query by `user_id`.

| Persisted state | Classification | Planned disposition |
| --- | --- | --- |
| Scalar Profile only | `not_yet_confirmed` | Keep Profile readable. Do not invent structured facts or evidence. |
| Uploaded or review-ready CV without structured authority | `not_yet_confirmed` | Keep the draft unconfirmed. Do not promote it. |
| One valid latest confirmed CV without structured authority | `repairable` | Report that structured reconstruction could be considered from the confirmed payload; do not perform it. |
| Equally latest confirmed CV payloads that differ, or malformed latest confirmed payload with no structured authority | `unresolved` | Require manual resolution; do not infer chronology or reconstruct. |
| Existing valid structured authority | `already_compatible` unless active evidence is incomplete | Preserve current structured state even when a confirmed historical CV differs. |
| Existing but invalid structured authority | `unresolved` | Do not fall back to an older CV and replace the existing authority. |
| Missing or stale active evidence rows | `repairable` | Evidence status distinguishes `missing`, `stale`, and `missing_and_stale`. Report resolver-derived reconciliation. The existing legacy fingerprint bridge is used as-is; legacy matches that still need current metadata are reported stale. |
| Confirmed factual Adviser clarification | Informational | The existing active-evidence resolver may include confirmed factual/mixed evidence; clarification text does not reconstruct structured Profile facts. |
| Unconfirmed clarification | Informational | Exclude it from active factual evidence and structured authority. |
| Draft/review-ready revisions, Adviser proposals, absent lineage, duplicate facts, and prior discovery/application artifacts | Informational | Preserve them as history. Do not promote, deduplicate, infer lineage, or rewrite snapshots. |

Per-user results are deterministic and fail closed. A user's malformed data is reported as unresolved without disclosing row content, and does not intentionally abort classification of other requested users. Adviser currentness is read through the existing provider-free read path; no provider or model is invoked.

## Phase 1 boundary

`CandidateCompatibilityDryRunService.inspect()` remains inspection-only. It performs no DML or DDL and never invokes schema repair. The explicit repair service is separate and has no startup hook, API endpoint, or frontend control.

## Phase 2: explicit SQLite schema repair

`CandidateSQLiteSchemaCompatibilityRepairService` is an internal, explicitly invoked schema-only operation for retained local SQLite databases. It is not called at startup. Non-SQLite engines are rejected. The service uses `CandidatePhysicalSchemaInspector` as its preflight and postflight authority and never opens an ORM session or examines candidate row contents.

The reviewed missing-column allowlist is fixed:

| Existing table | Nullable columns that may be added |
| --- | --- |
| `candidate_profiles` | `job_search_criteria`, `display_name`, `preferred_email`, `phone`, `linkedin_url`, `github_url`, `portfolio_url` |
| `candidate_cv_ingestion_drafts` | `runtime_attribution_json` |
| `candidate_adviser_profile_proposals` | `overlap_resolution_json` |

Column SQL types come from the current SQLAlchemy column metadata and are compiled for SQLite. Identifiers are quoted by the active dialect. No defaults or values are added; existing rows receive SQL `NULL` in newly added nullable columns.

Wholly absent tables are created only from `CANDIDATE_DOMAIN_TABLES`, in SQLAlchemy metadata dependency order. Current table constraints and indexes are created with each table. The physical `users` table must already exist. Missing ordinary non-unique metadata indexes may be recreated from their current `Index` definitions. Unique indexes/constraints, ownership or CHECK constraints, primary-key drift, missing `user_id`, and incompatible existing column/index shapes block the entire repair before DDL. Any missing existing-table column outside the allowlist also blocks all work. The service never rebuilds an existing table or calls unrestricted `Base.metadata.create_all()`.

The operation plan is fully validated before DDL. A successful call returns before/after schema reports and only the table/column/index repairs performed in that invocation. A second call on the repaired database is a no-op. A current schema is also a no-op.

The repair opens an explicit SQLite transaction with `BEGIN IMMEDIATE`, applies bounded `CREATE TABLE`, `ALTER TABLE ... ADD COLUMN`, and `CREATE INDEX` statements, then reinspects before commit. An injected failure after the first table creation was observed to roll back all DDL on the tested SQLite/SQLAlchemy driver: post-failure inspection found no candidate tables from the interrupted batch. The service raises a controlled failure with pre/post reports and completed pre-failure operations; a subsequent invocation completed successfully and matched a clean one-shot schema. This observed rollback behavior is covered by regression tests and is not generalized to other dialects.

Phase 2 does not reconcile evidence, reconstruct structured Profiles, create CV baseline rows, add clarification records, infer lineage, materialize overlap choices, or transform direct Profile history into revisions. Discovery, application, and all other non-candidate tables remain outside the repair plan. Historical row contents in retained candidate tables are preserved; only new nullable schema fields are `NULL`.

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

Wholly absent tables are created only from `CANDIDATE_DOMAIN_TABLES`, in SQLAlchemy metadata dependency order. Current table constraints and indexes are created with each table. Every candidate schema repair invocation requires the physical `users` table to exist, whether or not candidate DDL is planned. The compatibility service does not create or repair authentication schema. Missing ordinary non-unique metadata indexes may be recreated from their current `Index` definitions. Unique indexes/constraints, ownership or CHECK constraints, primary-key drift, missing `user_id`, and incompatible existing column/index shapes block the entire repair before DDL. Any missing existing-table column outside the allowlist also blocks all work. The service never rebuilds an existing table or calls unrestricted `Base.metadata.create_all()`.

The operation plan is fully validated before DDL. A successful call returns before/after schema reports and only the table/column/index repairs performed in that invocation. A second call on the repaired database is a no-op. A current schema is also a no-op.

The repair opens an explicit SQLite transaction with `BEGIN IMMEDIATE`, applies bounded `CREATE TABLE`, `ALTER TABLE ... ADD COLUMN`, and `CREATE INDEX` statements, then reinspects before commit. An injected failure after the first table creation was observed to roll back all DDL on the tested SQLite/SQLAlchemy driver: post-failure inspection found no candidate tables from the interrupted batch. The service raises a controlled failure with pre/post reports and completed pre-failure operations; a subsequent invocation completed successfully and matched a clean one-shot schema. This observed rollback behavior is covered by regression tests and is not generalized to other dialects.

Phase 2 does not reconcile evidence, reconstruct structured Profiles, create CV baseline rows, add clarification records, infer lineage, materialize overlap choices, or transform direct Profile history into revisions. Discovery, application, and all other non-candidate tables remain outside the repair plan. Historical row contents in retained candidate tables are preserved; only new nullable schema fields are `NULL`.

## Phase 3: explicit per-user data reconciliation

`CandidateLegacyDataReconciliationService` performs data reconciliation only when `CandidatePhysicalSchemaInspector` reports `compatible`. It never invokes Phase 2 repair. It owns a fresh session and outer transaction for each user; batch requests are deduplicated, sorted, and isolated so one unresolved or failed user does not roll back another user's result. SQLite reconciliation starts an explicit `BEGIN IMMEDIATE` transaction before classification so the resolver's nested savepoint remains inside the user's atomic transaction.

The Phase 1 classifier is rerun inside that transaction immediately before mutation. Its current source selection and tie/malformed-payload rules remain authoritative. A valid current `CandidateStructuredProfile` always wins and its serialized value and timestamp are preserved. An invalid current authority is returned unresolved. Missing structured state is reconstructed only from the classifier-selected, valid confirmed CV payload; uploaded/review-ready drafts and scalar Profile fields never become structured authority. The source draft remains unchanged, and reconstruction uses current creation-time semantics.

For a valid current or newly reconstructed structured Profile, the existing `ActiveCandidateEvidenceResolver` is inspected first and called only if active evidence is incomplete. This reuses legacy evidence IDs where the resolver's fingerprint bridge matches, refreshes current materialization metadata, retains inactive historical rows, and includes only confirmed factual/mixed clarification evidence. It does not promote unconfirmed clarification or derive evidence from scalar Profile text, skills alone, or standalone projects/achievements.

Reconstruction, evidence materialization, and postflight verification commit together for that user or roll back together. Evidence-only reconciliation also uses the per-user outer transaction around the resolver savepoint. Successful postflight verifies valid structured data, complete active evidence, and the Phase 1 classification. Repeated reconciliation is idempotent; an already compatible user causes no DML.

Phase 3 does not invoke semantic providers, CV confirmation, Profile revision or Adviser proposal workflows, or schema repair. It creates no CV overlap-review or CV baseline rows and no structured-item lineage; historical CV confirmation lacks the comparison context required for truthful lineage. Current Profile scalar fields, revisions, Adviser intake/assessments/proposals, existing lineage, clarification state, discovery/application/tracking artifacts, and inactive evidence remain historical state. The operational result reports user/status/action/count metadata and an optional selected CV draft ID, never profile or CV payload content. Phase 4 provides the bounded SQLite startup workflow and offline operator command described below.

## Phase 4: SQLite runtime orchestration and operator CLI

The runtime orchestrator composes the existing physical-schema inspector, the
reviewed Phase 2 SQLite repair, the Phase 1 provider-free dry run, and the Phase
3 per-user reconciliation. Its strict result reports database state, schema
before/after, repair counts, and aggregate user outcomes; physical-schema
failures raise a distinct blocked result while per-user failures remain
unresolved user entries.

`inspect()` is read-only. An empty database is reported as
`fresh_uninitialized`; a retained database without `users` is reported as
`retained_without_users`; and a retained compatible database includes a Phase
1 per-user plan. It does not bootstrap tables, repair schema, or reconcile user
data.

On SQLite startup, an empty database is bootstrapped from current metadata. A
retained database must have `users`; candidate schema repair runs before broad
metadata table creation, then candidate-schema postflight runs before sorted
user enumeration and Phase 3 reconciliation. Unsafe candidate schema blocks
startup before broad table creation or data reconciliation. A user's unresolved
result does not block startup. Startup logs only aggregate counts, never
candidate data or user identifiers. Non-SQLite startup keeps its prior
`Base.metadata.create_all` behavior and does not run candidate repair or
reconciliation.

Offline operations use the same orchestrator:

```text
career-trans dev candidate-compatibility inspect [--json] [--database-url URL]
career-trans dev candidate-compatibility apply --yes [--json] [--database-url URL]
```

`inspect` opens an existing SQLite file read-only and reports a missing local
file as fresh without creating it. `apply` requires the explicit `--yes` flag,
and is limited to SQLite. Both commands operate offline and invoke no providers.

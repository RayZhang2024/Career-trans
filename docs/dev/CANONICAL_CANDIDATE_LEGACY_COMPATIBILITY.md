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

The physical inspector reads database catalog metadata. It compares the twelve candidate-domain tables against current SQLAlchemy metadata, checks columns, nullability/type affinity, primary keys, ownership foreign keys, unique/check constraints, and indexes, then reports one of `compatible`, `additive_repair_available`, or `unsupported_drift`. It does not inspect unrelated job/discovery tables.

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
| Missing or stale active evidence rows | `repairable` | Report resolver-derived reconciliation. The existing legacy fingerprint bridge is used as-is; legacy matches that still need current metadata are reported stale. |
| Confirmed factual Adviser clarification | Informational | The existing active-evidence resolver may include confirmed factual/mixed evidence; clarification text does not reconstruct structured Profile facts. |
| Unconfirmed clarification | Informational | Exclude it from active factual evidence and structured authority. |
| Draft/review-ready revisions, Adviser proposals, absent lineage, duplicate facts, and prior discovery/application artifacts | Informational | Preserve them as history. Do not promote, deduplicate, infer lineage, or rewrite snapshots. |

Per-user results are deterministic and fail closed. A user's malformed data is reported as unresolved without disclosing row content, and does not intentionally abort classification of other requested users. Adviser currentness is read through the existing provider-free read path; no provider or model is invoked.

## Phase 1 boundary

The schema and per-user services are inspection-only. They perform no DML or DDL, have no startup hook, do not apply migrations, and do not mutate user data. Planned actions are descriptions for future migration planning, not executable migration instructions. No client endpoint or automatic repair is introduced here.

Before a later migration is designed, operators should run the dry run against representative physical databases and explicitly review unresolved users. A later phase can then define migrations for nullable columns, absent whole tables and ordinary indexes, while requiring manual handling of missing ownership/identity constraints, non-null data transformations, incompatible types, malformed authority, ambiguous CV chronology, and duplicate/conflicting records. This Phase 1 work does not perform those migrations or decide user-specific repairs.

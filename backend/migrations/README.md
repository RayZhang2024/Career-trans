# Discovery run request ID migration

## OpenAI semantic credential storage (Issue #269)

Apply repository migrations in their documented/date order through the
20261002 migrations, then apply the Issue #269 credential migration below
before starting the updated app. This additive migration creates
`user_semantic_credentials`, keyed by `(user_id, provider)`, which contains
AES-GCM ciphertext, nonce, format version, independent revision, four-character
display suffix, and timestamps. Apply it to every existing database before
starting the updated application. Back up the database and stop app writers
first. Then configure a separate 32-byte URL-safe base64
`SEMANTIC_CREDENTIAL_ENCRYPTION_KEY` in backend secret settings;
never reuse or commit the key, and keep a protected backup because saved user
credentials cannot be decrypted after key loss or rotation without a migration.
`SEMANTIC_CREDENTIAL_POLICY` accepts `deployment_only`, `user_required`, or
`user_or_deployment` (the default). A present but unusable user key is always
authoritative and never falls back to deployment credentials.

SQLite:

```powershell
python backend/migrations/20261004_user_semantic_credentials_sqlite.py "sqlite:///D:/Career-trans-data/career_agent.db"
```

PostgreSQL:

```powershell
psql $env:CAREER_TRANS_POSTGRES_DSN --set ON_ERROR_STOP=on --file backend/migrations/20261004_user_semantic_credentials_postgresql.sql
```

Verify `user_semantic_credentials` exists, its composite primary key is
`(user_id, provider)`, and its
foreign key references `users(id)` with cascade deletion. The SQLite migration
is safe to rerun; the PostgreSQL script uses `CREATE TABLE IF NOT EXISTS`.
Only after migration and encryption-key configuration should the updated app
start. `GET /api/v1/ai/credentials` reports policy and safe status only; it does
not return plaintext; it may locally verify decryption but never contacts
OpenAI. `POST /api/v1/ai/credentials/openai/test` tests only the submitted or
saved user credential with one small provider request. Settings key writes and removal use an
independent credential revision, separate from model preference revisions.

The updated application expects `discovery_runs.client_request_id` and the
per-user unique index `uq_discovery_run_client_request`. The app's
`create_all` startup step does not add columns to existing tables. Upgrade each
existing database **before starting or deploying the updated application**.

The migration is additive and preserves existing run rows. Keep a database
backup, stop application instances that write discovery runs, apply the
database-specific step below, verify the column and index, then start the
updated application. Apply the migration once per database (the scripts are
safe to rerun).

## SQLite

From the repository root, pass the SQLAlchemy URL of the existing database to
the Python migration. For a Windows file at `D:\Career-trans-data\career_agent.db`:

```powershell
python backend/migrations/20261002_discovery_run_request_id_sqlite.py "sqlite:///D:/Career-trans-data/career_agent.db"
```

For a POSIX file at `/var/lib/career-trans/career_agent.db`:

```sh
python backend/migrations/20261002_discovery_run_request_id_sqlite.py \
  'sqlite:////var/lib/career-trans/career_agent.db'
```

Verify the upgrade against the same file before starting the new app:

```sql
PRAGMA table_info(discovery_runs);
PRAGMA index_list(discovery_runs);
```

The first result must include nullable `client_request_id`; the second must
include unique index `uq_discovery_run_client_request`.

## PostgreSQL

Use a PostgreSQL DSN for the existing application database. Do not pass a
SQLAlchemy driver URL such as `postgresql+psycopg://` to `psql`; use the normal
PostgreSQL DSN stored in `CAREER_TRANS_POSTGRES_DSN`. From the repository root,
run:

```powershell
psql $env:CAREER_TRANS_POSTGRES_DSN --set ON_ERROR_STOP=on --file backend/migrations/20261002_discovery_run_request_id_postgresql.sql
```

Verify the upgrade against that same database before starting the new app:

```powershell
psql $env:CAREER_TRANS_POSTGRES_DSN --set ON_ERROR_STOP=on --command "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'discovery_runs' AND column_name = 'client_request_id'; SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'discovery_runs' AND indexname = 'uq_discovery_run_client_request';"
```

The column result must show `YES` for nullable and the index result must show a
unique index on `(user_id, client_request_id)`.

## Post-upgrade application check

After verification, start the updated backend and make authenticated requests
as a synthetic/test user (or an authorized user in the target environment):

```text
GET /api/v1/jobs/search-history?limit=20
GET /api/v1/jobs/inbox?limit=20
```

Both should return HTTP 200. Search History should continue to include legacy
rows; Inbox should return its normal bounded response. These GETs are read-only
and do not call a search provider.

## Adviser enrichment resolution migration (Issue #266)

Apply this migration after the existing Candidate Adviser and Profile proposal
migrations have been applied, and before starting the updated app:

1. Back up the database and stop application instances that write Adviser or
   Profile proposal data.
2. Apply the SQLite or PostgreSQL enrichment migration to the existing database.
3. Verify the new `candidate_adviser_enrichments` table and its
   `(user_id, clarification_id)` primary key.
4. Start the updated app.
5. Authenticate as a synthetic/test user and request
   `GET /api/v1/onboarding/status`; the nested Adviser journey should load
   without provider calls or clarification materialization.

SQLite:

```powershell
python backend/migrations/20261002_candidate_adviser_enrichments_sqlite.py "sqlite:///D:/Career-trans-data/career_agent.db"
```

PostgreSQL:

```powershell
psql $env:CAREER_TRANS_POSTGRES_DSN --set ON_ERROR_STOP=on --file backend/migrations/20261002_candidate_adviser_enrichments_postgresql.sql
```

Both migrations are safe to rerun. Confirmed career-fact/mixed clarifications
with existing proposal rows are backfilled as `proposals_created`; eligible
clarifications without proposal rows are backfilled as `pending`. Missing
proposal history is never interpreted as a historical successful empty result.

## Direct Adviser proposal application (Issue #275)

Apply the Issue #275 proposal-state migration after `20260926_candidate_adviser_profile_proposals.sql` and `20260929_candidate_adviser_proposal_overlap_resolution.sql` (and after the existing Issue #266 enrichment migration where used), before starting the updated app. It preserves existing pending, rejected, and transferred proposal records, adds nullable `applied_at`, and permits `applied` as a distinct terminal state. Do not start application writers during migration; back up the database first.

SQLite, from the repository root:

```powershell
python backend/migrations/20261005_candidate_adviser_applied_proposals_sqlite.py "sqlite:///D:/Career-trans-data/career_agent.db"
```

PostgreSQL:

```powershell
psql $env:CAREER_TRANS_POSTGRES_DSN --set ON_ERROR_STOP=on --file backend/migrations/20261005_candidate_adviser_applied_proposals_postgresql.sql
```

The SQLite migration rebuilds the proposal table transactionally to replace its
state check and is safe to rerun. The PostgreSQL migration is transactional and
safe to rerun. Before starting the updated app, verify that
`candidate_adviser_profile_proposals.applied_at` exists and its state check
accepts `pending`, `rejected`, `transferred`, and `applied`; existing transferred
rows and their linked Profile revisions remain unchanged.

# Discovery run request ID migration

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

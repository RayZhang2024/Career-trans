# Career Agent Backend — V1

FastAPI backend foundation for the multi-user Career Agent application.

## Included

- FastAPI application
- SQLite via SQLAlchemy 2
- email/password registration
- secure Argon2 password hashing via `pwdlib`
- JWT access-token login
- protected `GET /api/v1/users/me`
- user-scoped candidate-profile create/read/update
- structured job extraction and evidence-first requirement matching
- deterministic fit scoring and gap classification
- six-dimension Career Alignment V1 with deterministic weighted aggregation
- development demo workflow returning separate fit and career assessments
- CORS for the React development server
- tests for authentication and profile isolation

## Setup (PowerShell)

From `D:\career-trans\backend`:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Before any non-local deployment, replace `JWT_SECRET_KEY` in `.env` with a strong random secret.

## Run

```powershell
uvicorn app.main:app --reload
```

Open:

- API docs: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/health`

## Tests

```powershell
pytest
```

## V1 API

```text
POST  /api/v1/auth/register
POST  /api/v1/auth/login
GET   /api/v1/users/me
GET   /api/v1/profile
POST  /api/v1/profile
PATCH /api/v1/profile
POST  /api/v1/jobs/analyse
POST  /api/v1/jobs/match
POST  /api/v1/demo/analyse-and-match
GET   /health
```

## Important V1 limitations

- SQLite and automatic `create_all()` are for early development.
- Alembic migrations should be added before production deployment.
- Current JWT handling is an initial access-token implementation; production auth should add an appropriate refresh/session strategy, account recovery, email verification, rate limiting, and stronger operational controls.
- The profile schema is intentionally minimal. Employment, education, skills, projects, evidence, and preferences will be separate user-owned entities in later versions.
- The demo workflow uses repository fixture data. Production assessment persistence and authenticated candidate-context assembly are not implemented yet.
- A real demo analysis requires `OPENAI_API_KEY`; automated tests use fake AI components and make no OpenAI calls.

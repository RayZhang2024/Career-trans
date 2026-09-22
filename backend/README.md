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
- deterministic Recommendation V1 with explicit rule IDs and hard-blocker precedence
- development demo workflow returning separate fit, career, and recommendation assessments
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

## Local Docker Compose

From the repository root, `docker compose up --build` runs this same FastAPI
application on `0.0.0.0:8000` with an absolute SQLite URL in a named volume:
`sqlite:////data/career_agent.db`. `Base.metadata.create_all()` remains the
local-development initializer. The container does not install or use Codex;
the host CLI can reach this published API at `http://127.0.0.1:8000`.

`docker compose down` keeps the volume. Use `docker compose down -v` only when
you deliberately want to delete its local data.

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

# Career-trans

Career-trans is a multi-user AI-assisted job-search and career-application platform.

It is designed to help different users turn their own career history, evidence, goals and constraints into:

```text
Candidate context
      +
Job market / job descriptions
      |
      v
Relevant opportunities
      |
      v
Evidence-based requirement matching
      |
      v
Fit assessment
      |
      v
Career-alignment assessment
      |
      v
APPLY / CONSIDER / SKIP
      |
      v
Application preparation
      |
      v
Application tracking and learning
```

The repository is intentionally user-agnostic. Demo candidate data exists only for development/evaluation and must never become global product logic.

---

## Current product status

The browser now exposes the main onboarding flow and a Jobs workspace over the existing backend read models.

### Available in the browser today

The authenticated browser workspace includes:

- register;
- sign in / sign out;
- authenticated session restore;
- create and edit the user's basic profile;
- backend-owned onboarding status;
- responsive desktop/mobile presentation;
- CV upload, review, correction, and confirmation;
- Candidate Adviser intake, reviewable assessment, confirmation, and clarification answers.
- current ranked opportunities with lazy current-detail views;
- bounded discovery-run history with lazy run and historical job detail;
- a recent shared imported-vacancy inbox and selection of actionable records for evaluation;
- saved discovery configurations with daily/weekly recurrence setup, pause/resume, manual execution of persisted configurations, execution history, and historical configuration snapshots.
- application preparation from current ranked opportunities, saved preparation history, evidence-grounded CV / cover-letter / question review, and authenticated DOCX/PDF downloads.

The current browser flow is:

```text
Register
  -> Sign in
  -> Profile
  -> CV
  -> Career Adviser
  -> Jobs
  -> Applications
```


### Implemented in the backend

Current backend capabilities include:

- email/password registration and JWT authentication;
- authenticated, user-scoped profile CRUD;
- onboarding-status read model;
- CV upload and text extraction;
- semantic CV interpretation into structured candidate data;
- explicit CV review/edit/confirmation lifecycle;
- persisted candidate context and CareerEvidence;
- Candidate Adviser intake, assessment, confirmation and adaptive clarification lifecycle;
- structured job-description extraction into `JobProfile`;
- evidence-limited requirement matching;
- authenticated-user matching through persisted confirmed candidate context;
- deterministic fit scoring and gap classification;
- career-alignment assessment;
- deterministic `APPLY` / `CONSIDER` / `SKIP` recommendation rules;
- structured ATS discovery;
- bounded agentic/public-web discovery;
- external discovery import and verification;
- job enrichment, deduplication and lifecycle handling;
- job ranking and current-user ranking;
- persisted user discovery runs and current opportunities;
- recurring discovery schedules and execution history;
- application preparation with grounded CV/cover-letter drafting;
- downloadable CV and cover-letter DOCX/PDF outputs;
- provider-free, user-scoped reads of persisted application preparations;
- multi-user ownership and isolation tests;
- deterministic fake-provider tests with no live LLM/network dependency.

### Saved discovery and recurrence boundaries

The browser can save discovery configurations, configure daily/weekly recurrence, pause/resume future recurrence, manually execute a persisted saved configuration, browse execution history, and inspect the historical configuration snapshot used by an execution. Saving a configuration does not run it. Manual Run now executes only the persisted schedule ID and its saved configuration; unsaved edits must be saved or discarded first. Confirmed candidate context is required before the browser enables Run now. The semantic configuration check is advisory only, and the run-now API response is authoritative.

An enabled configuration means recurrence is configured, not that the scheduler is running. Automatic due execution requires the server-side scheduled-discovery operator and the required provider configuration. The current local Compose stack does not continuously run that operator. Missed due slots are coalesced by backend semantics rather than replayed individually. Pausing affects future recurrence only and does not cancel an already-running execution; the browser has no execution-cancel control. History snapshots represent the configuration used at claim time and do not imply a historical name, enabled state, or scheduler health.

Broad external Codex discovery remains a separate host-side workflow outside the browser and containers. The browser does not invoke Codex and does not expose its credentials. The Jobs inbox reads persisted public vacancies; it does not run browser-side discovery.

Application-preparation review and authenticated document downloads are available in the browser. Editing generated content, selective regeneration, and broader application tracking remain future milestones.

### Still planned

- application review/iteration enhancements (V2C2);
- application tracking/status history (V2D);
- production database migration;
- production identity/session hardening;
- deployment, monitoring and production storage.

---

## Architecture

Current local-development architecture:

```text
React / TypeScript / Vite
          |
          v
      FastAPI API
          |
          +--> auth / authorization
          +--> candidate profile + CV ingestion
          +--> Candidate Adviser
          +--> job analysis / discovery / ranking
          +--> requirement matching / scoring
          +--> application preparation
          |
          v
        SQLite

Semantic/provider boundaries
          |
          +--> OpenAI or configured LLM provider
          +--> public ATS / discovery providers where enabled
```

Current development persistence uses SQLite.

Production direction remains:

```text
React frontend
      |
FastAPI service
      |
PostgreSQL + private document storage
      |
provider integrations / monitoring
```

See `docs/ARCHITECTURE.md` for the broader target architecture. That document contains both current and future design material; this README is the shorter current-state guide.

---

## Repository structure

```text
career-trans/
|
|-- AGENTS.md
|-- README.md
|
|-- backend/
|   |-- app/
|   |   |-- agents/
|   |   |-- api/
|   |   |-- core/
|   |   |-- models/
|   |   |-- providers/
|   |   |-- schemas/
|   |   |-- services/
|   |   `-- workflows/
|   |-- tests/
|   `-- pyproject.toml
|
|-- frontend/
|   |-- src/
|   |-- package.json
|   `-- package-lock.json
|
|-- docs/
|   `-- ARCHITECTURE.md
|
|-- resources/
|   |-- README.md
|   |-- templates/
|   `-- examples/
|
`-- tests/
```

---

## Core concepts

### Candidate Profile

User-owned high-level career information such as headline, current role, location, summary, career goal and job-search criteria.

### Structured Candidate Context

The typed candidate representation consumed by downstream matching and career workflows.

For authenticated-user workflows it can be assembled from persisted confirmed candidate data. Demo Markdown loading remains available for development/evaluation.

### CareerEvidence

Atomic, provenance-aware evidence supporting claims about candidate capability.

Requirement matching may cite only permitted evidence records; the matcher cannot invent evidence IDs.

### CV Ingestion Draft

A user-scoped CV workflow with explicit states:

```text
uploaded
   -> interpret
review_ready
   -> review/edit
   -> confirm
confirmed
```

AI-extracted content does not silently become authoritative.

### Candidate Adviser

A separate career-strategy enrichment workflow with:

- intake;
- assessment;
- explicit confirmation;
- adaptive clarifications;
- reassessment when confirmed context changes.

It enriches career/search context without silently rewriting CV provenance.

### JobProfile

Structured job representation containing role information, responsibilities, requirements, skills, seniority and eligibility constraints.

### Requirement Match

Evidence-first assessment of one requirement.

Match types include:

- `demonstrated`;
- `transferable`;
- `inferred`;
- `missing`.

### Fit Assessment

Deterministic aggregation of requirement-level evidence into strengths, gaps, fit score and hard blockers.

### Career Alignment

A separate assessment of whether a role supports the user's own longer-term direction.

It remains distinct from current-role fit.

### Recommendation

Deterministic recommendation logic returning:

- `apply`;
- `consider`;
- `skip`.

Fit and career-alignment scores remain visible rather than being collapsed into an unexplained single model judgement.

### Discovered Job

A normalized vacancy collected from supported ATS/public-web discovery paths with source/lifecycle provenance.

Discovery omission alone is not treated as proof that a job is inactive.

### Application Preparation

A user-scoped immutable preparation snapshot combining the target role and confirmed candidate context to generate grounded application material.

Current backend output can include:

- tailored CV content;
- cover letter;
- application-question answers;
- DOCX;
- PDF.

It does not submit applications.

---

## Important API areas

The exact API is visible through Swagger at `/docs`. Major route groups currently include:

```text
/api/v1/auth
/api/v1/users
/api/v1/profile
/api/v1/onboarding
/api/v1/cv-ingestion
/api/v1/candidate-adviser
/api/v1/jobs
/api/v1/applications
/api/v1/demo
```

Examples include:

```text
POST /api/v1/auth/register
POST /api/v1/auth/login
GET  /api/v1/users/me

GET   /api/v1/profile
POST  /api/v1/profile
PATCH /api/v1/profile
GET   /api/v1/onboarding/status

POST /api/v1/cv-ingestion/upload
POST /api/v1/cv-ingestion/{draft_id}/interpret
PATCH /api/v1/cv-ingestion/{draft_id}
POST /api/v1/cv-ingestion/{draft_id}/confirm

PUT  /api/v1/candidate-adviser/intake
POST /api/v1/candidate-adviser/assessment
POST /api/v1/candidate-adviser/assessment/confirm
GET  /api/v1/candidate-adviser/clarifications

POST /api/v1/jobs/analyse
POST /api/v1/jobs/match
POST /api/v1/jobs/match-me
POST /api/v1/jobs/discover
POST /api/v1/jobs/discover-agentic-me
POST /api/v1/jobs/rank
POST /api/v1/jobs/rank-me
POST /api/v1/jobs/discovery-runs
GET  /api/v1/jobs/opportunities
POST /api/v1/jobs/discovery-schedules

POST /api/v1/applications/prepare
GET  /api/v1/applications
```

Swagger is the authoritative route reference.

---

## Local development

### Prerequisites

Backend:

- Python 3.11+

Frontend:

- Node.js 24.15+
- npm 11+

### 1. Backend

Open a PowerShell terminal:

```powershell
cd D:\Career-trans\backend
.\.venv\Scripts\Activate.ps1

python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

The API runs at:

```text
http://127.0.0.1:8000
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

Default local persistence:

```text
sqlite:///./career_agent.db
```

Settings are loaded from environment variables and/or `backend/.env`.

Common optional settings include:

```text
OPENAI_API_KEY
DATABASE_URL
JWT_SECRET_KEY
DEFAULT_LLM_PROVIDER
LLM_BASE_URL
AGENTIC_SEARCH_PROVIDER
BRAVE_SEARCH_API_KEY
```

Provider-backed semantic operations require the corresponding provider configuration. Deterministic automated tests do not require live provider calls.

### 2. Frontend

Open a second PowerShell terminal:

```powershell
cd D:\Career-trans\frontend

npm ci
npm run dev
```

The Vite UI runs at:

```text
http://localhost:5173
```

By default the Vite development server proxies same-origin `/api/*` requests
to `http://127.0.0.1:8000`. A separately hosted API can be selected at build
time with:

```text
VITE_API_BASE_URL
```

The Python virtual environment is required for the backend, not for the frontend.

### 3. Local Docker Compose stack

Docker Desktop can run the production frontend build, reverse proxy, FastAPI,
and a disposable local SQLite volume without provider credentials:

```powershell
cd D:\Career-trans
docker compose up --build
```

Open the UI at `http://127.0.0.1:5173`; the API remains available to host-side
tools at `http://127.0.0.1:8000`. The browser only calls same-origin `/api/*`;
nginx proxies that path internally to the backend container. Direct refreshes
of `/`, `/cv`, `/adviser`, and `/jobs` are served by the SPA fallback.

Compose uses `sqlite:////data/career_agent.db` in the named
`career_agent_data` volume. `docker compose down` retains it; `docker compose
down -v` permanently removes this local Compose data. It never reuses the
native development database.

The stack starts without `OPENAI_API_KEY` or any secret file. Compose reads
optional backend-only provider settings from the shell environment and/or a
root-level Compose `.env` file; `backend/.env` is used by native backend runs
but is not automatically a Compose environment source. Provider settings are
never passed to the frontend image. The host `career-trans jobs
discover-external` and `career-trans jobs hunt` commands continue to use the
locally installed/authenticated Codex CLI and should target the published API
URL (for example `--base-url http://127.0.0.1:8000`). Codex is intentionally
not installed or configured inside Compose.

---

## Validation

Backend:

```powershell
cd D:\Career-trans\backend
.\.venv\Scripts\Activate.ps1
python -m pytest
```

Frontend:

```powershell
cd D:\Career-trans\frontend
npm run test
npm run typecheck
npm run build
```

Automated tests should remain deterministic and must not depend on live LLM or public-network calls unless a test is explicitly designed and authorized as live validation.

---

## Security and privacy

Career-trans handles private career information, so the main design principles are:

- authenticated user ownership is enforced server-side;
- one user's private candidate context must not leak into another user's workflow;
- passwords are hashed rather than stored in plaintext;
- secrets are not committed;
- private CV/evidence content is not used as global shared product state;
- demo fixtures are separate from production/user data;
- LLM/provider outputs are validated before becoming trusted domain state;
- user confirmation is required at important AI-extraction boundaries;
- application preparation never autonomously submits an application.

The current browser auth stores the V1 bearer token in `sessionStorage`. This is a development-stage implementation, not the final production identity architecture.

---

## Demo data

Files under `resources/examples/` are development/evaluation fixtures only.

They exist to exercise the system and must not be treated as globally applicable candidate information.

---

## Later product work

Application-preparation UI supports new preparations from current ranked opportunities and historical review/downloads; editing or regenerating drafts remains future V2C2 work. Application status tracking remains future V2D work. Broad external Codex discovery continues through the host-side workflow; the browser evaluates only persisted public vacancies selected from the Jobs inbox. Saved-discovery configuration, manual execution, and execution-history controls are available in the Jobs workspace; automated recurrence still requires an operated server-side scheduler and required providers.

---

## Development policy

Repository-wide implementation instructions are in `AGENTS.md`.

General project expectations include:

- preserve user isolation and evidence provenance;
- keep deterministic rules outside LLM prompts where practical;
- fail closed at trust boundaries;
- keep automated tests provider-independent;
- use synthetic fixtures rather than real user data;
- do not merge implementation PRs without explicit product-owner approval.

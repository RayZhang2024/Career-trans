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

The backend is substantially ahead of the browser UI.

### Available in the browser today

UI V1A is implemented:

- register;
- sign in / sign out;
- authenticated session restore;
- create and edit the user's basic profile;
- backend-owned onboarding status;
- responsive desktop/mobile presentation;
- explicit placeholder stages for CV and Career Adviser.

The current browser flow is:

```text
Register
  -> Sign in
  -> Profile
  -> CV                 (UI planned next)
  -> Career Adviser     (UI planned after CV)
```

CV and Career Adviser are not yet interactive in the frontend, even though their backend capabilities already exist.

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
- multi-user ownership and isolation tests;
- deterministic fake-provider tests with no live LLM/network dependency.

### Not yet exposed in the browser

The following are implemented partly or fully in the backend but do not yet have normal end-user UI:

- CV upload/review/confirmation — tracked by #161;
- Candidate Adviser and clarification loop — tracked by #162;
- job discovery/ranking dashboard;
- application-preparation UI;
- recurring-discovery controls.

### Still planned

- application tracking/status history;
- production database migration;
- production identity/session hardening;
- Docker/local full-stack packaging — tracked by #163;
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

By default the frontend calls:

```text
http://127.0.0.1:8000
```

Override it when needed with:

```text
VITE_API_BASE_URL
```

The Python virtual environment is required for the backend, not for the frontend.

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

## Near-term UI roadmap

The next planned UI slices are:

1. **#161 — CV Upload, Safe Review, Confirmation & Resume**
2. **#162 — Candidate Adviser & Adaptive Clarification Loop**
3. **#163 — Docker & Local Full-Stack Developer Packaging**

Job discovery/ranking and application-preparation browser workflows come after the onboarding UI is complete.

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

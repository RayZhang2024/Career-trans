# Career Agent

Career Agent is a multi-user AI-powered web application for job discovery, job-fit assessment, career strategy, application preparation, and application tracking.

The application is intended to support different users with different backgrounds, career goals, skills, locations, and job-search strategies.

It must not be optimised around one fixed candidate profile.

---

## Product Vision

Career Agent should help a user move from:

```text
Career history + goals
        +
     Job market
        |
        v
Relevant opportunities
        |
        v
Evidence-based fit assessment
        |
        v
Career-value assessment
        |
        v
Application preparation
        |
        v
Application tracking and learning
```

The system should eventually answer two separate questions for each role:

1. **Can this user realistically obtain and perform the role?**
2. **Should this user pursue the role given their longer-term career direction?**

---

## Intended User Journey

```text
Register / login
      |
      v
Create profile
      |
      +--> upload CV
      +--> enter preferences
      +--> review extracted experience
      |
      v
Submit job URL or description
      |
      v
Extract job requirements
      |
      v
Match against user's evidence
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
CV tailoring / cover letter
      |
      v
Application tracking
```

Later versions will add recurring job discovery and shortlist generation.

---

# Architecture

Target architecture:

```text
React / TypeScript frontend
          |
          v
      FastAPI API
          |
          +--> authentication / authorization
          |
          +--> candidate-profile services
          |
          +--> job-analysis services
          |
          +--> matching / scoring
          |
          +--> agent workflows
          |
          +--> PostgreSQL
          |
          +--> file/object storage
          |
          +--> LLM and web providers
```

The application should support local development first and production deployment later.

See `docs/ARCHITECTURE.md` for the detailed design.

---

# Multi-User Design

Career Agent is designed for external users.

Each registered user owns their own candidate profile, career evidence, skills, projects, uploaded CVs, preferences, saved jobs, assessments, application materials, and application history.

Production data must be isolated by authenticated user identity.

Shared prompts and source code must not contain candidate-specific information.

---

# Repository Structure

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
|   |   |-- schemas/
|   |   |-- services/
|   |   `-- workflows/
|   `-- tests/
|
|-- frontend/
|   |-- public/
|   `-- src/
|
|-- docs/
|   `-- ARCHITECTURE.md
|
|-- prompts/
|
|-- resources/
|   |-- README.md
|   |-- templates/
|   `-- examples/
|
`-- tests/
```

---

# Resources

`resources/` contains development resources, templates, and demo fixtures. It does **not** contain production registered-user data.

Demo profiles exist only for development, testing, evaluation, and examples. They must never be treated as globally applicable candidate information.

---

# Core Concepts

## Candidate Context

A typed, user-agnostic representation of candidate information consumed by matching workflows. During development it can be loaded from demo Markdown resources; in production it will be assembled from authenticated user data.

## Career Evidence

Atomic evidence supporting claims about candidate capability. Evidence items have stable IDs so requirement matches can cite the exact supporting records.

## Job Profile

Structured representation of a job including title, company, location, responsibilities, essential/desirable requirements, technical skills, seniority, and eligibility constraints.

## Requirement Match

An evidence-first assessment of one job requirement. Match types are:

- `demonstrated`
- `transferable`
- `inferred`
- `missing`

Each match includes a 0–1 evidence-strength score, cited evidence IDs, and concise reasoning.

## Fit Assessment

Measures how well the user's current evidence matches the role. V1 deterministically
aggregates requirement scores and reports strengths, classified gaps, and confirmed
hard blockers.

## Career Assessment

Measures whether the role moves the user in their preferred strategic direction.
Career Alignment V1 scores six explainable dimensions independently of current-role
fit, aggregates them with centrally configured Python weights, and reports explicit
confidence based on the supplied strategy, preferences, and job information.

## Recommendation Assessment

Combines fit and career alignment through explicit deterministic rules while
preserving both scores. Recommendation V1 returns `apply`, `consider`, or `skip`, a
stable rule ID, concise reasoning, existing strengths/trade-offs, and hard blockers.

---

# Implemented Backend Capabilities

Current backend functionality includes:

- FastAPI application;
- email/password registration and login;
- JWT-protected current-user endpoint;
- user-scoped candidate profile CRUD;
- structured job-description extraction through `POST /api/v1/jobs/analyse`;
- typed `JobProfile` and `JobRequirement` schemas;
- generic Markdown demo-candidate loading into `CandidateContext`;
- evidence-first requirement matching through `POST /api/v1/jobs/match`;
- typed `RequirementMatch` output;
- validation that matchers cannot alter requirements or invent evidence IDs;
- deterministic fit scoring and gap classification;
- career-alignment assessment across six strategic dimensions, kept separate from fit;
- deterministic career-alignment aggregation with explicit input-confidence handling;
- development demo workflow returning fit, career, and recommendation assessments;
- deterministic APPLY / CONSIDER / SKIP recommendation with hard-blocker precedence;
- backend tests using fake AI components so automated tests do not call OpenAI.

The matching endpoint currently accepts candidate context directly in the request. Production user-data loading and persistence will be connected later.

---

# Planned Development Stages

## Web Foundation

Backend registration/login/profile support exists. React authentication/profile UI is deferred while the core intelligence workflow is developed.

## Profile Intelligence

Planned:

- CV upload;
- CV parsing;
- structured candidate profile;
- evidence extraction;
- user review/editing.

## Job Analysis

Implemented for raw job-description text. Job URL fetching is still planned.

## Matching and Scoring

Requirement/evidence matching is implemented at V1 level.

The backend demo workflow also implements deterministic aggregate fit scoring,
explainable strengths, gap classification, and hard-blocker detection.

## Career Strategy

Career Alignment V1 is implemented in the backend demo workflow. It consumes the
candidate's dynamically loaded strategy and preferences and returns dimension-level
reasoning, strategic strengths/trade-offs, a deterministic 0–100 score, and explicit
confidence.

Recommendation V1 is also implemented in the demo workflow. It uses centrally
configured thresholds, supports a documented strategic-stretch APPLY case, downgrades
score-based APPLY decisions when career confidence is low, and otherwise preserves
mixed or uncertain cases for human review with CONSIDER.

## Application Preparation

Planned:

- evidence selection;
- CV tailoring;
- cover-letter drafting.

## Application Tracking

Planned:

- saved jobs;
- application status;
- events;
- outcomes;
- notes.

## Job Discovery

Planned:

- recurring searches;
- deduplication;
- filtering;
- scoring;
- shortlist generation.

---

# Technology Direction

## Frontend

- React
- TypeScript

## Backend

- Python
- FastAPI
- Pydantic
- SQLAlchemy

## Database

Development currently uses SQLite. Production target is PostgreSQL.

## AI

AI/provider logic is kept behind interfaces. Current semantic uses are job extraction,
requirement matching, and career alignment. Final recommendation selection is
deterministic Python and does not call another model.

## Workflow

LangGraph will be introduced when the workflow has enough stateful stages to justify orchestration complexity.

---

# Security

Security is a core product requirement because the application stores private career information.

Important principles:

- user-owned production data is scoped to authenticated users;
- passwords are never stored in plaintext;
- secrets are never committed;
- authorization is enforced server-side;
- uploaded documents will be private;
- demo data is never mixed with production user data.

---

# Development

Repository-wide coding instructions are defined in `AGENTS.md`.

From `backend/`, with the virtual environment active:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
uvicorn app.main:app --reload
```

Swagger is available locally at:

```text
http://127.0.0.1:8000/docs
```

---

# Demo Data

Example candidate profiles under `resources/examples/` are fixtures for development and evaluation only.

The application itself must work for users with very different backgrounds and goals.

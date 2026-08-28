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
Build candidate understanding
      |
      +--> upload and confirm CV evidence
      +--> complete structured career-adviser intake
      +--> review adviser assessment / role hypotheses
      |
      v
Discover or submit jobs
      |
      v
Extract job requirements
      |
      v
Match against user's factual evidence
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

Later versions will add richer conversational adviser workflows and application-outcome learning.

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
          +--> candidate-understanding services
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

See `docs/ARCHITECTURE.md` for the detailed design and `docs/CANDIDATE_ADVISER.md` for the candidate-understanding evidence boundary.

---

# Multi-User Design

Career Agent is designed for external users.

Each registered user owns their own candidate profile, career evidence, candidate intake, adviser assessment, skills, projects, uploaded CVs, preferences, saved jobs, assessments, application materials, and application history.

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
|   |-- ARCHITECTURE.md
|   `-- CANDIDATE_ADVISER.md
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

A typed, user-agnostic representation assembled from authenticated user data in production. It keeps factual career evidence separate from candidate-authored strategy/preferences and confirmed adviser interpretation so each downstream stage receives only the authority level it needs.

## Career Evidence

Atomic evidence supporting claims about candidate capability. Evidence items have stable IDs so requirement matches can cite the exact supporting records. Adviser interpretations never silently become career evidence.

## Candidate Adviser Intake

Structured candidate-authored information covering career direction, work preferences, constraints and eligibility, self-assessment, motivations, and trade-offs. Intake is source data but is not treated as independently verified CV evidence.

## Candidate Adviser Assessment

A reviewable semantic interpretation of confirmed CV/profile data plus confirmed intake. It can identify professional positioning, transferable strengths, development gaps, role hypotheses, unresolved questions, and career/search strategy. Confirmed adviser summaries inform discovery and career alignment but are deliberately excluded from requirement-matching evidence.

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
confidence based on the supplied strategy, preferences, adviser context, and job information.

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
- CV upload, deterministic file-text extraction, semantic structured interpretation, review and confirmation;
- persisted structured candidate profiles and provenance-aware career evidence;
- structured candidate-adviser intake with review/confirmation lifecycle;
- semantic candidate-adviser assessment with validated source references and deterministic stale-input detection;
- confirmed intake projection into career strategy, job-search criteria, and structured eligibility;
- confirmed current adviser projection into job discovery and career alignment while keeping it out of requirement-matching evidence;
- structured job-description extraction through `POST /api/v1/jobs/analyse`;
- typed `JobProfile` and `JobRequirement` schemas;
- generic Markdown demo-candidate loading into `CandidateContext`;
- authenticated persisted candidate-context loading for matching/ranking/discovery;
- evidence-first requirement matching through `POST /api/v1/jobs/match` and authenticated `match-me`;
- typed `RequirementMatch` output;
- validation that matchers cannot alter requirements or invent evidence IDs;
- deterministic fit scoring and gap classification;
- career-alignment assessment across six strategic dimensions, kept separate from fit;
- deterministic career-alignment aggregation with explicit input-confidence handling;
- development demo workflow returning fit, career, and recommendation assessments;
- deterministic APPLY / CONSIDER / SKIP recommendation with hard-blocker precedence;
- backend tests using fake AI components so automated tests do not require live model calls.

---

# Planned Development Stages

## Web Foundation

Backend registration/login/profile support exists. React authentication/profile UI is deferred while the core intelligence workflow is developed.

## Profile Intelligence

Implemented backend foundations:

- CV upload and parsing;
- structured candidate profile and evidence extraction;
- user review/editing and confirmation;
- structured career-adviser intake;
- reviewable semantic adviser assessment;
- stale-assessment invalidation when confirmed candidate sources change;
- purpose-specific projections for matching, discovery, and career alignment.

Still planned:

- frontend onboarding/adviser intake UI;
- richer iterative/conversational follow-up workflow;
- additional non-CV evidence capture where appropriate.

## Job Analysis

Implemented for raw job-description text. Job URL fetching/enrichment exists in selected discovery flows and can continue to expand.

## Matching and Scoring

Requirement/evidence matching is implemented at V1 level.

The backend workflow also implements deterministic aggregate fit scoring,
explainable strengths, gap classification, and hard-blocker detection. Candidate-adviser
inference is intentionally excluded from the requirement-matching evidence set.

## Career Strategy

Career Alignment V1 is implemented. It consumes the candidate's dynamically loaded
strategy and preferences plus bounded confirmed adviser context and returns dimension-level
reasoning, strategic strengths/trade-offs, a deterministic 0–100 score, and explicit
confidence.

Recommendation V1 is also implemented. It uses centrally configured thresholds,
supports a documented strategic-stretch APPLY case, downgrades score-based APPLY
decisions when career confidence is low, and otherwise preserves mixed or uncertain
cases for human review with CONSIDER.

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

Implemented foundations:

- bounded agentic public-web discovery from authenticated candidate context and search criteria;
- structured search strategies, provider-neutral web-search and page-fetch boundaries;
- deterministic URL filtering, page caps, vacancy normalization, deduplication, and non-authoritative lifecycle persistence;
- existing downstream screening and ranking APIs for normalized listings;
- candidate search projections that can include confirmed adviser role hypotheses and development priorities.

Agentic discovery never submits applications and does not treat web-search omission as proof that a posting is inactive.

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

AI/provider logic is kept behind interfaces. Current semantic uses include CV interpretation,
candidate adviser assessment, job extraction, requirement matching, discovery strategy,
and career alignment. Final recommendation selection is deterministic Python and does not
call another model.

## Workflow

LangGraph is used where stateful orchestration adds value and should not replace simpler deterministic service workflows. Candidate Adviser V1 deliberately uses an explicit review/confirmation service lifecycle rather than an autonomous conversational graph.

---

# Security

Security is a core product requirement because the application stores private career information.

Important principles:

- user-owned production data is scoped to authenticated users;
- passwords are never stored in plaintext;
- secrets are never committed;
- authorization is enforced server-side;
- uploaded documents and candidate intake are private;
- adviser inference is kept separate from authoritative career evidence;
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

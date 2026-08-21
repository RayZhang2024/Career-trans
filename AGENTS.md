# AGENTS.md

## Purpose

This file defines how coding agents and AI assistants should build, modify, test, and evolve the Career Agent repository.

These instructions apply to the whole repository unless a more specific instruction file overrides them.

---

# 1. Product Goal

Build a multi-user web application that helps people:

1. register and log in;
2. create or upload a career profile;
3. build a structured evidence base from their CV and other career information;
4. submit a job URL or job description;
5. extract structured job requirements;
6. compare those requirements with the authenticated user's profile and evidence;
7. generate explainable job-fit and career-alignment assessments;
8. identify gaps and hard blockers;
9. generate CV-tailoring recommendations and application materials;
10. track applications;
11. discover and rank relevant jobs;
12. learn from application outcomes over time.

The final product is intended for external users and must not assume any single candidate profile.

---

# 2. Core Architectural Principle

The application must be user-agnostic.

Never hard-code candidate-specific information into:

- source code;
- prompts;
- scoring logic;
- workflow definitions;
- shared configuration;
- shared resources;
- tests intended to validate generic application behaviour.

Candidate-specific information must be loaded dynamically from the authenticated user's data.

Demo or fixture data may exist under `resources/examples/`, but it must never be treated as production user data or global application knowledge.

---

# 3. Multi-User Data Isolation

All user-owned records must be scoped to a user identity.

Examples include:

- candidate profiles;
- career evidence;
- skills;
- projects;
- uploaded CVs;
- preferences;
- job analyses;
- saved jobs;
- assessments;
- tailored CVs;
- cover letters;
- application records;
- interview notes.

Production storage must associate these records with `user_id` or an equivalent immutable user identifier.

Backend services must always operate on the authenticated user's scope.

Do not implement data access patterns that can expose one user's information to another user.

Examples:

Correct:

```python
get_profile(user_id=current_user.id)
```

Incorrect:

```python
get_all_profiles()
```

unless the operation is explicitly administrative and access-controlled.

---

# 4. Security and Privacy

Treat user career information as private data.

Never:

- log passwords;
- log authentication tokens;
- commit secrets;
- expose private profile data in client-visible debug output;
- use one user's data to answer another user's request;
- include user documents in shared prompts without explicit request context;
- place production user data under `resources/`.

Use environment variables or managed secret storage for credentials.

Provide `.env.example` rather than committing `.env`.

Authentication and authorization are separate concerns:

- authentication determines who the user is;
- authorization determines what that user can access.

Both must be enforced.

---

# 5. Product Architecture

Target architecture:

```text
React frontend
      |
      v
FastAPI backend
      |
      +--> Authentication / authorization
      |
      +--> Career Agent services
      |
      +--> LLM / web / retrieval integrations
      |
      +--> PostgreSQL
      |
      +--> Object/file storage
```

The architecture should support local development first and production deployment later.

Do not tightly couple the core career-analysis logic to React, FastAPI, one database, or one model provider.

---

# 6. Development Strategy

Build incrementally.

Each development step must leave the repository in a runnable and testable state.

Recommended progression:

## V1 — Core web foundation

- FastAPI application
- React application
- register / login
- authenticated current-user endpoint
- basic profile creation and retrieval

## V2 — Candidate profile ingestion

- CV upload
- structured profile extraction
- user review and correction
- career evidence creation

## V3 — Job analysis

- job text / URL input
- structured `JobProfile`
- structured `JobRequirement`

## V4 — Matching and scoring

- requirement-by-requirement evidence matching
- fit scoring
- gap classification
- explainable assessment

## V5 — Career strategy

- career-alignment scoring
- APPLY / CONSIDER / SKIP recommendation

## V6 — Application preparation

- evidence retrieval
- CV-tailoring recommendations
- tailored CV drafting
- cover-letter drafting

## V7 — Application tracking

- saved jobs
- status
- application events
- notes
- outcomes

## V8 — Job discovery

- recurring discovery
- deduplication
- fast filtering
- scoring
- shortlist generation

Do not implement later-stage features prematurely unless required by the current task.

---

# 7. Prefer Workflows Over Uncontrolled Autonomy

Use explicit workflows and deterministic control flow where practical.

Use normal Python for:

- validation;
- scoring aggregation;
- thresholds;
- routing rules;
- deduplication;
- database operations;
- authorization;
- dates;
- identifiers;
- deterministic transformations.

Use LLMs for:

- job requirement extraction;
- semantic matching;
- transferable-skill assessment;
- career reasoning;
- evidence selection;
- gap interpretation;
- CV rewriting;
- cover-letter drafting.

Use agentic behaviour only when it adds clear value.

---

# 8. Structured Data First

Important intermediate outputs must use typed schemas.

Prefer:

- Pydantic models;
- TypedDict where appropriate;
- enums for controlled categories.

Core schemas should eventually include:

- `User`
- `CandidateProfile`
- `CareerEvidence`
- `Project`
- `Skill`
- `JobProfile`
- `JobRequirement`
- `RequirementMatch`
- `Gap`
- `FitAssessment`
- `CareerAssessment`
- `ApplicationRecord`

Do not pass loosely structured prose between stages when structured data is practical.

---

# 9. Evidence-First Matching

A claimed candidate match should be supported by evidence belonging to the authenticated user.

Classify evidence as:

- demonstrated;
- transferable;
- inferred;
- missing.

Never convert inferred capability into demonstrated capability.

A missing keyword is not automatically a gap.

A claimed match is not valid merely because an LLM says it is.

---

# 10. Separate Fit From Career Value

Always distinguish:

> Can this user credibly obtain and perform this role?

from:

> Would this role improve this user's longer-term career position?

Maintain separate concepts such as:

- `fit_score`
- `career_alignment_score`

Final recommendations may combine them but should retain the distinction.

---

# 11. Explainable Scoring

Do not produce only an unexplained percentage.

Possible score dimensions include:

- essential-requirement match;
- technical-skill match;
- relevant experience;
- transferable experience;
- seniority;
- domain fit;
- customer-facing fit;
- leadership fit;
- location;
- work authorization / eligibility;
- career alignment.

Where possible:

- LLMs judge semantic compatibility;
- Python performs final score aggregation.

Store enough evidence to explain each score.

---

# 12. Gap Classification

Use explicit categories:

- `hard_blocker`
- `meaningful_capability_gap`
- `learnable_gap`
- `evidence_gap`
- `positioning_gap`

Do not label every missing tool/framework as a major gap.

---

# 13. Resource Policy

`resources/` contains repository-level development resources only.

Allowed:

- templates;
- example/demo profiles;
- synthetic fixtures;
- static reference material.

Not allowed:

- production registered-user data;
- live user CV uploads;
- private application histories;
- authentication data.

Example data under `resources/examples/` is test/demo material only.

---

# 14. Prompt Policy

Shared prompts belong under `prompts/`.

Prompts must be:

- user-agnostic;
- reusable;
- version-controlled;
- written to consume candidate data dynamically;
- explicit about factual accuracy and evidence boundaries.

Never encode a specific candidate's history into a shared system prompt.

---

# 15. Backend Structure

Preferred structure:

```text
backend/
└── app/
    ├── agents/
    ├── api/
    ├── core/
    ├── models/
    ├── schemas/
    ├── services/
    └── workflows/
```

Suggested responsibilities:

### `api/`
HTTP routes and request/response handling.

### `core/`
Configuration, authentication utilities, shared infrastructure.

### `models/`
Database models.

### `schemas/`
Pydantic request/response/domain schemas.

### `services/`
Business logic and external integrations.

### `agents/`
LLM-powered components.

### `workflows/`
Multi-step orchestration and LangGraph workflows.

Keep HTTP logic out of core business services.

Keep database access out of React.

---

# 16. Frontend Structure

Target frontend is React.

Prefer TypeScript unless there is a strong reason not to.

The frontend should eventually support:

- registration;
- login/logout;
- onboarding;
- CV upload;
- profile editing;
- job analysis;
- assessment views;
- saved jobs;
- application tracking;
- settings.

Do not place sensitive business logic or authorization decisions solely in the frontend.

---

# 17. API Design

Use explicit versioned API routes when appropriate, for example:

```text
/api/v1/auth/register
/api/v1/auth/login
/api/v1/users/me
/api/v1/profile
/api/v1/jobs
/api/v1/jobs/{job_id}/assessment
/api/v1/applications
```

API handlers should:

- validate input;
- authenticate;
- authorize;
- delegate to services;
- return typed responses.

---

# 18. Authentication

The application must support external-user registration and login.

Initial implementation may use secure email/password authentication.

Production-quality authentication must include:

- strong password hashing;
- secure token/session handling;
- expiration;
- refresh/re-authentication strategy where required;
- account-level authorization;
- logout;
- protection against unauthorized access.

Do not implement custom cryptography.

A managed identity provider may replace or supplement custom authentication later.

---

# 19. Persistence

Use simple storage during early development where useful, but design toward:

- PostgreSQL for structured production data;
- object/file storage for uploaded documents;
- optional vector/search infrastructure only when genuinely needed.

Do not add a vector database prematurely.

---

# 20. Model Strategy

Allow model substitution.

Do not hard-code the entire system to a single provider.

Model configuration should be centralised.

Potential model roles:

- stronger cloud model for semantic reasoning;
- cheaper/local model for simple extraction or classification experiments.

Keep provider-specific logic behind service interfaces.

---

# 21. External Tools and Web Access

Web search, job-page extraction, browser automation, and third-party APIs should be isolated behind tool/service interfaces.

The core scoring and domain models should not depend directly on one web provider.

---

# 22. Testing

Every meaningful behavioural change should include appropriate tests.

Prioritise:

- schema validation;
- authentication;
- authorization;
- per-user data isolation;
- scoring;
- routing;
- parsing;
- database operations;
- deterministic business rules.

Use evaluation cases for LLM behaviour instead of brittle exact-string assertions.

Root-level `tests/` may be used for:

- integration tests;
- end-to-end tests;
- cross-stack tests;
- shared fixtures.

`backend/tests/` should contain backend-focused tests.

---

# 23. Evaluation

Maintain evaluation cases representing different candidate types.

Do not optimise only for the demo candidate.

Useful evaluation personas include:

- experienced technical specialist;
- software engineer;
- graduate;
- career switcher;
- project manager;
- data professional.

Evaluation should check both:

- quality of matching;
- fairness of behaviour across different profile shapes.

---

# 24. Logging and Observability

Log useful operational information such as:

- request IDs;
- workflow stage;
- model/provider;
- extraction counts;
- scoring components;
- routing decisions;
- failures.

Never log:

- passwords;
- tokens;
- raw secrets;
- unnecessary private user content.

---

# 25. Documentation

Keep documentation current.

## `README.md`

Should contain:

1. product purpose;
2. current implementation status;
3. architecture;
4. setup;
5. how to run;
6. tests;
7. limitations;
8. roadmap.

Do not describe unimplemented features as already available.

## `docs/ARCHITECTURE.md`

Should explain intended and implemented system design.

---

# 26. Coding Style

Use modern Python and modern React/TypeScript practices.

Prefer:

- type hints;
- Pydantic;
- pathlib;
- dependency injection where useful;
- descriptive naming;
- small testable functions;
- clear module boundaries.

Avoid:

- unnecessary abstractions;
- giant files;
- deep inheritance;
- premature optimisation;
- magic global state;
- hard-coded user information;
- hidden side effects.

Optimise for readability, learning, and maintainability.

---

# 27. Dependency Discipline

Add dependencies only when they provide clear value.

Before adding a dependency, check whether:

- the standard library;
- FastAPI/Pydantic;
- React ecosystem already in use;
- an existing project dependency

already solves the problem.

Keep `pyproject.toml` and `package.json` clean.

---

# 28. Human-in-the-Loop

The application may:

- research;
- analyse;
- rank;
- recommend;
- draft;
- prepare application materials.

Do not autonomously submit applications, send messages, or perform external account actions unless such functionality is explicitly introduced with user control and appropriate authorization.

---

# 29. Coding-Agent Behaviour

When modifying this repository:

1. inspect relevant code before editing;
2. follow this file;
3. preserve working behaviour unless intentionally changing it;
4. prefer the smallest coherent change;
5. do not rewrite unrelated code;
6. add/update tests for behavioural changes;
7. run relevant tests;
8. fix failures caused by the change;
9. update documentation when user-facing behaviour changes;
10. keep the repository runnable;
11. do not introduce candidate-specific assumptions into shared logic;
12. consider multi-user security implications for every new data path.

If multiple approaches are reasonable, prefer the one that is:

- simple;
- testable;
- understandable;
- secure;
- replaceable later.

---

# 30. Definition of Done

A feature is complete when:

- it works;
- user ownership is enforced where relevant;
- schemas are typed;
- obvious errors are handled;
- relevant tests pass;
- documentation reflects the change;
- no secrets are committed;
- the repository remains runnable;
- no candidate-specific data has leaked into shared application logic.

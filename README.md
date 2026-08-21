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

See:

`docs/ARCHITECTURE.md`

for the detailed design.

---

# Multi-User Design

Career Agent is designed for external users.

Each registered user owns their own:

- candidate profile;
- career evidence;
- skills;
- projects;
- uploaded CVs;
- preferences;
- saved jobs;
- assessments;
- application materials;
- application history.

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
|   `-- system.md
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

`resources/` contains development resources, templates, and demo fixtures.

It does **not** contain production registered-user data.

Example:

```text
resources/
|-- templates/
`-- examples/
    `-- ray_demo/
        |-- 01_candidate_profile.md
        |-- 02_master_career_evidence.md
        |-- ...
```

Demo profiles exist only for:

- development;
- testing;
- evaluation;
- examples.

They must never be treated as globally applicable candidate information.

---

# Core Concepts

## Candidate Profile

A structured representation of the authenticated user's:

- employment;
- education;
- skills;
- projects;
- achievements;
- preferences;
- career targets.

## Career Evidence

Atomic evidence supporting claims about candidate capability.

Example:

```text
Requirement:
Customer-facing technical project delivery

Evidence:
Led multiple industrial engineering projects from
requirements capture through technical delivery.
```

## Job Profile

Structured representation of a job including:

- title;
- company;
- location;
- responsibilities;
- essential requirements;
- desirable requirements;
- technical skills;
- seniority;
- eligibility constraints.

## Fit Assessment

Measures how well the user's current evidence matches the role.

## Career Assessment

Measures whether the role moves the user in their preferred strategic direction.

These two concepts remain separate.

---

# Planned Development Stages

## V1 — Web Foundation

- FastAPI backend
- React frontend
- user registration
- login/logout
- authenticated current-user endpoint
- basic profile CRUD

## V2 — Profile Intelligence

- CV upload
- CV parsing
- structured candidate profile
- evidence extraction
- user review/editing

## V3 — Job Analysis

- job-description input
- job URL input
- `JobProfile`
- structured requirements

## V4 — Matching

- requirement/evidence matching
- fit score
- gap classification
- explainable results

## V5 — Career Strategy

- career-alignment score
- APPLY / CONSIDER / SKIP recommendation

## V6 — Application Preparation

- evidence selection
- CV tailoring
- cover-letter drafting

## V7 — Application Tracking

- saved jobs
- application status
- events
- outcomes
- notes

## V8 — Job Discovery

- recurring searches
- deduplication
- filtering
- scoring
- shortlist generation

## V9 — Learning System

Use application outcomes to improve prioritisation and evaluation.

---

# Technology Direction

## Frontend

- React
- TypeScript
- modern component-based UI

## Backend

- Python
- FastAPI
- Pydantic

## Database

Development may start simply.

Production target:

- PostgreSQL

## File Storage

Uploaded CVs and documents should eventually use appropriate private file/object storage.

## AI

Architecture should support multiple model providers.

Likely uses include:

- job extraction;
- semantic matching;
- evidence retrieval;
- gap analysis;
- career reasoning;
- CV tailoring;
- cover-letter generation.

## Workflow

LangGraph may be introduced where stateful multi-step orchestration adds value.

---

# Security

Security is a core product requirement because the application stores private career information.

Important principles:

- all user-owned data is scoped to authenticated users;
- passwords are never stored in plaintext;
- secrets are never committed;
- authorization is enforced server-side;
- uploaded documents are private;
- demo data is never mixed with production user data.

---

# Current Status

Repository architecture and shared instructions are being established.

The application implementation is not yet complete.

Do not assume roadmap features already exist.

---

# Development Guidance

Repository-wide coding instructions are defined in:

`AGENTS.md`

Before major implementation changes, follow those instructions.

---

# Demo Data

The repository may contain example candidate profiles under:

`resources/examples/`

These are only fixtures for development and evaluation.

The application itself must work equally well for users with very different backgrounds and goals.

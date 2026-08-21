# Career Agent Architecture

## 1. Purpose

This document describes the target architecture for Career Agent, a multi-user AI-powered job-search and career application web platform.

The architecture should evolve incrementally. This document may describe future components that are not yet implemented.

The `README.md` should always distinguish implemented behaviour from planned behaviour.

---

# 2. Primary Architectural Goals

The system should be:

- multi-user;
- secure;
- user-agnostic;
- modular;
- testable;
- explainable;
- provider-independent where practical;
- suitable for local development;
- capable of evolving into a deployed web application.

---

# 3. System Overview

```text
                      Internet
                          |
                          v
                 +----------------+
                 | React Frontend |
                 +--------+-------+
                          |
                       HTTPS
                          |
                          v
                 +----------------+
                 | FastAPI Backend|
                 +--------+-------+
                          |
          +---------------+----------------+
          |               |                |
          v               v                v
   Authentication    Career Services   Job Services
          |               |                |
          +---------------+----------------+
                          |
                          v
                  Workflow / Agent Layer
                          |
        +-----------------+-----------------+
        |                 |                 |
        v                 v                 v
      LLMs            Web tools         Retrieval
        |
        v
  Structured outputs

                          |
                          v
                     Persistence
              +-----------+-----------+
              |                       |
              v                       v
          PostgreSQL             File storage
```

---

# 4. User Model

The system is built around authenticated users.

Each user should have an immutable internal identifier:

```text
user_id
```

All user-owned entities must be associated with this identifier.

Examples:

```text
User
 |
 +-- CandidateProfile
 +-- CareerEvidence[]
 +-- Skills[]
 +-- Projects[]
 +-- UploadedDocuments[]
 +-- JobPreferences
 +-- SavedJobs[]
 +-- JobAssessments[]
 +-- Applications[]
```

Never rely on email address as the only long-term ownership key.

---

# 5. Authentication Flow

Initial conceptual flow:

```text
Register
   |
   v
Create account
   |
   v
Hash password securely
   |
   v
Persist user
   |
   v
Login
   |
   v
Verify credentials
   |
   v
Create authenticated session/token
   |
   v
Access protected API
```

The exact implementation may evolve.

Possible approaches:

- secure JWT access/refresh tokens;
- secure server-side sessions;
- managed identity provider.

Do not implement custom cryptographic algorithms.

---

# 6. Authorization

Every protected backend operation must validate resource ownership.

Example:

```text
GET /api/v1/jobs/{job_id}
```

The backend must verify:

```text
job.user_id == current_user.id
```

before returning the record.

The frontend hiding a button is not authorization.

Authorization must be enforced server-side.

---

# 7. Candidate Onboarding

Suggested onboarding workflow:

```text
User registers
      |
      v
Upload CV
      |
      v
Extract text
      |
      v
LLM / parser creates structured candidate data
      |
      v
User reviews and corrects
      |
      +--> employment
      +--> education
      +--> skills
      +--> achievements
      +--> projects
      |
      v
Career preferences
      |
      +--> target roles
      +--> industries
      +--> location
      +--> work arrangement
      +--> compensation
      +--> career goals
      |
      v
Profile ready
```

AI-extracted data should not silently become authoritative.

The user should be able to review and edit important profile fields.

---

# 8. Candidate Data Model

Possible domain entities:

## User

```text
id
email
password_hash / identity_provider_id
created_at
updated_at
status
```

## CandidateProfile

```text
id
user_id
headline
summary
current_role
location
years_experience
career_goal
created_at
updated_at
```

## Employment

```text
id
user_id
profile_id
organisation
role
start_date
end_date
description
```

## Education

```text
id
user_id
profile_id
institution
qualification
field
dates
```

## Skill

```text
id
user_id
name
level
classification
notes
```

## CareerEvidence

```text
id
user_id
title
organisation
situation
actions
results
skills
evidence_type
```

## Project

```text
id
user_id
title
description
skills
results
```

Production schemas may normalise some of these fields differently.

---

# 9. Job Data Model

## Job

```text
id
user_id
source_url
title
company
location
raw_text
created_at
```

## JobRequirement

```text
id
job_id
category
text
importance
source_text
```

## RequirementMatch

```text
id
user_id
job_id
requirement_id
match_type
score
reasoning
evidence_ids
```

## FitAssessment

```text
id
user_id
job_id
fit_score
score_components
major_strengths
major_gaps
hard_blockers
```

## CareerAssessment

```text
id
user_id
job_id
career_alignment_score
confidence
dimensions[]
strategic_strengths
strategic_tradeoffs
reasoning
```

Career Alignment V1 is implemented as a provider-independent semantic agent followed
by deterministic validation and weighted aggregation. It consumes `JobProfile`,
`CandidateContext`, and `FitAssessment`, but never changes or derives its score from
the fit score. Missing strategy or preference information caps confidence instead of
causing the system to invent goals. The current implementation is integrated into the
development demo workflow; production persistence remains future work.

## RecommendationAssessment

```text
recommendation
fit_score
career_alignment_score
career_alignment_confidence
rule_id
reasoning
key_strengths
key_tradeoffs
hard_blockers
```

Recommendation V1 is deterministic Python. Ordered rules give hard blockers explicit
precedence, support both strong-fit and deliberate strategic-stretch APPLY cases,
preserve uncertain cases as CONSIDER, and reserve SKIP for blockers or explicitly low
score combinations. The service copies the two source scores without blending or
mutating them. Thresholds are central and injectable for later calibration.

---

# 10. Application Tracking Model

Possible entities:

## Application

```text
id
user_id
job_id
status
applied_at
next_action
notes
```

## ApplicationEvent

```text
id
user_id
application_id
event_type
event_date
notes
```

Possible statuses:

```text
discovered
reviewing
shortlisted
preparing
applied
interview
rejected
offer
withdrawn
archived
```

---

# 11. Backend Layering

Recommended structure:

```text
backend/app/
|-- api/
|-- core/
|-- models/
|-- schemas/
|-- services/
|-- agents/
`-- workflows/
```

## API Layer

Responsible for:

- HTTP;
- authentication dependencies;
- request validation;
- authorization checks;
- typed responses.

Avoid embedding substantial domain reasoning here.

## Core

Responsible for:

- configuration;
- security helpers;
- database infrastructure;
- logging;
- shared dependencies.

## Models

Persistence/database models.

## Schemas

Pydantic API/domain schemas.

## Services

Domain and integration services such as:

```text
ProfileService
JobService
AssessmentService
ApplicationService
DocumentService
```

## Agents

LLM-powered semantic components such as:

```text
JobExtractionAgent
RequirementMatcher
CareerAssessmentAgent
CVTailoringAgent
```

## Workflows

Stateful multi-step processes.

LangGraph may be used here where beneficial.

---

# 12. Frontend Architecture

Target:

- React;
- TypeScript.

Potential areas:

```text
frontend/src/
|-- api/
|-- components/
|-- features/
|-- hooks/
|-- pages/
|-- routes/
|-- types/
`-- utils/
```

Potential feature areas:

```text
auth
onboarding
profile
jobs
assessments
applications
settings
```

Do not duplicate backend domain rules in the frontend.

---

# 13. API Surface

Possible initial API:

## Authentication

```text
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/users/me
```

## Profile

```text
GET    /api/v1/profile
POST   /api/v1/profile
PATCH  /api/v1/profile
```

## Documents

```text
POST /api/v1/documents/cv
GET  /api/v1/documents
```

## Jobs

```text
POST /api/v1/jobs
GET  /api/v1/jobs
GET  /api/v1/jobs/{job_id}
```

## Analysis

```text
POST /api/v1/jobs/{job_id}/analyse
GET  /api/v1/jobs/{job_id}/assessment
```

## Applications

```text
POST  /api/v1/applications
GET   /api/v1/applications
PATCH /api/v1/applications/{application_id}
```

These are design suggestions, not mandatory final routes.

---

# 14. Job Analysis Workflow

```text
Job URL or text
      |
      v
Fetch / clean content
      |
      v
Extract JobProfile
      |
      v
Extract requirements
      |
      v
Load authenticated user's profile
      |
      v
Retrieve relevant evidence
      |
      v
Match requirements
      |
      v
Aggregate fit score
      |
      v
Analyse career alignment
      |
      v
Apply deterministic recommendation rules
```

---

# 15. Agent State

Potential workflow state:

```python
class CareerState(TypedDict):
    user_id: str
    job_id: str | None
    job_profile: dict | None
    candidate_profile: dict | None
    requirement_matches: list[dict]
    fit_score: float | None
    career_alignment_score: float | None
    gaps: list[dict]
    recommendation: str | None
```

State should contain the user identifier, but prompts should receive only the user data required for the current task.

---

# 16. Evidence Retrieval

Early versions may use:

- structured SQL queries;
- keyword search;
- lightweight semantic matching.

A vector database should be added only if the evidence volume and retrieval quality justify it.

Conceptually:

```text
Job requirement
      |
      v
Candidate evidence search
      |
      v
Top relevant evidence
      |
      v
Semantic match judgement
```

All retrieval must be restricted to the current user.

---

# 17. LLM Boundary

LLMs should handle semantic tasks.

Examples:

- requirement extraction;
- semantic match judgement;
- transferable-skill reasoning;
- gap interpretation;
- career-alignment reasoning;
- CV rewriting.

LLMs should not determine:

- user authorization;
- database ownership;
- password validation;
- final deterministic score aggregation;
- security policy.

---

# 18. Scoring

Example conceptual fit model:

```text
Essential requirements     30%
Technical skills           20%
Relevant experience        15%
Transferable experience    10%
Career/domain fit           10%
Customer/leadership fit     5%
Location/eligibility        5%
Seniority                   5%
```

Exact weights should be tested and calibrated.

The system should store:

- component scores;
- evidence;
- reasons;
- gaps.

Do not return an unexplained score.

---

# 19. Career Alignment

Career alignment is user-specific.

It should depend on the user's own:

- target roles;
- industries;
- desired capabilities;
- career objectives;
- preferences;
- constraints.

No universal career strategy should be hard-coded.

---

# 20. Data Storage

## Development

Early development may use SQLite where practical.

## Production

Target:

- PostgreSQL for structured records;
- private object/file storage for CVs and documents.

Database rows containing user-owned information should include a `user_id` where appropriate.

---

# 21. File Uploads

Uploaded documents should eventually support:

- file-type validation;
- size limits;
- private storage;
- secure retrieval;
- malware/security controls appropriate to deployment;
- ownership enforcement;
- deletion.

Do not expose raw storage URLs publicly by default.

---

# 22. Demo Resources vs Production Data

Repository:

```text
resources/
|-- templates/
`-- examples/
```

Production:

```text
database
private file storage
```

The two must remain conceptually separate.

`resources/examples/ray_demo/` is a development/evaluation fixture only.

---

# 23. Deployment Evolution

Possible stages:

## Stage 1

```text
React local dev server
FastAPI local server
SQLite
```

## Stage 2

```text
React build
FastAPI container
PostgreSQL
Docker Compose
```

## Stage 3

Cloud deployment:

```text
CDN/static frontend
API service
managed PostgreSQL
private object storage
managed secrets
monitoring
```

The exact cloud provider should remain replaceable where practical.

---

# 24. Security Requirements

At minimum, production design should address:

- secure password hashing;
- secure cookies/tokens;
- HTTPS;
- authorization;
- CSRF where applicable;
- CORS configuration;
- rate limiting;
- brute-force mitigation;
- private document access;
- validation;
- SQL-injection prevention;
- secure secret management;
- auditability;
- account deletion/export considerations.

---

# 25. Privacy Principles

Use the minimum user data necessary.

Do not:

- use one user's career data to benefit another user without explicit design and consent;
- expose private data in logs;
- send unnecessary private content to model providers;
- retain data indefinitely without a reason.

Future production development should include explicit retention and deletion policies.

---

# 26. Testing Strategy

## Backend unit tests

```text
backend/tests/
```

## Cross-stack / integration / E2E

```text
tests/
```

Critical tests include:

- User A cannot access User B's profile.
- User A cannot access User B's job.
- User A cannot access User B's application.
- unauthenticated requests cannot access protected endpoints;
- score aggregation is deterministic;
- malformed LLM output is rejected/handled.

---

# 27. Future Components

Potential later components include:

- recurring job discovery;
- email notifications;
- browser extension;
- company intelligence;
- interview preparation;
- analytics;
- application outcome learning;
- subscription/billing;
- team/admin tooling.

These should not complicate V1 prematurely.

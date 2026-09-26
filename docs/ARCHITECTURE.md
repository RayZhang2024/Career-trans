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

CV ingestion stores extracted source text and provenance metadata, not the uploaded binary. `GET /api/v1/cv-ingestion?limit=...` lists at most 50 newest source records for the authenticated user without returning segment text; the existing detail read returns extracted segments and remains read-only. These reads do not resolve an AI provider or mutate canonical profile state. Historical source detail is kept separate from the onboarding latest-draft workflow in the frontend. Confirming a review writes the structured profile, reconciles active evidence, and transitions the draft to confirmed in one database transaction; failures roll the whole confirmation back. A previously confirmed structured profile remains usable even when no source draft was retained, and users are not forced to upload again.

AI-extracted data should not silently become authoritative.

Current structured career information can be established by confirming a CV
review or a manual Profile revision. Candidate-authored Adviser intake is a
separate authority. A bounded, review-ready Adviser assessment must be explicitly
confirmed and remain non-stale before it enriches discovery and career-alignment
context. It never becomes `CareerEvidence` or enters the evidence-limited
requirement-matching profile.

The user should be able to review and edit important profile fields.

### Current candidate read snapshot

Authenticated reads compose a non-persisted typed snapshot from the existing
`CandidateProfile`, `CandidateStructuredProfile`, user-owned active
`CareerEvidence`, Adviser intake, eligibility and only confirmed, current
Adviser assessment content. The authenticated `GET /api/v1/profile/snapshot`
endpoint exposes that snapshot to backend clients, including the authenticated
Profile view. The UI renders the snapshot's separate domains without
assembling another candidate authority. Reads are provider-free and do not
reconcile, flush or commit records. Raw
CV source text and generated application materials are excluded.

The snapshot reports structured-profile readiness and evidence materialisation
counts. `incomplete` means one or more facts currently derived from the
confirmed structured profile or confirmed factual clarifications either lack a
persisted evidence row or have a matching row whose canonical fingerprint,
skills, provenance, or other materialised metadata no longer matches what the
current resolver would write. Candidate-context consumers fail closed for that
state instead of treating partial or stale evidence as complete. The state is
visible for later legacy repair planning; this version does not backfill it or
introduce a second persisted candidate store. `not_applicable` is used when
there is no confirmed structured profile, such as a profile-only account; a
structured profile with zero derived evidence claims is complete with zero
expected rows.

### Manual Profile revision proposals and confirmation (Issue #207 Phases 1–4)

Manual Profile changes are persisted in a separate `CandidateProfileRevisionRecord` proposal. Its scalar CandidateProfile fields and editable structured career sections remain separate from the current authorities. A null proposed authority means that authority is excluded from the revision; deletion is not supported, and a present all-null scalar object can establish an empty Profile authority. The editable structured schema omits `CandidateCVData.evidence`, so manual drafts cannot author or rewrite source-grounded evidence or CV provenance. Creation copies current editable values when present and records deterministic, absence-aware fingerprints for the scalar and full structured authorities (the latter includes evidence). A portable nullable unique active-user slot enforces one `draft`/`review_ready` revision per user. Versioned save, review, discard, and confirmation operations are provider-free. Review and confirmation check only the baselines for authorities changed by the proposal; confirmation rechecks the full structured baseline, promotes changed authorities in one transaction, preserves semantic evidence, and reconciles active evidence through `ActiveCandidateEvidenceResolver`. A no-change review confirms as an idempotent no-op. Pending proposals are excluded from the canonical snapshot. Public Profile POST/PATCH routes reject writes so canonical Profile changes pass through confirmation.

The canonical snapshot contains current confirmed Profile and structured career information only; pending manual and CV drafts do not enter `CandidateContext`. CV confirmation remains a whole-structured-information replacement after explicit confirmation, while scalar Profile details, Adviser intake/preferences, and eligibility remain separate. Existing Adviser input fingerprints determine whether structured changes make an assessment stale. Discovery fingerprints change naturally when their candidate-context inputs change; identity-only changes do not. New application preparations use the current confirmed snapshot, while persisted discovery evaluations and application preparations remain historical and immutable.

### Candidate Adviser structured Profile proposals (Issue #208 Phases 1–4)

Clarification confirmation remains provider-free and immediately reconciles confirmed career/mixed clarification evidence through `ActiveCandidateEvidenceResolver`. A separate explicit `POST /api/v1/candidate-adviser/clarifications/{clarification_id}/profile-proposals` action may generate typed suggestions from the clarification's validated affirmative `proposed_evidence` and a bounded current structured Profile target catalogue. The structured Profile is target context only; it is not a source for new factual claims. The catalogue is limited to the first ten items in canonical order per editable section, records which sections were truncated, and excludes `CandidateCVData.evidence`. Provider output is strict-schema validated and exact replacement fingerprints are rechecked against current state after generation. The entire batch is materialized atomically as pending proposal records, retaining deterministic idempotency, immutable original updates, and separately editable proposal updates. These records remain noncanonical until an explicit transfer creates a normal Issue #207 Profile revision.

The Adviser page exposes this lifecycle separately from assessment and clarification loading: users explicitly generate suggestions from a confirmed career/mixed clarification, review and edit the typed item, then reject it or send it to the Profile workflow. Proposal history remains inspectable across Adviser assessment changes. Transferred proposals remain historical records; the Profile page owns review and confirmation of the linked draft, and transfer itself does not change current Profile data.

### Structured-item lineage and source overlap (Issue #209 Phases 1–3)

`CandidateStructuredProfile.structured_json` remains the sole authority for current structured career information. The additive `CandidateStructuredItemLineageRecord` table stores prospective, immutable source/history snapshots keyed to an exact typed-item fingerprint; a lineage row never makes an item current, and legacy current items do not receive fabricated source records. `StructuredProfileComparisonService` is a provider-free, non-mutating classifier for new, reinforcement, refinement, conflict, and ambiguous comparisons. Successful CV confirmations and structured manual or Adviser-originated Profile revision confirmations prospectively stage lineage in the same transaction as canonical changes, evidence reconciliation, and source terminal state. CV confirmations attribute every adopted item from the reviewed complete source; Profile revisions attribute only resulting changed item occurrences, with Adviser suggestions retaining proposal source identity when the same fact survives.

Phase 3 classifies source overlap before mutation. CV refinements, conflicts, and ambiguous overlaps require persisted explicit choices tied to fingerprints of both the current structured authority and reviewed draft; a changed authority or edited draft invalidates those choices. Exact and normalized reinforcement leave one canonical item, and normalized-equivalent duplicates inside one CV block confirmation until the draft is edited. Manual Profile revisions continue through the existing #207 review/confirm boundary, while same-fact duplicate growth is rejected and typed comparison metadata is exposed on the revision read. Adviser `add` reinforcement transfers without appending a duplicate; other overlapping `add` suggestions stay pending until explicitly retargeted. Exact-target Adviser replacement remains available. Canonical reads still exclude lineage, no historical rows are rewritten, and no frontend conflict-resolution UI is implemented yet.

Phase 4A adds a separate provider-free read projection for current structured Profile provenance. Its source descriptors resolve prospective lineage records but do not enter canonical snapshots or downstream fingerprints. A pending ambiguous Adviser `add` may persist an explicit, current-authority-bound human `add_as_new` choice before transfer. No frontend conflict or provenance UX is implemented yet.

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

## External Discovery Verification

External runtime output is a bounded, non-authoritative discovery lead rather
than a rankable advert. Career-trans retains its safe provenance, then uses
deterministic provider-aware verification for recognized Greenhouse, Lever,
Ashby, and recoverable Workday URLs. A verified provider identity and usable
employer detail produce the canonical actionable `JobListing`; unresolved,
stale, and provider-detail failures remain unverified diagnostics and do not
enter semantic or deep ranking.

Persisted detail is monotonic by authority:

```text
verified employer detail
    > provider detail
    > external discovery summary
```

A weaker subsequent discovery summary therefore cannot overwrite a richer
verified description. External omissions remain non-authoritative lifecycle
evidence and never make prior jobs inactive.

## User-effective semantic runtime preferences (V1A)

Deployment configuration remains the provider and fallback source. An
authenticated user may store provider-scoped OpenAI model/reasoning preferences
through the credential-free AI settings API. A pure immutable runtime snapshot
resolves each logical operation once per workflow; semantic request clients are
request/run-scoped and do not share user-effective configuration through global
caches. Purpose-specific runtime projections, rather than the preference row
revision, participate in evaluation and application-preparation fingerprints.
Unset reasoning effort means provider default and is omitted from OpenAI
requests. Ollama deployment behavior is retained, but V1A does not expose
arbitrary local-model selection. Host-side Codex discovery remains separate.

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

Requirement matching is fail-closed. The semantic matcher receives canonical
requirement indexes and bounded authenticated `CareerEvidence` only; Python
rejects missing, duplicate, out-of-range, or unknown-evidence references and
reattaches the canonical requirements deterministically. Structural output
failures may use the bounded application retry, while provider/configuration
failures do not retry. If matching still fails, ranking exposes only the safe
`requirement_matching: <kind>` category—never prompts, evidence text, raw
provider output, or credentials—and continues analysing other finalists.

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

## Stage 2 — Local Compose packaging

```text
Production React static build + nginx same-origin `/api/*` proxy
FastAPI container
SQLite named volume for local developer persistence
Docker Compose
```

This is local developer packaging, not a production database migration. A
later production/deployment stage may introduce PostgreSQL, managed secrets,
and deployment controls independently of this Compose stack.

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

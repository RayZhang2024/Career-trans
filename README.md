# Career-trans

Career-trans is a multi-user AI-assisted job-search and career-transition platform.

It combines candidate understanding, job discovery, vacancy verification, evidence-based fit assessment, career-strategy assessment, and deterministic application recommendations.

The system is designed for users with different backgrounds, goals, locations, constraints, and job-search strategies. Demo profiles are development fixtures only and must never become global candidate assumptions.

---

## What Career-trans Does Today

The current backend supports an end-to-end workflow from confirmed candidate information to ranked job opportunities:

```text
Register / login
      |
      v
Create profile + career direction
      |
      v
Upload CV documents
      |
      v
Semantic CV interpretation
      |
      v
Human review / correction / confirmation
      |
      v
Confirmed structured profile + CareerEvidence
      |
      +--> optional Candidate Adviser intake
      |       |
      |       v
      |    review-ready adviser assessment
      |       |
      |       v
      |    explicit confirmation
      |
      v
Candidate search / career / matching context
      |
      v
Discover jobs
      |
      +--> structured ATS scans
      +--> external Codex discovery
      +--> optional in-process agentic web discovery
      |
      v
Verify / normalize / deduplicate / persist vacancies
      |
      v
Bounded semantic relevance screening
      |
      v
Deep career analysis for top finalists
      |
      +--> extract job requirements
      +--> match requirements to candidate evidence
      +--> deterministic fit score
      +--> career-alignment assessment
      +--> deterministic APPLY / CONSIDER / SKIP
      |
      v
Ranked opportunities + diagnostics
```

Career-trans does **not** submit job applications automatically.

---

# Core Product Questions

For each role the system keeps two questions separate:

1. **Can this candidate realistically obtain and perform the role?**
2. **Should this candidate pursue the role given their longer-term career direction?**

The first becomes the **fit assessment**. The second becomes the **career-alignment assessment**.

The final recommendation preserves both rather than collapsing everything into one opaque AI score.

---

# Candidate Understanding

## Authentication and profile

Implemented backend capabilities include:

- email/password registration and login;
- JWT-protected authenticated endpoints;
- user-scoped candidate profile CRUD;
- career goal and job-search criteria;
- current-user candidate-context readiness summaries.

Production candidate data is scoped by authenticated user identity.

## CV ingestion

CV ingestion is implemented as a reviewable workflow:

```text
upload
  -> extract text / structured input
  -> semantic interpretation where needed
  -> merge
  -> review-ready draft
  -> user edit/correction
  -> explicit confirmation
```

Confirmed CV data is persisted as:

- a structured candidate profile;
- atomic `CareerEvidence` records with stable IDs and source provenance.

AI-extracted CV content does not silently become authoritative before confirmation.

## Candidate Adviser

Career-trans also supports a separate Candidate Adviser layer for information that a CV alone may not capture well, such as:

- career direction;
- work preferences;
- constraints;
- motivations;
- self-assessment;
- trade-offs;
- eligibility information.

The adviser can produce review-ready insights including professional positioning, transferable strengths, development gaps, role hypotheses, transition assessment, career strategy, job-search strategy, and open questions.

Adviser assessments must be explicitly confirmed and become stale when their underlying inputs change.

A critical evidence boundary is preserved:

```text
confirmed CV / CareerEvidence
        -> requirement matching

candidate-authored intake + confirmed adviser interpretation
        -> search strategy and career-alignment context
```

Adviser interpretation never becomes `CareerEvidence` and cannot be used to prove that a candidate satisfies a job requirement.

---

# Job Discovery

Career-trans now supports multiple discovery paths.

## Structured ATS discovery

Known company career sources can be resolved, persisted, and scanned deterministically.

Current structured ATS adapters include:

- Greenhouse;
- Ashby;
- Lever;
- SmartRecruiters;
- Recruitee.

Structured ATS discovery performs collection, factual screening, deduplication, bounded candidate selection, lifecycle persistence, and source diagnostics without using semantic models for collection.

## External Codex discovery

The CLI can use a local Codex runtime for broader employer-agnostic public-web discovery.

Career-trans provides only a compact candidate search profile and search query. Returned jobs are treated as non-authoritative discovery leads until verified.

## In-process agentic web discovery

An optional backend discovery path can use configured web-search providers and semantic vacancy extraction. Supported search-provider configuration currently includes OpenAI web search and Brave, with an explicit disabled mode.

## Vacancy verification

External search results are not automatically trusted as rankable jobs.

The verification path is:

```text
external lead
    -> provider/current-vacancy verification
    -> canonical identity
    -> employer/provider detail retrieval
    -> verified actionable listing
```

Provider-aware external verification currently supports recognized:

- Greenhouse;
- Lever;
- Ashby;
- recoverable Workday vacancy URLs.

If a current vacancy and usable employer-provided detail cannot be verified, the lead remains unverified and is excluded from semantic/deep ranking.

Detail authority is monotonic:

```text
verified employer detail
    > provider detail
    > external discovery summary
```

A later short search summary therefore cannot overwrite a richer verified advert.

## Persisted job universe

Discovered jobs and provenance are persisted so searches are not treated as isolated one-off lists.

The backend tracks lifecycle states such as new, updated, unchanged, and inactive where the source provides authoritative lifecycle evidence.

Bounded external-search omission is not treated as proof that a vacancy has closed.

An authenticated opportunity-inbox endpoint allows inspection of recent persisted discoveries without launching another search or ranking run.

---

# Ranking Funnel

Career-trans uses a cost-aware funnel instead of deeply analysing every discovered vacancy.

```text
verified / actionable jobs
        |
        v
hard factual gate
        |
        v
bounded semantic relevance screening
        |
        v
role-archetype classification
        |
        v
relevance-qualified finalists
        |
        v
bounded deep career analysis
```

The current CLI `jobs hunt` defaults are:

- maximum 10 jobs submitted to bounded semantic screening;
- maximum 5 relevance-qualified jobs sent to deep analysis.

These limits are configurable.

Candidate selection is spread across company/source fronts rather than simply consuming the budget from the first source returned.

---

# Deep Career Analysis

Deep analysis is orchestrated with LangGraph:

```text
extract_job
    -> match_requirements
    -> assess_fit
    -> assess_career_alignment
    -> build_recommendation
```

## Job extraction

Employer job text is converted into a typed `JobProfile` containing structured role information and atomic job requirements.

Known factual listing metadata from discovery remains authoritative for overlapping fields such as title, company, location, work arrangement, and employment type.

## Requirement matching

Requirement matching is evidence-first and fail-closed.

For semantic requirements, the matcher receives:

- canonical requirement indexes;
- a bounded candidate matching profile;
- authenticated `CareerEvidence` only.

Application code rejects malformed outputs including:

- missing or duplicate requirement indexes;
- out-of-range indexes;
- the wrong number of requirement matches;
- unknown or invented evidence IDs.

The model cannot rewrite canonical job requirements.

Factual eligibility-style requirements remain separated from semantic capability matching.

Structural output failures may use bounded application retry. Provider/configuration failures do not retry indefinitely.

If requirement matching still fails, ranking exposes only a safe failure category such as:

```text
requirement_matching: wrong_match_count
```

Prompts, candidate evidence text, credentials, and raw private provider payloads are not surfaced in ranking diagnostics.

A failure for one finalist does not suppress successful analysis of other finalists.

---

# Fit Assessment

Fit scoring is deterministic Python over validated requirement matches.

Current importance weights are:

```text
essential     3.0
unspecified   2.0
desirable     1.0
```

The fit layer reports:

- aggregate fit score;
- essential/desirable scores where applicable;
- strengths;
- evidence gaps;
- learnable gaps;
- meaningful capability gaps;
- confirmed hard blockers.

An ordinary missing skill is not automatically a hard blocker. Hard blockers are reserved for confirmed incompatibilities with clearly mandatory constraints such as work authorization, nationality/security restrictions, or required professional licences/registration.

---

# Career Alignment

Career alignment is intentionally separate from current-role fit.

The semantic career-alignment agent evaluates six dimensions, which Python validates and aggregates with centrally defined weights:

```text
target role              25%
capability growth        25%
industry / domain        15%
seniority progression    15%
long-term optionality    15%
preferences / constraints 5%
```

Career alignment uses the candidate's own strategy, preferences, constraints, and confirmed adviser context where available.

Missing strategic information reduces confidence rather than causing the model to invent career goals.

---

# Recommendation

The final `APPLY`, `CONSIDER`, or `SKIP` recommendation is deterministic Python.

Examples of current rule behavior include:

- confirmed hard blocker -> `SKIP`;
- very low fit -> `SKIP`;
- strong fit + positive career alignment -> `APPLY`;
- credible fit + very strong career alignment -> strategic-stretch `APPLY`;
- low career-alignment confidence can downgrade an otherwise apply-worthy case to `CONSIDER`;
- mixed or borderline cases -> `CONSIDER`.

The final recommendation preserves the original fit and career-alignment scores and does not ask another model to make the final decision.

---

# Interfaces Available Today

## FastAPI backend

The backend currently exposes API areas for:

- authentication;
- current user;
- candidate profile;
- semantic LLM configuration inspection;
- CV ingestion;
- Candidate Adviser;
- job analysis;
- requirement matching;
- structured ATS discovery;
- external-discovery search context and import;
- opportunity inbox;
- job-detail enrichment;
- ATS/company-source resolution;
- employer-universe construction;
- agentic discovery;
- job ranking;
- discover-and-rank workflows;
- development/demo analysis.

Swagger is available locally at:

```text
http://127.0.0.1:8000/docs
```

## CLI

The `career-trans` CLI currently supports workflows including:

```text
career-trans auth login
career-trans config show
career-trans config check
career-trans profile show
career-trans profile context-summary
career-trans profile set-strategy
career-trans cv upload
career-trans cv interpret
career-trans cv show
career-trans cv edit
career-trans cv confirm
career-trans jobs discover-external
career-trans jobs discover-ats
career-trans jobs list
career-trans jobs enrich-imported
career-trans jobs rank-imported
career-trans jobs hunt
```

`jobs hunt` combines known ATS discovery, optional local Codex broad discovery, cross-channel deduplication, lifecycle selection, bounded semantic screening, and bounded deep analysis.

---

# Architecture

Current implementation is backend-first:

```text
CLI / API clients
       |
       v
FastAPI
       |
       +--> authentication / authorization
       +--> candidate-profile services
       +--> CV ingestion
       +--> Candidate Adviser
       +--> job-source discovery
       +--> vacancy verification / persistence
       +--> semantic screening
       +--> LangGraph career-analysis workflow
       |
       +--> LLM provider abstractions
       +--> web-search/page-fetch abstractions
       |
       v
SQLAlchemy persistence
```

Current repository structure:

```text
career-trans/
|
|-- AGENTS.md
|-- README.md
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
|   `-- tests/
|-- docs/
|-- prompts/
`-- resources/
```

A React/TypeScript frontend is part of the product direction but is **not yet implemented in the repository**.

See `docs/ARCHITECTURE.md` for detailed architectural design and future direction.

---

# AI vs Deterministic Logic

Career-trans deliberately separates semantic reasoning from deterministic policy.

LLMs currently help with tasks such as:

- CV semantic interpretation;
- Candidate Adviser assessment;
- job extraction;
- semantic requirement matching;
- career-alignment reasoning;
- job relevance screening;
- role-archetype classification;
- agentic search-strategy / vacancy extraction where configured.

Deterministic Python is responsible for tasks such as:

- authentication/authorization policy;
- user ownership boundaries;
- factual screening constraints;
- canonical job/evidence identity;
- deduplication and lifecycle persistence;
- structured-output validation;
- evidence-ID validation;
- fit aggregation;
- career-alignment aggregation;
- recommendation rules;
- semantic/deep-analysis budgets.

Automated backend tests use fake semantic/network components where appropriate and should not require live OpenAI/provider calls.

---

# Multi-User and Privacy Design

Career-trans is intended for external multi-user use.

Important principles include:

- user-owned candidate data is scoped to authenticated users;
- passwords are not stored in plaintext;
- candidate contexts are request-scoped rather than globally cached;
- matching evidence is restricted to the authenticated user's confirmed evidence;
- shared prompts/source code must not contain candidate-specific assumptions;
- demo data is never mixed with production registered-user data;
- external public-job records can be shared while private candidate data remains user-scoped;
- diagnostics should not leak candidate evidence, prompts, credentials, or private provider payloads.

---

# Storage and Deployment Status

Development currently uses SQLite.

Application startup uses SQLAlchemy `Base.metadata.create_all()` as a V1 convenience. This creates missing tables but does **not** safely migrate existing schemas. The code explicitly treats a proper versioned migration system such as Alembic as a production prerequisite.

Production direction remains:

- PostgreSQL for structured data;
- private file/object storage for uploaded documents;
- managed secrets;
- HTTPS;
- deployment monitoring and operational controls.

---

# Not Yet Implemented as a Complete Product Flow

The core candidate-understanding, discovery, verification, ranking, and recommendation engine exists, but Career-trans is not yet a complete end-user job-search product.

Major remaining product layers include:

- React/TypeScript web UI and onboarding;
- Candidate Adviser UI;
- saved shortlist and explicit user decisions;
- job-specific CV tailoring;
- cover-letter / application-answer generation;
- application status and event tracking;
- interview preparation;
- application-outcome learning;
- recurring scheduled discovery and notifications;
- production database migrations and deployment infrastructure.

These should build on the existing evidence, user-isolation, and deterministic-policy boundaries rather than bypass them.

---

# Development

From `backend/`, with the virtual environment active:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
uvicorn app.main:app --reload
```

The CLI can then target the running API, for example:

```powershell
career-trans --base-url http://127.0.0.1:8000 profile context-summary
```

---

# Demo Resources

`resources/` contains development resources, templates, examples, and evaluation fixtures.

Demo candidate profiles exist only for development/testing. They are not production registered-user data and must not be treated as globally applicable candidate information.

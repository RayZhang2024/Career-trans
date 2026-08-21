# AI Collaboration Model

This document refines the collaboration model in `AGENTS.md` with the explicit goal of reducing unnecessary Codex usage while preserving code quality and user learning.

The default model is now:

> **ChatGPT designs and reviews; Codex implements focused code slices; the user runs and validates the system locally.**

This document should be referenced by future AI feature issues and implementation handoffs.

---

## 1. Roles

### User — product owner and validation engineer

The user owns:

- feature priority and product decisions;
- local repository execution;
- full test-suite runs after implementation handoff;
- selected boundary tests and small test additions where practical;
- Swagger/API end-to-end testing;
- real job/candidate evaluation runs;
- inspection of generated outputs;
- merge approval;
- reporting runtime errors and unexpected behaviour.

The user does **not** need to implement complex production code unless they want to learn that part directly.

### ChatGPT — architect, spec author, reviewer, and lightweight maintainer

ChatGPT owns:

- milestone definition and decomposition;
- architecture and data-flow design;
- LLM vs deterministic-code boundaries;
- schemas and decision semantics;
- LangGraph state/routing design;
- prompt/evaluation strategy;
- acceptance criteria;
- PR/diff review;
- analysis of real runtime outputs supplied by the user;
- targeted GitHub changes such as documentation, prompts, small fixes, and collaboration/task metadata when useful.

ChatGPT should avoid handing Codex work that can reasonably be done through design, review, a tiny targeted edit, or user validation.

### Codex — focused implementation engineer

Codex should be used for work where repository-aware coding and local execution provide clear leverage, especially:

- coherent multi-file production-code implementation;
- non-trivial refactors;
- new framework/library integration;
- LangGraph graph implementation after the graph is already designed;
- database migrations;
- React/TypeScript implementation;
- complex dependency wiring;
- mechanical changes across many files.

Codex should **not** automatically own the entire milestone from specification through exhaustive testing and documentation.

---

## 2. Default Reduced-Codex Feature Workflow

Use this workflow unless the user explicitly requests otherwise:

```text
1. ChatGPT
   Define architecture, schemas, rules, acceptance criteria
          ↓
2. Codex
   Implement the smallest coherent production-code slice
   + minimal core tests needed to prove the implementation
          ↓
3. User
   Pull branch
   Run full test suite locally
   Run selected boundary/integration tests
   Exercise Swagger / real workflow
          ↓
4. ChatGPT
   Review PR + user runtime results
   Identify design/correctness issues
          ↓
5. Codex only if needed
   Implement non-trivial fixes
          ↓
6. User
   Re-test and merge
```

Do not ask Codex to perform steps 3–4 by default.

---

## 3. Testing Split

Testing is deliberately shared.

### Codex should normally do

- tests required to prove new production code imports and behaves basically correctly;
- one or a few happy-path unit tests;
- tests for dangerous regressions introduced directly by its change;
- enough testing to avoid handing the user obviously broken code.

### Codex should normally leave for the user

- the full existing test-suite run;
- broad threshold/boundary matrices unless risk justifies them;
- Swagger/manual API execution;
- real OpenAI-backed end-to-end runs;
- evaluation using real job adverts;
- inspection of generated reasoning quality;
- optional extra test cases used mainly for learning or calibration.

### ChatGPT should normally do

- specify which tests matter and why;
- inspect test design in the PR;
- identify missing high-value cases;
- give the user exact local commands/test cases to run;
- interpret failures and runtime outputs.

For security-sensitive code, authentication/authorization, migrations, destructive operations, or difficult-to-reproduce bugs, Codex may run a broader suite before handoff.

---

## 4. Documentation Split

Do not spend Codex usage on broad documentation updates by default.

Preferred ownership:

- ChatGPT: architecture notes, issue specifications, README wording, prompts, design rationale;
- Codex: only documentation that must change alongside implementation to avoid misleading developers/users;
- User: optional notes about manual validation or observed behaviour.

---

## 5. Task Sizing for Codex

Prefer narrow tasks such as:

```text
Implement RecommendationService + schema.
Do not add exhaustive boundary tests or documentation.
Add only minimal unit tests proving the rule paths work.
```

rather than:

```text
Implement the whole milestone, all tests, all docs, all evaluation fixtures,
all integration validation, and final cleanup.
```

Split a milestone when the implementation can be validated meaningfully between slices.

Good examples:

- schema + service;
- workflow integration;
- persistence layer;
- API endpoint;
- React view;
- LangGraph orchestration.

---

## 6. When Not to Use Codex

Prefer ChatGPT + user execution when the task is primarily:

- architecture/design discussion;
- prompt editing;
- scoring/rule calibration;
- reviewing a few files;
- adding/changing a small test;
- changing one or two simple Python files that the user wants to learn from;
- documentation;
- analyzing JSON output;
- deciding whether behaviour is sensible;
- GitHub issue/PR management.

Use Codex when implementation complexity, repository breadth, or local execution meaningfully justifies it.

---

## 7. PR Handoff Requirements

A Codex PR should report only what it actually performed:

- branch and commit;
- production files changed;
- minimal tests it ran and their result;
- known limitations;
- tests intentionally left to the user;
- any assumptions requiring manual validation.

Codex should not claim a milestone is fully validated when end-to-end/user validation has intentionally been left for the user.

---

## 8. User Validation Checklist

After a Codex implementation handoff, ChatGPT should normally guide the user through a compact checklist such as:

```text
git switch <branch>
git pull
python -m pytest
uvicorn app.main:app --reload
```

Then test the relevant endpoint or workflow with at least one realistic case.

For decision logic, include selected boundary cases rather than automatically asking Codex to generate all of them.

---

## 9. Escalation Rule

If user testing reveals a problem:

1. ChatGPT diagnoses it first from the error/output and PR code.
2. If the fix is small and safe, ChatGPT or the user may apply it.
3. Use Codex again only when the fix is non-trivial, multi-file, or benefits materially from repository-local execution.

This avoids reopening Codex for minor issues.

---

## 10. LangGraph-Specific Split

For upcoming LangGraph work:

### ChatGPT first

- define state schema;
- define graph nodes;
- define edges/conditional edges;
- decide what belongs in nodes vs existing services;
- define retry/error/human-review behaviour;
- define acceptance cases.

### Codex second

- install/integrate LangGraph if needed;
- implement the graph and node adapters;
- wire dependencies;
- add minimal graph unit/smoke tests.

### User third

- run the full suite;
- exercise the graph end-to-end;
- inspect state/output;
- test realistic jobs and failure cases.

### ChatGPT fourth

- review graph behaviour and runtime output;
- refine architecture before more autonomous features are added.

---

## 11. Cost/Effort Principle

Use Codex where it creates implementation leverage, not merely because code is involved.

The preferred order is:

```text
Can ChatGPT design/review it without coding?       → do that first
Can the user make/test a small change with guidance? → prefer that when useful
Is repository-aware multi-file coding valuable?    → use Codex
```

The goal is lower agent usage, clearer ownership, more user learning, and equally strong review discipline.
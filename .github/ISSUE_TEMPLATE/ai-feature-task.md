---
name: AI feature task
title: "[Feature] "
about: Shared implementation specification for ChatGPT, Codex, and human review
labels: ""
assignees: ""
---

# Goal

Describe the user/product outcome this feature should achieve.

# Background / Context

Explain why this work is needed and link relevant issues, PRs, docs, or prior milestones.

# Necessity Gate

Before substantial implementation, the coding agent must inspect the current repository and verify that this issue is still necessary and consistent with the current architecture.

Record one outcome:

- **Proceed** — the issue is still needed and the proposed scope is appropriate.
- **Duplicate** — another issue, PR, or implementation already covers the need.
- **Already implemented** — the requested behaviour already exists in the current codebase.
- **Superseded** — a newer design or issue replaces this approach.
- **Not planned / no longer necessary** — investigation shows the change adds no meaningful product value, introduces unnecessary complexity, or solves an obsolete problem.
- **Blocked / defer** — the issue remains valid but should wait for a named dependency or decision.

If the outcome is anything other than **Proceed**:

1. do not implement unnecessary production code;
2. document the evidence and reasoning clearly in the issue or PR;
3. recommend the appropriate disposition;
4. do **not** close the issue, merge a PR, delete work, or change product scope without explicit product-owner approval.

A decision not to implement is a valid engineering outcome when supported by evidence.

## Necessity decision record

**Outcome:** _Proceed / Duplicate / Already implemented / Superseded / Not planned / Blocked_

**Evidence:**

- 

**Recommended next action:**

- 

# Inputs

List the data, API inputs, schemas, user state, files, or external information the feature consumes.

# Expected Outputs

Describe the expected API/domain/UI outputs and important structured schemas.

# Architecture / Constraints

Record important constraints, including:

- multi-user/data-isolation requirements;
- existing interfaces that should be preserved;
- provider-independence expectations;
- security/privacy concerns;
- migration/backward-compatibility requirements;
- reuse of existing services/schemas/workflows where practical;
- avoidance of duplicated business logic and unrelated refactors;
- no silent fallback to demo/example user data in production-facing paths.

# LLM vs Deterministic Responsibilities

## LLM should handle

- 

## Deterministic Python/application logic should handle

- 

# Non-Goals

Explicitly list work that should not be included in this milestone.

# Acceptance Criteria

- [ ] Necessity gate completed and outcome recorded
- [ ] Core behavior implemented when outcome is **Proceed**
- [ ] Typed schemas updated/added where required
- [ ] Relevant authorization/data ownership preserved
- [ ] Error cases handled
- [ ] Tests added or updated
- [ ] Existing relevant tests still pass
- [ ] Full applicable test suite passes
- [ ] Documentation updated if behavior/architecture changed
- [ ] No candidate-specific assumptions added to shared logic
- [ ] No secrets/private production data committed

Add milestone-specific criteria below:

- [ ] 

# Test Expectations

Describe deterministic unit tests, integration tests, and any LLM evaluation cases required.

Prefer fakes/fixtures over live external services unless the issue explicitly requires an integration test.

Expected command(s), when applicable:

```powershell
cd backend
python -m pytest
```

# Likely Files / Components

List likely areas, but treat this as guidance rather than permission to rewrite unrelated code.

- `backend/app/...`
- `backend/tests/...`
- `prompts/...`
- `docs/...`

# Implementation Handoff

## Before coding

1. Read `AGENTS.md` and this issue in full.
2. Inspect the latest target branch and relevant code.
3. Complete the **Necessity Gate** above.
4. If the outcome is not **Proceed**, stop implementation and report the evidence/recommendation.

## If proceeding

1. Work on a dedicated feature branch.
2. Implement the smallest coherent change.
3. Add/update tests.
4. Run focused tests, then the full applicable suite.
5. Inspect the diff for unrelated changes.
6. Commit and push the branch.
7. Open a **draft PR** against the intended base branch.
8. Reference this issue in the PR.
9. Report branch, commit SHA, PR number, files changed, tests/results, known limitations, and excluded follow-up work.
10. Do **not** merge unless the product owner explicitly authorizes it.

## Branch

`feature/...`

## Primary implementer

Codex / ChatGPT / human

## Latest commit / PR

Add when handing off.

## Completed

- 

## Remaining

- 

## Known issues / design questions

- 

# Review Notes

Use this section for architecture/code-review findings and the decisions made in response.

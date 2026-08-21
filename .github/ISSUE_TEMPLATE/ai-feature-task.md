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
- migration/backward-compatibility requirements.

# LLM vs Deterministic Responsibilities

## LLM should handle

- 

## Deterministic Python/application logic should handle

- 

# Non-Goals

Explicitly list work that should not be included in this milestone.

# Acceptance Criteria

- [ ] Core behavior implemented
- [ ] Typed schemas updated/added
- [ ] Relevant authorization/data ownership preserved
- [ ] Error cases handled
- [ ] Tests added or updated
- [ ] Existing relevant tests still pass
- [ ] Documentation updated if behavior/architecture changed
- [ ] No candidate-specific assumptions added to shared logic
- [ ] No secrets/private production data committed

Add milestone-specific criteria below:

- [ ] 

# Test Expectations

Describe deterministic unit tests, integration tests, and any LLM evaluation cases required.

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

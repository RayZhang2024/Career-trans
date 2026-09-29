# Unified Job Journey Phase 9C Evidence

## Scope and evidence method

- Issue: #248 — Phase 9C Accessibility & responsive hardening
- Authorized baseline: `5fa87fa0f653ffd520d169fc2ceab4f2535f1e38`
- Repair branch: `codex/issue-248-phase-9c-accessibility-responsive`
- Render sizes: 390px × 844px and 1280px × 720px
- Evidence sources: local rendered UI, DOM/semantic inspection, focused frontend tests, and keyboard-only traversal using the existing browser capability.
- No live provider, web-search, or Codex calls were used.
- Render data was temporary local SQLite data and synthetic accounts only. The temporary database copies were not committed; no backend source, schema, migration, seed behavior, or durable user data changed.

The first Phase 9C implementation did not capture every required baseline surface before its CSS commit. This repair obtained the missing baseline evidence by rendering the exact authorized SHA in a separate detached checkout. That is remedial exact-base evidence, not a claim that every capture occurred chronologically before the first implementation commit.

The exact-base render confirmed the pre-fix mismatch: `.jobs-tabs` styled `button` and `aria-pressed`, while the route shells render anchor/NavLink elements with `aria-current="page"`. The selected Job Search and nested-opportunity links therefore lacked the intended selected styling and mobile tab sizing. The repaired render confirmed the anchor/`aria-current` selectors fix that issue.

Status vocabulary:

- `PASS`: the requested state was reached and met the check.
- `ISSUE_FOUND`: the exact-base pre-fix state showed the scoped issue.
- `FIXED`: the issue was verified repaired on the branch.
- `NOT_REACHABLE`: the state required persisted local records that were not available; no conclusion is drawn about that state.
- `NOT_PERFORMED`: the requested check was not completed.

## Rendered state matrix

### Exact authorized-base render, remedial pre-fix evidence

| Surface/state | 390px | 1280px | Evidence |
| --- | --- | --- | --- |
| Primary navigation and Home | PASS | PASS | Route reached at both sizes; navigation wrapped at 390px without clipping. |
| Profile Overview and Profile secondary navigation | PASS | PASS | Route and secondary links reached at both sizes. |
| Profile CV | PASS | PASS | CV form/remediation route reached at both sizes. |
| Profile Career Adviser | PASS | PASS | Adviser remediation route reached at both sizes. |
| Job Search Find/SearchIntent | PASS | PASS | SearchIntent controls reached; no horizontal clipping observed. |
| Job Search Inbox | ISSUE_FOUND | ISSUE_FOUND | Route reached, but rendered tabs were plain links without the intended selected styling. |
| My opportunities / Recommended | ISSUE_FOUND | ISSUE_FOUND | Primary and nested selected links lacked the intended selected styling. |
| My opportunities / Shortlisted | ISSUE_FOUND | ISSUE_FOUND | Nested selected link lacked the intended selected styling. |
| Search history | PASS | PASS | Empty/loading history state reached and fit. |
| Saved discovery history | PASS | PASS | Saved-search route reached and fit. |
| Workspace Overview | NOT_REACHABLE | NOT_REACHABLE | No canonical job record existed for the synthetic account. |
| Workspace Fit | NOT_REACHABLE | NOT_REACHABLE | No canonical job/evaluation record existed for the synthetic account. |
| Workspace Application | NOT_REACHABLE | NOT_REACHABLE | No canonical job/preparation record existed for the synthetic account. |
| Workspace Tracking | NOT_REACHABLE | NOT_REACHABLE | No canonical job/preparation/tracking record existed for the synthetic account. |
| Applications list | PASS | PASS | Global Applications route and external-preparation controls reached. |
| Application detail and downloads | NOT_REACHABLE | NOT_REACHABLE | No persisted preparation detail existed. |
| Tracking list | PASS | PASS | Global Tracking route and empty state reached. |
| Tracking detail/history | NOT_REACHABLE | NOT_REACHABLE | No persisted tracking detail existed. |
| Settings / AI Models | PASS | PASS | Settings controls reached and fit at both sizes. |

### Repaired branch post-fix render

| Surface/state | 390px | 1280px | Result |
| --- | --- | --- | --- |
| Primary navigation and Home | PASS | PASS | Route and navigation fit at both sizes. |
| Profile Overview/CV/Adviser and secondary navigation | PASS | PASS | Reachable states fit and remained identifiable. |
| Job Search Find/SearchIntent | PASS | PASS | Controls remained reachable and fit. |
| Job Search Inbox and tabs | FIXED | FIXED | Selected anchor styling and mobile tab sizing now apply. |
| My opportunities / Recommended | FIXED | FIXED | Primary and nested selected links now have selected styling. |
| My opportunities / Shortlisted | FIXED | FIXED | Nested selected link now has selected styling. |
| Search history and Saved discovery history | PASS | PASS | Empty/loading states fit. |
| Workspace Overview/Fit/Application/Tracking | NOT_REACHABLE | NOT_REACHABLE | No canonical job record was available in the temporary local render data. |
| Applications list | PASS | PASS | External-preparation controls and empty/history state fit. |
| Application detail and downloads | NOT_REACHABLE | NOT_REACHABLE | No persisted preparation detail existed. |
| Tracking list | PASS | PASS | Empty tracking state fit. |
| Tracking detail/history | NOT_REACHABLE | NOT_REACHABLE | No persisted tracking detail existed. |
| Settings / AI Models | PASS | PASS | Long content fit and scrolled vertically without horizontal overflow. |

The repaired screenshots showed no horizontal overflow at either requested size for the reachable routes. Vertical scrolling on long pages was expected.

## Accessibility and semantic checks

### Repeated action names

Visible wording, element types, routes, handlers, and locking mechanics were preserved.

- `JobsPage.tsx`: repeated actions include human-readable title/company/location context plus a surface-specific discriminator. Inbox actions use `last seen` time, Shortlisted/Dismissed actions use decision-update time, Recommended actions use the visible `result N` ordinal, and Search history run toggles use status plus started time. Rows without an opportunity use `returned result 1`, `returned result 2`, and `historical result N`; no `discovered_job_id` is exposed in an accessible name.
- `jobDecisions.tsx`: repeated DecisionControls accept optional human-readable context for action names while preserving the visible decision labels and mutation mechanics.
- `JobWorkspacePage.tsx`: preparation/tracking actions include job context and creation time; tracked actions also include status/update time. Start tracking uses `Start tracking for …` normally and `Starting tracking… for …` while pending, matching the visible label and preserving the disabled lock.
- `ApplicationsPage.tsx`: preparation actions include title/company/location and creation time; tracking actions also include status/update time.
- `TrackingPage.tsx`: tracking/preparation actions include title/company/location and status/update time.

Focused tests now directly prove:

- no-opportunity exact-result and historical rows retain visible action wording, contain no opaque discovered-job ID, and remain distinct by human-readable ordinal;
- normal and pending Workspace Start tracking accessible names match their visible labels;
- two Applications records with the same title, company, and location differ only by creation time and produce distinct names;
- two Tracking records with the same title, company, and location differ by status/update time and produce distinct names;
- repeated no-opportunity Job Search result actions remain distinct without using IDs;
- same-shaped Inbox, Shortlisted, and Dismissed rows expose distinct contextual workspace/decision action names using visible timestamps;
- same-shaped Recommended cards expose distinct preparation-toggle and DecisionControls names using the visible result ordinal;
- repeated Search history run cards expose distinct View run/Close run names using status and started time while preserving `aria-expanded`.

### Keyboard-only smoke

Result: `PARTIAL — not PASS` under the literal Issue #248 gate.

Exercised on the repaired branch:

- primary navigation;
- Profile secondary navigation;
- Job Search navigation;
- My opportunities navigation;
- SearchIntent controls on Find jobs;
- global Applications controls;
- global Tracking controls;
- Settings controls.

Not exercised because the corresponding rendered states were `NOT_REACHABLE`:

- Job Workspace Overview/Fit/Application/Tracking tabs;
- repeated job-card action group;
- Workspace Application controls;
- Workspace Tracking controls;
- application-detail download controls;
- tracking-detail/history controls.

The evidence therefore does not claim a complete keyboard PASS and the PR remains draft/not merge-ready.

### Downloads and status semantics

- Existing download labels remain distinct in source/tests (`Download CV DOCX`, `Download CV PDF`, and separate cover-letter variants); no download markup or behavior changed. Rendered download controls were `NOT_REACHABLE` because no preparation detail existed.
- Existing `role="status"` and `role="alert"` semantics remain in place for loading, success, retry, and error messages. No blanket `aria-live` region was added.

## Scoped changes

| File | Change |
| --- | --- |
| `frontend/src/App.css` | Corrected the existing `.jobs-tabs` selectors to target rendered anchors and `aria-current="page"`. |
| `frontend/src/JobsPage.tsx` | Replaced opaque ID fallbacks with human-readable result ordinals. Existing contextual accessible names remain otherwise unchanged. |
| `frontend/src/jobDecisions.tsx` | Added optional human-readable context to repeated decision-action accessible names without changing visible labels or decision behavior. |
| `frontend/src/JobWorkspacePage.tsx` | Made the Start tracking accessible name follow the visible pending label. |
| `frontend/src/ApplicationsPage.test.tsx` | Made the repeated-preparation records same-shaped and retained distinct-name assertions. |
| `frontend/src/TrackingPage.test.tsx` | Made the repeated-tracking records same-shaped and retained distinct-name assertions. |
| `frontend/src/JobsPage.test.tsx` | Added ordinal/no-ID result regressions, repeated surface/action-name regressions, and normal/pending Start tracking assertions. |
| `docs/UNIFIED_JOB_JOURNEY_PHASE9C_EVIDENCE.md` | Corrected the evidence gate record with exact-base remedial baseline evidence and explicit remaining limits. |

No route, readiness authority, candidate authority, application-preparation behavior, tracking behavior, backend/API/schema, migration, lifecycle, data, provider, or Phase 9D behavior changed.

## Validation record

- Focused Jobs/Applications/Tracking tests: 3 files, 231 passed.
- Full frontend suite: 15 files, 573 passed.
- TypeScript typecheck: passed.
- Production build: passed; Vite emitted only the existing informational large-chunk warning.
- `git diff --check`: passed; Git emitted only normal LF-to-CRLF conversion warnings.
- Backend tests were not rerun because this remains frontend-only and no backend files changed.

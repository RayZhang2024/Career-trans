# Unified Job Journey Phase 9D — Final history/deep-link parity sign-off

## Scope and authorized baseline

- Issue: #250 — Phase 9D: Final history/deep-link parity sign-off
- Authorized baseline: `5d05f0f385251e34a8b50bdba0c849ac7877e5ff`
- Branch: `codex/issue-250-phase-9d-parity-signoff`
- Verification scope: source inspection, existing regression coverage, and the
  two narrowly justified test-only additions recorded below.
- Production-source changes: none.
- Backend/API/schema/migration changes: none.
- Live provider, ATS, web-search, LLM, browser-network, and Codex calls: none.

The verification started from the exact authorized `origin/main` SHA. The
baseline was not rebased or adapted. All persisted-read checks used the
repository's offline test fixtures and provider-forbidden seams.

## Evidence key

| Key | Concrete evidence |
| --- | --- |
| F1 | `frontend/src/App.tsx`: protected route table, canonical shells, compatibility redirects, and unknown-path fallback. |
| F2 | `frontend/src/JobsPage.tsx`: literal Job Search route parsing, reserved-slug rejection, history query handling, Workspace links, and read-state presentation. |
| F3 | `frontend/src/JobsSearchesPage.tsx`: canonical saved-discovery view, legacy state handoff, persisted execution history, and stale/error handling. |
| F4 | `frontend/src/JobWorkspacePage.tsx`: Workspace Overview/Fit/Application/Tracking composition, historical evaluations, preparation snapshots, tracking projection, and readiness handling. |
| F5 | `frontend/src/ApplicationsPage.tsx`, `frontend/src/TrackingPage.tsx`, `frontend/src/CvPage.tsx`, `frontend/src/AdviserPage.tsx`, and `frontend/src/AiSettingsPage.tsx`: durable detail/history, downloads, Profile workflows, and Settings controls. |
| FT1 | `frontend/src/JobsPage.test.tsx`: route shell, compatibility, Job Search route family, history/run/job deep links, reserved/malformed paths, Workspace lifecycle, decisions, and readiness regressions. |
| FT2 | `frontend/src/JobsSearchesPage.test.tsx`: saved-discovery state forwarding, direct configuration/history reads, execution snapshots, stale reconciliation, and provider-free UI reads. |
| FT3 | `frontend/src/ApplicationsPage.test.tsx`, `frontend/src/TrackingPage.test.tsx`, `frontend/src/CvPage.test.tsx`, and `frontend/src/AdviserPage.test.tsx`: detail/history, download, tracking, CV/Adviser readiness, retry, and stale-session behavior. |
| FT4 | `frontend/src/App.test.tsx`, `frontend/src/ProfileSecondaryNavigation.test.tsx`, and `frontend/src/AiSettingsPage.test.tsx`: Profile navigation/source history, Home readiness, session replacement, Profile secondary navigation, and Settings behavior. |
| B1 | `backend/tests/test_job_workspace.py` and `backend/tests/test_jobs_read_models.py`: bounded provider-free Workspace/opportunity/history projections, applicability, runtime attribution, ordering, malformed-record safety, and user scope. |
| B2 | `backend/tests/test_application_preparation.py` and `backend/tests/test_application_tracking.py`: immutable preparation/detail/download history, tracking/event authority, provider-free reads, and cross-user safe 404 behavior. |
| B3 | `backend/tests/test_discovery_schedule_service.py`: saved schedule ownership, execution history, snapshot behavior, cross-user isolation, and the Phase 9D provider-forbidden persisted-read regression. |
| B4 | `backend/tests/test_user_job_decisions.py`: explicit decision authority, absence versus persisted `undecided`, CAS/revision behavior, ordering, and user scoping. |
| D1 | `docs/UNIFIED_JOB_JOURNEY_PHASE9_AUDIT.md` and `docs/UNIFIED_JOB_JOURNEY_UX_MIGRATION.md`: Phase 9A route, authority, history, compatibility, rollback, and migration contracts. |
| D2 | `docs/UNIFIED_JOB_JOURNEY_PHASE9C_EVIDENCE.md`: repaired-branch 390px/desktop and keyboard evidence, including its intentionally retained historical exact-base rows. |

## Canonical route and deep-link matrix

`App.tsx` owns the top-level route family. `JobsPage.tsx` owns the literal
Job Search paths and the encoded dynamic Workspace paths. Literal paths are
checked before the dynamic match, and the parser decodes one segment before
rejecting malformed or reserved values. The route tests exercise direct
initial entries rather than relying only on clicked navigation.

| Canonical route | Ownership and deep-link evidence | Result |
| --- | --- | --- |
| `/home` | `F1` `HomePage`; `FT1` Home route/readiness tests. | Confirmed |
| `/profile` | `F1` `ProfileHome`; `FT4` canonical snapshot and Profile navigation tests. | Confirmed |
| `/profile/cv` | `F1` `CvPage`; `FT1` direct CV workflow and `FT3` CV history/readiness tests. | Confirmed |
| `/profile/adviser` | `F1` `AdviserPage`; `FT1` direct Adviser workflow and `FT3` Adviser readiness/history tests. | Confirmed |
| `/jobs/find` | `F1`/`F2` Find route and SearchIntent form; `FT1` Issue #230 and Job Search route-family tests. | Confirmed |
| `/jobs/find/saved` | `F1`/`F3` canonical saved-discovery route; `FT2` direct saved configuration/history tests. | Confirmed |
| `/jobs/inbox` | `F2` Job Search navigation/parser; `FT1` Inbox and non-actionable/readiness tests. | Confirmed |
| `/jobs/opportunities/recommended` | `F2` Recommended/current-analysis view; `FT1` direct Recommended and loading/error/applicability tests. | Confirmed |
| `/jobs/opportunities/shortlisted` | `F2` explicit decision projection; `FT1` direct Shortlisted/Dismissed-management tests and `B4`. | Confirmed |
| `/jobs/history` | `F2` Search History view; `FT1` bounded list, stale, unavailable, and history navigation tests. | Confirmed |
| `/jobs/:discoveredJobId` | `F2` dynamic route parser and `F4` Workspace Overview; `FT1` direct Workspace/not-found tests and `B1`. | Confirmed |
| `/jobs/:discoveredJobId/fit` | `F2`/`F4` Workspace Fit; `FT1` current/historical/unknown applicability and runtime-attribution tests. | Confirmed |
| `/jobs/:discoveredJobId/application` | `F2`/`F4` Workspace Application; `FT1` readiness/history/conflict/interruption tests and `B1`/`B2`. | Confirmed |
| `/jobs/:discoveredJobId/tracking` | `F2`/`F4` Workspace Tracking; `FT1` start/pending/history tests and `B1`/`B2`. | Confirmed |
| `/applications` | `F1`/`F5` Applications list; `FT3` list/loading/error/history tests and `B2`. | Confirmed |
| `/applications/:preparationId` | `F1`/`F5` durable detail route; `FT3` direct review/download tests and `B2` owner-safe 404 tests. | Confirmed |
| `/tracking` | `F1`/`F5` Tracking list; `FT3` list/loading/error/history tests and `B2`. | Confirmed |
| `/tracking/:trackingId` | `F1`/`F5` durable tracking detail; `FT3` direct status/event tests and `B2` owner-safe 404 tests. | Confirmed |
| `/settings/ai` | `F1`/`F5` canonical Settings route; `FT4` Settings navigation, loading, error, validation, and session tests. | Confirmed |

The route families therefore have one canonical owner each: Profile,
Job Search, Applications, and Tracking. Direct record URLs remain stable.
`/jobs/opportunities` is a reserved/noncanonical slug; it is not redirected
and `FT1` proves it is not sent to a Workspace endpoint.

## Compatibility redirect matrix

| Compatibility path | Canonical destination | Semantics and evidence | Result |
| --- | --- | --- | --- |
| `/` | `/profile` | `F1` uses `<Navigate replace>`; `FT1` proves root and unknown fallback navigation type `REPLACE`. | Confirmed |
| `/cv` | `/profile/cv` | `F1` uses `<Navigate replace>`; the Phase 9D alias regression directly asserts `REPLACE`, and `FT1` preserves the CV workflow. | Confirmed |
| `/adviser` | `/profile/adviser` | `F1` uses `<Navigate replace>`; the Phase 9D alias regression directly asserts `REPLACE`, and `FT1` preserves the Adviser workflow. | Confirmed |
| `/jobs` | `/jobs/find` | `F1` uses the protected `<Navigate replace>`; `FT1` directly asserts `REPLACE` and the Find heading. | Confirmed |
| `/jobs/searches` | `/jobs/find/saved` | `F1` forwards `location.state` through `LegacySavedSearchesRedirect`; `FT2` proves SearchIntent/schedule state survives and the Phase 9D regression asserts `REPLACE`. | Confirmed |
| `/settings` | `/settings/ai` | `F1` uses `<Navigate replace>`; the Phase 9D alias regression directly asserts `REPLACE`. | Confirmed |

Unknown paths safely fall back to `/profile` with replacement semantics. No
redirect was invented for `/jobs/opportunities`; reserved and malformed Job
Search segments fail closed before Workspace lookup.

## Profile canonical-link result

Phase 9B remains intact. General Profile remediation links use `/profile`:
`CvPage.tsx`'s confirmed-CV link and the two `ExternalPreparationForm.tsx`
remediation links. CV-specific links use `/profile/cv`, and Adviser-specific
links use `/profile/adviser`. `F1`, `F5`, `FT1`, `FT3`, and `FT4` cover those
destinations. No remediation CTA regressed to `/`; `/` remains compatibility
only.

## History, readability, and deep-link matrix

| Contract | Source/test evidence | Result |
| --- | --- | --- |
| Exact completed run results remain distinct from Recommended/current analyses. | `F2` exact-result rendering; `FT1` exact-result and current/historical applicability tests; `B1` run read models. | Confirmed |
| `/jobs/history?run=<runId>` and `?run=<runId>&job=<discoveredJobId>` load directly. | `F2`; `FT1` direct run/job, bounded-list failure, and selected-run tests. | Confirmed |
| Historical query/input, funnel, outcome, and per-run job detail remain readable. | `F2`; `FT1` history detail tests; `B1` bounded historical run read models. | Confirmed |
| Runtime attribution remains available where persisted and does not become currentness by inference. | `F2`/`F4`; `FT1` runtime-attribution and applicability tests; `B1`. | Confirmed |
| Candidate/job/runtime changes do not rewrite old evaluations. | `F4`; `FT1` current versus historical evaluation tests; `B1` snapshot/applicability tests. | Confirmed |
| Inactive or non-actionable jobs retain permitted historical/read-only access. | `F2`/`F4`; `FT1` non-actionable and historical Workspace tests; `B1`. | Confirmed |
| Back/forward navigation restores history state. | `F2`; `FT1` normalized job-only URL and back/forward test. | Confirmed |
| Stale/invalid run/job deep links fail safely. | `F2`; `FT1` unavailable direct run/job and safe-close tests. | Confirmed |
| Saved configurations are user-owned; execution snapshots are immutable historical context. | `F3`; `FT2` saved-history/stale tests; `B3` schedule ownership and snapshot tests. | Confirmed |
| Editing a schedule affects future executions only; old execution history remains readable. | `B3` snapshot-after-edit and execution-history tests; `FT2` history tests. | Confirmed |
| History reads do not require live acquisition. | `F3`; `FT2`; `B3` provider-forbidden schedule-read regression. | Confirmed |

## User-scoping matrix

| User-owned surface | Evidence | Result |
| --- | --- | --- |
| Opportunity detail and Workspace private projections | `B1` scoped opportunity/workspace reads and safe missing-job 404 tests; `FT1` Workspace direct-route tests. | Confirmed |
| Discovery-run detail and historical run-job detail | `B1` bounded historical run read models; `FT1` direct history/deep-link tests. | Confirmed |
| Saved schedules and execution history | `B3` cross-user schedule routes and execution reads; `FT2` direct saved-history tests. | Confirmed |
| Decision reads/lists and explicit Shortlist/Dismiss state | `B4` absence/undecided/CAS/list scope tests; `FT1` decision projections and reconciliation tests. | Confirmed |
| Application detail, review, and downloads | `B2` owner/cross-user read, review, and download tests; `FT3` direct detail tests. | Confirmed |
| Tracking detail, by-preparation lookup, and event history | `B2` scoped list/detail/by-preparation/history tests; `FT3` direct tracking tests. | Confirmed |

Foreign records fail closed with safe 404/empty-projection behavior and do not
leak private identifiers or diagnostics.

## Provider-free and read-only matrix

| Persisted read path | Evidence | Result |
| --- | --- | --- |
| Opportunity and Search History GETs | `B1` provider-free dashboard/run read-model tests. | Confirmed |
| Discovery run list/detail/run-job history | `F2`; `B1` bounded historical read tests. | Confirmed |
| Workspace GET | `F4`; `B1` provider-free Workspace tests, including lazy dependency and runtime-unavailable states. | Confirmed |
| Applications list/detail/review/download | `F5`; `B2` application history/download provider-free test. | Confirmed |
| Tracking list/detail/by-preparation/history | `F5`; `B2` real provider-free tracking dependency test. | Confirmed |
| Saved schedule configuration and execution-history GETs | `F3`; `B3` `test_authenticated_schedule_reads_do_not_construct_execution_provider_dependency`. | Confirmed |

Explicit writes remain explicit: discovery execution, evaluation, application
preparation, and tracking status changes are not triggered by these reads.
No live provider call was made by this sign-off.

## Shortlist, Application, and Tracking parity

### Shortlist and decision authority

`B4` confirms absence versus persisted `undecided`, durable user-owned
Shortlist/Dismiss transitions, CAS/revision behavior, bounded ordering, and
strict user scope. `F2`/`FT1` confirm Recommended/current analyses remain
evaluation-owned, Shortlisted is explicit decision state, dismissed rows remain
recoverable, and shortlist is not inferred from Fit, preparation, tracking,
ranking, downloads, or history.

### Application history

`F1`/`F4`/`F5` retain `/applications` and
`/applications/:preparationId` as canonical durable routes. `B2` confirms
immutable target/identity/result/runtime snapshots, owner isolation, provider-
free list/detail/review/download reads, and safe 404 behavior for foreign
records. `FT1`/`FT3` confirm Workspace Application history, review, and all CV
and cover-letter downloads remain reachable. Preparing does not imply Applied.

### Tracking history

`F1`/`F4`/`F5` retain `/tracking` and `/tracking/:trackingId`. `B2` confirms
one-preparation linkage, append-only event history, persisted current status,
historical target snapshots, by-preparation scoping, and provider-free reads.
`FT1`/`FT3` confirm Workspace/global tracking detail and status-history
behavior. Preparation creation and job state do not imply application status.

## Loading, error, readiness, and stale-state matrix

| State contract | Evidence | Result |
| --- | --- | --- |
| Home/Profile loading and retry | `F1`/`F5`; `FT3`/`FT4` Home/Profile loading, retry, snapshot, and stale-session tests. | Confirmed |
| CV/Adviser prerequisites, readiness, unavailable, and retry | `F5`; `FT3` CV/Adviser readiness and provider-failure tests. | Confirmed |
| Find Jobs saved-schedule loading, validation, preflight, pending, interruption, and reconciliation | `F3`; `FT2` saved configuration and run lifecycle tests. | Confirmed |
| Inbox non-actionable and readiness states | `F2`; `FT1` Inbox actionability/readiness tests. | Confirmed |
| Recommended/Shortlisted loading, error, empty, CAS, and reconciliation | `F2`; `FT1` Recommended/Shortlisted lifecycle tests; `B4`. | Confirmed |
| Search History unavailable, stale, direct deep link, and invalid detail | `F2`/`F3`; `FT1`/`FT2` history tests; `B1` read models. | Confirmed |
| Workspace Overview/Fit loading, not-found, current/historical, and unavailable states | `F4`; `FT1` Workspace lifecycle/applicability tests; `B1`. | Confirmed |
| Workspace Application readiness, not-ready, unavailable, history, POST conflict, and interruption | `F4`; `FT1` Workspace Application tests; `B2` preparation history/read tests. | Confirmed |
| Workspace Tracking loading, untracked, start, pending, and history | `F4`; `FT1`/`FT3` tracking lifecycle tests; `B2`. | Confirmed |
| Applications and Tracking list/detail loading, not-found, error, and retry | `F5`; `FT3` list/detail lifecycle tests; `B2` safe 404 tests. | Confirmed |
| Delayed responses cannot restore prior-user state | `F1`/`F5`; `FT1`, `FT3`, and `FT4` session replacement/stale-response tests. | Confirmed |

## Rollback and no-deletion sign-off

Compared with the Phase 9A audit and UX migration inventory (`D1`):

- all six compatibility aliases remain in `F1`;
- Search History and saved execution history remain present and readable;
- Workspace evaluation history remains present and bounded;
- immutable ApplicationPreparation detail/review/download history remains present;
- Tracking detail and append-only event history remain present;
- no historical route or component was deleted;
- no existing read API was removed or renamed;
- no schema or data migration was introduced;
- decision, preparation, and tracking records remain retained rather than
  deleted to simulate rollback;
- the staged rollback model remains viable: UI/navigation composition can be
  reverted while durable records and read APIs remain intact.

This is structural sign-off only; no destructive rollback execution was run.

## Phase 9C dependency result

`D2` remains the dependency record. It still documents completed 390px × 844px
and 1280px × 720px checks, completed keyboard smoke, and no unsupported
assistive-technology/screen-reader conformance claim. Its historical exact-base
pre-fix `NOT_REACHABLE` rows are intentionally retained and are not unresolved
post-fix findings. The repaired/post-fix matrix and final keyboard result are
complete. Phase 9D did not change production UI code or inherit new visual
evidence requirements.

## Validation

| Validation | Result |
| --- | --- |
| Focused frontend parity suites | 8 files, 433 passed: `JobsPage`, `JobsSearchesPage`, `ApplicationsPage`, `TrackingPage`, `CvPage`, `AdviserPage`, `ProfileSecondaryNavigation`, and `App`. |
| Full frontend suite | 15 files, 580 passed. |
| TypeScript typecheck | Passed. |
| Frontend production build | Passed; Vite emitted only the existing informational large-chunk warning. |
| Focused backend parity/scoping/provider-free suites | 6 files, 127 passed: Workspace, read models, application preparation, tracking, discovery schedules, and user decisions. |
| Full backend suite | 1,091 passed. |
| `git diff --check` | Passed. |

The test additions were limited to direct compatibility `REPLACE`/state
forwarding coverage and the provider-forbidden persisted schedule-read
contract. No existing test was deleted, weakened, skipped, xfailed, or widened
with a hiding mock.

## Unresolved findings and final verdict

Unresolved required findings: **None.**

Final verdict: **`PARITY_CONFIRMED`**

This sign-off does not mark the PR ready and does not merge it. The PR remains
draft pending review.

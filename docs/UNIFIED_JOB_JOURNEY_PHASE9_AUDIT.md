# Phase 9A — Unified Job Journey parity and consolidation audit

## Scope, baseline, and method

This audit describes the implementation that exists at the authorized baseline
`378c44bf0e72af1f66aec8cce07e9fc930242753` on `main`. It is an audit artifact,
not a product change. The audit inspected the React route table and shells,
the Job Search, Workspace, Applications, Tracking, Profile, Home, and Settings
surfaces, their focused tests, and the corresponding authenticated FastAPI
read models and ownership tests.

The following evidence abbreviations are used below:

| Key | Evidence |
| --- | --- |
| E1 | `frontend/src/App.tsx`: `AppShell`, route definitions, `Protected`, `HomePage`, and legacy redirects. |
| E2 | `frontend/src/ProfileSecondaryNavigation.tsx`; `frontend/src/JobsPage.tsx`: `JobSearchNavigation`, `OpportunitiesNavigation`, and route parser. |
| E3 | `frontend/src/JobsPage.tsx` and `frontend/src/JobsSearchesPage.tsx`: SearchIntent, saved schedules, Inbox, opportunities, Shortlisted, history, run/job detail, and exact-result presentation. |
| E4 | `frontend/src/JobWorkspacePage.tsx`: Overview, Fit, Application, Tracking, preparation history, evaluation history, provenance, and readiness. |
| E5 | `frontend/src/ApplicationsPage.tsx` and `frontend/src/TrackingPage.tsx`: global/detail history, immutable target snapshots, downloads, and manually recorded events. |
| E6 | `frontend/src/App.css`: wrapping navigation, min-width/overflow handling, and max-width 640px layout rules. 390px visual evidence was not performed. |
| T1 | `frontend/src/JobsPage.test.tsx`: Issue #230 route/shell tests; Issue #236 route-family and history deep-link tests; Issue #238 lifecycle tests; Issue #240 decision-authority tests; Workspace Application readiness tests. |
| T2 | `frontend/src/ProfileSecondaryNavigation.test.tsx` and `frontend/src/App.test.tsx`: secondary navigation, Profile source/history, Home readiness, auth replacement, and compatibility redirects. |
| T3 | `frontend/src/JobsSearchesPage.test.tsx`: saved-search direct links, history, preflight, execution, stale state, and auth behavior. |
| T4 | `frontend/src/ApplicationsPage.test.tsx` and `frontend/src/TrackingPage.test.tsx`: preparation/tracking direct links, provider-free reads, owner scoping, stale requests, downloads, and event history. |
| B1 | `backend/tests/test_job_workspace.py`, `test_jobs_read_models.py`, and `test_user_job_decisions.py`: provider-free read models, historical/current applicability, bounded projections, decision authority, and user scoping. |
| B2 | `backend/tests/test_application_preparation.py` and `test_application_tracking.py`: immutable preparation snapshots, provider-free history/downloads, owner isolation, tracking history, and event authority. |

No live provider, browser network, or external service evidence was used.

## Route and surface master matrix

Disposition meanings are intentionally limited to the Phase 9A vocabulary:
RETAIN means the surface is canonical and remains; CONSOLIDATE_LINK means the
surface works but internal links should move to the canonical destination;
COMPATIBILITY_ONLY means retain only to preserve old links; REMOVE_LATER means
removal is staged and not performed here; BLOCKED means a parity or authority
defect prevents sign-off and the required follow-up is recorded.

| Surface / route / component | Canonical or compatibility | Purpose | Owning authority | Primary nav | Read/write/provider behavior | User scope | Direct/deep link | Historical role | Loading/error/readiness | Accessibility | 390px status | Disposition | Evidence | Follow-up |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/home` HomePage | Canonical | Workspace landing and readiness links | onboarding status for readiness; linked surfaces own their data | Workspace > Home | GET onboarding status only; no provider call | authenticated user | direct protected route | no historical content | loading, retryable error, ready/empty copy | PASS for labelled nav/status/error | NOT PERFORMED | RETAIN | E1, T1 | Phase 9C visual/accessibility pass only |
| `/profile` ProfileHome | Canonical | Confirmed candidate Profile and source/history entry point | canonical profile snapshot/revisions | Workspace > Profile; Profile secondary Overview | read snapshot, active revision, optional provenance; writes revision workflow; no provider for reads | authenticated user | direct/reload-safe | source history and revision provenance | independent source-history loading/error/retry | PARTIAL: labelled sections, repeated generic actions need review | NOT PERFORMED | RETAIN | E1, E2, T2 | Phase 9C review of action naming |
| `/profile/cv` CvPage | Canonical | CV ingestion/review workflow | CV draft and confirmed Profile authority | Profile secondary > CV | authenticated read/write CV workflow; semantic work only on explicit user action | authenticated user | direct and `/cv` alias | CV source/history is preserved | prerequisite, upload, review, unavailable, retry states | PASS/PARTIAL: labelled form; long source content needs visual check | NOT PERFORMED | RETAIN | E1, E2, T2 | retain current alias while users migrate |
| `/profile/adviser` AdviserPage | Canonical | Career Adviser intake/review/proposal workflow | adviser assessment plus confirmed Profile transfer | Profile secondary > Career Adviser | authenticated reads/writes; provider only on explicit generation/action | authenticated user | direct and `/adviser` alias | adviser assessment/clarifications/proposals persist | CV prerequisite, loading, error, readiness-blocked, retry | PASS/PARTIAL: labelled controls; long proposal layout needs visual check | NOT PERFORMED | RETAIN | E1, E2, T2 | retain current alias while users migrate |
| `/` | Compatibility redirect | Historical entry point to Profile | router redirect only | none | replace redirect; no data/provider read | auth determines protected destination | direct reload redirects to `/profile` | legacy root links still resolve | no surface state | redirect is transparent | NOT PERFORMED | COMPATIBILITY_ONLY | E1, T1 | migrate internal root links in a later narrow cleanup |
| Internal Profile remediation links via `/` (`CvPage` / `ExternalPreparationForm`) | Canonical destination `/profile`; compatibility path `/` | Profile remediation links for legacy CV/preparation states | Profile route ownership; router compatibility redirect currently completes the transition | reached from CV and preparation error states; not primary nav | internal links only; no provider behavior | authenticated user | direct click resolves through `/` to `/profile` | no historical authority; `/` remains compatibility-only | no independent loading/error/readiness state | PARTIAL: remediation copy is labelled; destination should become canonical | NOT PERFORMED | CONSOLIDATE_LINK | `frontend/src/CvPage.tsx`, `frontend/src/ExternalPreparationForm.tsx`, E1 | later change only link destinations and focused assertions; rollback restores old destinations; retain `/` redirect |
| `/cv` | Compatibility redirect | Historical CV entry point | router redirect to Profile CV | none | replace redirect; no data/provider read | auth-protected | direct reload redirects to `/profile/cv` | preserves pre-Phase-2 links | no surface state | transparent redirect | NOT PERFORMED | COMPATIBILITY_ONLY | E1, T1 | no removal until link inventory is clean |
| `/adviser` | Compatibility redirect | Historical Adviser entry point | router redirect to Profile Adviser | none | replace redirect; no data/provider read | auth-protected | direct reload redirects to `/profile/adviser` | preserves pre-Phase-2 links | no surface state | transparent redirect | NOT PERFORMED | COMPATIBILITY_ONLY | E1, T1 | no removal until link inventory is clean |
| `/jobs` | Compatibility redirect | Historical Jobs landing entry point | router redirect to Find jobs | Workspace > Job Search resolves canonical family | replace redirect; no provider call | authenticated user | direct reload redirects to `/jobs/find` | Phase 2 compatibility | no surface state | transparent redirect | NOT PERFORMED | COMPATIBILITY_ONLY | E1, T1 | preserve while external links may exist |
| `/jobs/find` JobsPage find | Canonical | Build SearchIntent and start explicit discovery | SearchIntent/saved schedule and explicit execution | Job Search > Find jobs | read readiness/schedules; writes saved config or explicit run; provider only on explicit run | authenticated user | direct/reload-safe | current transient SearchIntent is not history | readiness, schedule load, validation, execution pending/error/reconciliation | PARTIAL: shell labels are good; repeated card actions need names scoped | NOT PERFORMED | RETAIN | E2, E3, T1, T3 | Phase 9C action-name review |
| `/jobs/find/saved` JobsSearchesPage | Canonical | Manage saved discovery configurations | persisted user saved schedules/executions | Job Search > Find jobs route family; no separate primary item | schedule/history reads are local; create/update/run are explicit writes; run may call provider | authenticated user | direct/reload-safe | saved execution history and run attribution | loading, stale/empty, 404, auth, preflight, interrupted-run uncertainty | PARTIAL: form labels and status roles present; dense history needs visual review | NOT PERFORMED | RETAIN | E2, E3, T3 | Phase 9C density/keyboard pass |
| `/jobs/inbox` JobsPage inbox | Canonical | Show shared persisted public vacancies for explicit evaluation selection | persisted inbox/job authority; evaluation result owns analysis | Job Search > Inbox | provider-free persisted read; evaluation POST is explicit provider-backed action | inbox item selection is user-scoped | direct/reload-safe | exact evaluation result can survive route changes | loading/error/empty, non-actionable visible and disabled, pending/reconciliation | PASS/PARTIAL: factual non-actionable states; generic repeated actions need review | NOT PERFORMED | RETAIN | E2, E3, T1, B1 | keep non-actionable rows readable and non-actionable |
| `/jobs/opportunities/recommended` JobsPage recommended | Canonical | Show current backend analyses and recommendation ordering | current opportunity/evaluation projection | Job Search > My opportunities > Recommended | provider-free persisted read; decisions are explicit writes | authenticated user | direct/reload-safe | current evaluation with applicability/history | loading/error/empty, unavailable applicability, refresh | PARTIAL: useful labels; card action names are repeated | NOT PERFORMED | RETAIN | E2, E3, T1, B1 | Phase 9C link disambiguation |
| `/jobs/opportunities/shortlisted` JobsPage shortlisted | Canonical | Show explicit durable Shortlisted decisions | user decision projection, not recommendations | Job Search > My opportunities > Shortlisted | provider-free scoped read; shortlist/dismiss/undo writes only decisions | authenticated user | direct/reload-safe | decision revision/current state; dismissed management | loading/error/empty, pending/CAS/reconciliation, non-actionable | PARTIAL: controls are labelled; repeated workspace/fit links need context | NOT PERFORMED | RETAIN | E2, E3, T1, B1 | keep separate from recommendation authority |
| `/jobs/history` JobsPage history | Canonical | Search run history and exact historical results | persisted discovery runs and result snapshots | Job Search > Search history | provider-free summary/detail reads; explicit run is provider-backed | authenticated user | direct; `?run=` and `?job=` deep links | current/historical/unknown evaluation, runtime attribution, exact run/job result | bounded list, lazy detail, unavailable/stale, direct detail outside window | PARTIAL: state language is strong; nested generic links need review | NOT PERFORMED | RETAIN | E2, E3, T1, T3, B1 | retain query deep-link contract |
| `/jobs/searches` | Compatibility redirect | Historical saved-search entry point | router redirects to `/jobs/find/saved` | none | replace redirect; no provider read | authenticated user | direct reload redirects | preserves older saved-search links | no surface state | transparent redirect | NOT PERFORMED | COMPATIBILITY_ONLY | E1, E3, T3 | preserve until external links age out |
| `/jobs/:discoveredJobId` JobWorkspacePage Overview | Canonical | Unified job facts, provenance, decision and workspace entry | persisted discovered job plus scoped decision/evaluation/application projections | reached from Job Search cards; no new primary item | provider-free workspace read; preparation/tracking writes are explicit | authenticated user; private projections scoped | direct/reload-safe for encoded dynamic ID | provenance, evaluation history/currentness, preparation/tracking summaries | loading/not-found, unavailable applicability, empty/history | PARTIAL: labelled tabs/sections; repeated card links need item context | NOT PERFORMED | RETAIN | E4, T1, B1 | no literal/reserved ID ambiguity allowed |
| `/jobs/:discoveredJobId/fit` Workspace Fit | Canonical | Current Fit and evaluation history | current evaluation and historical run snapshots | Workspace tabs > Fit | provider-free read; no provider call | authenticated user | direct/reload-safe encoded ID | current, historical, unknown applicability; evaluation/runtime attribution | loading/error/no evaluation/history unavailable | PASS/PARTIAL: semantic headings/status; visual review pending | NOT PERFORMED | RETAIN | E4, T1, B1 | preserve distinction between current and historical |
| `/jobs/:discoveredJobId/application` Workspace Application | Canonical | Prepare application from exact workspace target and show preparation history | shared `readPreparationPrerequisites` authority plus canonical preparation target | Workspace tabs > Application | provider-free history/readiness; explicit POST may call provider; downloads later | authenticated user | direct/reload-safe encoded ID | immutable preparation/candidate/target snapshots | history/readiness checking, not-ready/unavailable, POST conflict/error, success | PARTIAL: labelled form and statuses; visual action-name review remains | NOT PERFORMED | RETAIN | E4, T1, B2 | retain shared prerequisite semantics and Phase 9C accessibility follow-up |
| `/jobs/:discoveredJobId/tracking` Workspace Tracking | Canonical | Start/view manual tracking for preparations in this workspace | application tracking records/events | Workspace tabs > Tracking | provider-free scoped read; explicit status writes | authenticated user | direct/reload-safe encoded ID | preparation-linked status/event history | loading/error/untracked/start/pending | PASS/PARTIAL: labelled status controls and event history | NOT PERFORMED | RETAIN | E4, T1, T4, B2 | Phase 9C repeated-action review |
| `/applications` ApplicationsPage list | Canonical | Global preparation history and tracking summary | application preparation records and linked tracking | Workspace > Applications | provider-free GETs; no provider work | authenticated user | direct/reload-safe | immutable target/candidate snapshot and preparation history | loading/error/empty/retry as implemented | PARTIAL: card headings link; generic secondary links repeat | NOT PERFORMED | RETAIN | E1, E5, T4, B2 | disambiguate repeated preparation/tracking actions later |
| `/applications/:preparationId` ApplicationsPage detail | Canonical | Inspect preparation, review and download artifacts | owner-scoped preparation snapshot/documents | reached from Applications, Workspace, Tracking | provider-free detail/review/download; no provider call | authenticated user; cross-user access neutral/not found | direct/reload-safe encoded ID | exact preparation inputs, attribution, snapshots, questions, downloads | loading/not-found/error/download status | PARTIAL: headings/form controls labelled; download names need review | NOT PERFORMED | RETAIN | E5, T4, B2 | canonical detail remains `/applications/:id` |
| `/tracking` TrackingPage list | Canonical | Global manually recorded tracking records | tracking records/events | Workspace > Tracking | provider-free GET; explicit status writes only on detail | authenticated user | direct/reload-safe | current status and linked event history | loading/error/empty/retry | PARTIAL: repeated “Open tracking history” and “Open preparation” names need item context | NOT PERFORMED | RETAIN | E5, T4, B2 | Phase 9C screen-reader action names |
| `/tracking/:trackingId` TrackingPage detail | Canonical | Inspect/update recorded application event history | owner-scoped tracking record and revision/event authority | reached from Tracking, Applications, Workspace | provider-free detail; explicit status-event write with revision control | authenticated user; cross-user access neutral/not found | direct/reload-safe encoded ID | historical target, current recorded status, ordered event history | loading/not-found/error, pending, CAS conflict | PASS/PARTIAL: form labels/status history; visual review pending | NOT PERFORMED | RETAIN | E5, T4, B2 | canonical detail remains `/tracking/:id` |
| `/settings` | Compatibility redirect | Historical Settings entry point | router redirect to AI settings | none | replace redirect; no provider read | authenticated user | direct reload redirects to `/settings/ai` | preserves old links | no surface state | transparent redirect | NOT PERFORMED | COMPATIBILITY_ONLY | E1 | preserve until external links age out |
| `/settings/ai` AiSettingsPage | Canonical | Read/update model catalogue and AI settings | authenticated AI settings/config service | Workspace > Settings | catalogue/settings reads are provider-free; validation/model operations are explicit and may contact configured provider | authenticated user/settings scope | direct/reload-safe | effective configuration and operation records as exposed | loading/error/validation/unavailable | PARTIAL: controls labelled; operation detail density needs review | NOT PERFORMED | RETAIN | E1, T2, T3 | no provider calls from read-only catalogue path |

Master matrix disposition count: **26 rows** — **RETAIN 19**,
**CONSOLIDATE_LINK 1**, **COMPATIBILITY_ONLY 6**, **REMOVE_LATER 0**, and
**BLOCKED 0**.

### Route-safety findings

`parseJobSearchRoute` decodes one dynamic segment, rejects malformed encoding,
rejects reserved slugs (`find`, `inbox`, `history`, `opportunities`, and
`searches`), and does not treat malformed paths such as `/jobs/%` or nested
reserved paths as workspace IDs. The workspace links encode the persisted
`discovered_job_id`; no literal placeholder is used. These protections are
covered by the Issue #236 route tests in T1.

The route table uses replace redirects for `/`, `/cv`, `/adviser`, `/jobs`,
`/jobs/searches`, and `/settings`. Unknown paths fall back to Profile. Auth
replacement is handled by `Protected` and the API session-clear path; T1/T2
also cover stale responses not restoring data after a replacement session.

## Navigation and duplicate-concept audit

The primary Workspace navigation is Home, Profile, Job Search, Applications,
Tracking, and Settings. Job Search is active for `/jobs` and every `/jobs/*`
route, so Workspace tabs retain context across Find, Inbox, opportunities,
history, and Workspace. Profile secondary navigation is Overview, CV, and
Career Adviser. Job Search secondary navigation is Find jobs, Inbox, My
opportunities, and Search history; My opportunities has a nested Recommended /
Current analyses versus Shortlisted navigation. Workspace tabs are Overview,
Fit, Application, and Tracking.

There are no competing canonical routes in the route table. The duplicate
conceptual entries are compatibility aliases and a small number of internal
links that still point at the root compatibility redirect: `CvPage.tsx` uses
`href="/"`, and `ExternalPreparationForm.tsx` uses `to="/"` for Profile
remediation. They function today because `/` redirects to `/profile`, but they
are CONSOLIDATE_LINK inventory, not evidence that `/` owns Profile.

The conceptual separation is explicit in the UI and tests:

- Inbox is shared persisted public vacancy input and is not a live search or
  shortlist.
- Recommended is ordered by current backend analyses.
- Shortlisted is the durable user decision projection and is independent of
  recommendation, Fit, and evaluation history.
- Search history is the durable run/exact-result surface.
- Workspace is the per-job join surface, not a second job identity.

## Core journey transition audit

| Transition | Actual path and state | Identity required | Result |
| --- | --- | --- | --- |
| Profile → Find | Profile/Home links go to `/jobs/find`; SearchIntent is transient until explicit save/run | no internal ID | PASS |
| Find → Inbox | Job Search nav keeps shell mounted; transient SearchIntent is displayed on Inbox | no internal ID | PASS |
| Inbox → Review | selected persisted `discovered_job_id` is evaluated explicitly; exact result remains visible after refresh/navigation | discovered job ID for the selected job | PASS |
| Review → Analyse | Inbox/result links open `/jobs/:discoveredJobId` and `/fit`; exact evaluation is not fabricated when unavailable | discovered job ID; evaluation identity is shown where available | PASS |
| Analyse → Recommended | current evaluation projection is read from opportunities; no implicit provider rerun | no extra UI ID beyond discovered job/evaluation references | PASS |
| Recommended → Shortlist | explicit decision mutation updates decision authority and refetches the projection | discovered job ID plus revision for CAS | PASS |
| Shortlist → Workspace | encoded discovered job ID opens the same workspace | discovered job ID | PASS |
| Workspace → Preparation | Application tab uses the shared preparation prerequisite authority, canonical preparation endpoint, and exact workspace target | discovered job ID; preparation ID after creation | PASS |
| Preparation → Tracking | preparation history links to `/tracking/:trackingId`; workspace can start tracking | preparation ID and tracking ID | PASS |

The journey needs internal identifiers only where the user is opening a specific
persisted job, evaluation, preparation, or tracking record. It does not need a
second route identity or a literal workspace slug. No parity blocker was found
in the core journey after verifying the shared Workspace Application readiness
controller.

## Authority and contradiction audit

| Domain | Intended/current authority | Evidence | Finding |
| --- | --- | --- | --- |
| Candidate Profile | canonical profile snapshot/revision and onboarding status for readiness | E1, T2 | Profile, Home, and Workspace Application are aligned through the shared preparation prerequisite authority. |
| SearchIntent | transient editor state until an explicit saved configuration/run | E3, T1, T3 | Aligned; SearchIntent is not treated as eligibility/evidence. |
| Search configuration/history | user-owned saved schedules, executions, discovery runs and immutable run/job snapshots | E3, B1, T3 | Aligned and provider-free for history reads. |
| Fit/evaluation | current opportunity/evaluation projection plus historical run snapshots, with currentness/provenance explanation | E3, E4, B1, T1 | Aligned; current, historical, and unknown applicability remain distinct. |
| Decisions | user decision rows/projections with revision/CAS authority | E3, B1, T1 | Aligned; Shortlisted does not read Recommended as its source. |
| Applications | preparation record and immutable candidate/target/input snapshots | E4, E5, B2, T4 | Aligned for history/detail/downloads; explicit preparation is the only write path. |
| Tracking | tracking record revision and ordered status events | E4, E5, B2, T4 | Aligned; no employer activity is inferred. |

### Blocking contradiction

`JobWorkspacePage.tsx` contains a local `ApplicationReadiness` read that calls
onboarding status and the legacy profile endpoint together. The shared
Applications flow has its own readiness controller and the Home/Profile paths
use canonical readiness/snapshot contracts. The direct Workspace path can
therefore classify a candidate differently from the shared authority, especially
when the profile read is unavailable or a candidate-not-ready condition has
precedence. Existing T1 tests explicitly document the behavior. This is a
Phase 9B implementation candidate, not changed by this audit.

## History, deep-link, and provenance audit

| Concern | Evidence-backed behavior | Result |
| --- | --- | --- |
| Search runs and saved execution | `/jobs/history` and `/jobs/find/saved` load bounded persisted summaries, then lazy detail; interrupted POSTs remain neutral and do not invent run identity | PASS |
| Exact run/job results | `?run=` and `?job=` are normalized, direct detail can be opened outside the bounded list, and closing removes only the relevant query | PASS |
| Current versus historical evaluation | Fit and history label current, historical, unknown, and unavailable applicability without reclassifying a historical record as current | PASS |
| Runtime attribution/provenance | Current evaluation and preparation surfaces expose persisted attribution where available; backend bounded projections protect ownership | PASS |
| CV/source history | Profile source history is independent of the canonical snapshot and retryable; CV source/detail is historical metadata, not a current-profile replacement | PASS |
| Preparation snapshots | preparation detail/downloads use persisted candidate, target, input, attribution, questions, and document snapshots; later live changes do not rewrite history | PASS |
| Questions and downloads | preparation detail presents bounded questions and provider-free document downloads; unsupported/fabricated claims are rejected by backend tests | PASS |
| Tracking history | detail shows historical target, current recorded status, revision, and ordered status events; status writes use revision control | PASS |
| Compatibility deep links | root, `/cv`, `/adviser`, `/jobs`, `/jobs/searches`, `/settings`, and legacy detail links route through replace redirects or canonical owner-scoped detail pages | PASS with staged cleanup |

## Provider-free and read-only matrix

| Surface | Read dependency | Provider/network on read | Read-only expectation | Evidence |
| --- | --- | --- | --- | --- |
| Home | `/api/v1/onboarding/status` | none | readiness only; no profile/jobs/apps reads | T1 |
| Profile reads/history | snapshot, active revision, structured provenance/source history | none | canonical data and historical metadata only | T2, B1 |
| Inbox | persisted inbox read model | none | public persisted vacancies; evaluation is a separate explicit action | T1, B1 |
| Recommended | opportunities projection | none | current persisted analyses only | T1, B1 |
| Shortlisted | scoped decisions projection | none | decision authority only; no opportunities/history dependency | T1, B1 |
| Search history | runs, execution history, exact run/job detail | none | persisted snapshots and attribution | T1, T3, B1 |
| Workspace Overview | shared job facts/provenance/decisions/application projection | none | read-only facts and projections | T1, B1 |
| Workspace Fit | current/historical evaluations | none | no rerun during read | T1, B1 |
| Workspace Application history/readiness | preparations plus readiness/profile reads | none | history is read-only; preparation POST is explicit | T1, B2 |
| Workspace Tracking | tracking record/events | none | existing record/history read-only; status event is explicit | T4, B2 |
| Global Applications/detail/download | preparations/tracking summaries and documents | none | no provider on list/detail/review/download | T4, B2 |
| Global Tracking/detail | tracking records/events and preparation link | none | no provider on list/detail | T4, B2 |
| Settings catalogue | configured model catalogue/effective settings reads | no live provider for catalogue reads | read-only catalogue remains available without model call | T2, T3 |

Backend evidence includes explicit no-provider/no-SQL-write checks for Jobs read
models, provider-free workspace reads, provider-free preparation history and
downloads, and provider-free tracking reads. The audit did not run a live
provider or network validation.

## User scoping and privacy audit

Candidate snapshots, revisions, CV/source history, search configurations,
discovery runs, evaluations, decisions, preparations, documents, tracking
records, and event histories are accessed through authenticated user scope in
the inspected route/services. The backend tests cover owner and cross-user
neutral 404 behavior, strict decision scoping, bounded historical projections,
preparation download isolation, and tracking detail/list isolation (B1/B2).

Workspace public vacancy facts may be shared persisted source data, but private
Fit history, decisions, applications, preparations, and tracking projections
remain user-scoped. The UI does not display internal evidence identifiers in
Job Search detail. No candidate-specific production data or fixture was added
by this audit.

## Loading, error, readiness, and stale-state audit

Major surfaces consistently use loading status text, `role="alert"` for
recoverable errors, retry controls where the request can be retried, and
neutral unavailable/stale language where transport completion is uncertain.
Jobs list sections are independently loaded; a failed retry does not erase
other successfully loaded sections. Session generation guards prevent delayed
responses from restoring data after logout/user replacement. History and exact
result flows distinguish HTTP rejection, transport interruption, and stale
history rather than claiming a provider action succeeded.

Workspace Application readiness is included in the provider-free/read-only
evidence because `readPreparationPrerequisites` performs the shared onboarding
and profile reads, preserves candidate-not-ready precedence, maps profile
failures safely, and protects against session/user replacement. T1 directly
covers profile-missing authority, candidate-not-ready precedence, unavailable
retry, unavailable-to-ready transition, and stale prerequisite responses.

## Accessibility audit (audit only)

| Major surface | Status | Evidence/finding |
| --- | --- | --- |
| Primary Workspace navigation | PASS | labelled navigation, active route styling/`aria-current`, keyboard-native links; E1/T1 |
| Profile secondary navigation | PASS | labelled navigation and stable links; T2 |
| Job Search secondary navigation | PASS | labelled navigation, explicit active sections, nested opportunity tabs; E2/T1 |
| Home/Profile/CV/Adviser | PARTIAL | headings, labels, status/alert regions are present; repeated generic action text and long prose need contextual-link review |
| Inbox/Recommended/Shortlisted cards | PARTIAL | non-actionable states remain readable and disabled; repeated “Open workspace”, “View Fit”, and “Open vacancy” names are not item-specific |
| Search history/saved schedules | PARTIAL | labelled forms and status regions; dense nested history actions need keyboard/screen-reader review |
| Workspace tabs and panels | PASS | labelled tab-like navigation, semantic headings, status/error regions; E4/T1 |
| Applications and Tracking lists/details | PARTIAL | form labels/status history are present; repeated “Open tracking history”/“Open preparation” links need item context |
| Settings | PARTIAL | labelled settings controls and status cards; operation catalogue density needs review |

No accessibility fixes are included in Phase 9A. PARTIAL findings feed Phase
9C. No surface was marked NOT_TESTABLE_AUTOMATICALLY because the source and
existing semantic test evidence were sufficient for this source-level audit;
interactive assistive-technology verification is still a Phase 9C activity.

## Responsive audit

The source includes max-width 640px rules for wrapping the primary nav,
single-column Workspace/Profile/revision layouts, stacked headings/actions,
wrapping long identifiers, and auto-fit grids. These are promising safeguards,
but no 390px screenshot or manual visual inspection was performed. Therefore
every 390px status in the master matrix is exactly `NOT PERFORMED`; this audit
does not claim responsive visual evidence and does not change CSS.

## Staged consolidation inventory

| Candidate | Current role | Staged disposition | Phase 9B impact | Rollback |
| --- | --- | --- | --- | --- |
| `/` → `/profile` | root compatibility redirect; two internal remediation links still use it | CONSOLIDATE_LINK, keep alias | `ExternalPreparationForm.tsx`, `CvPage.tsx` link destinations and focused assertions only | restore the two link destinations; redirect remains |
| `/cv` → `/profile/cv` | CV compatibility alias | COMPATIBILITY_ONLY | no immediate change | retain redirect |
| `/adviser` → `/profile/adviser` | Adviser compatibility alias | COMPATIBILITY_ONLY | no immediate change | retain redirect |
| `/jobs` → `/jobs/find` | Jobs compatibility alias | COMPATIBILITY_ONLY | no immediate change | retain redirect |
| `/jobs/searches` → `/jobs/find/saved` | saved-search compatibility alias | COMPATIBILITY_ONLY | no immediate change | retain redirect |
| `/settings` → `/settings/ai` | Settings compatibility alias | COMPATIBILITY_ONLY | no immediate change | retain redirect |
| `/applications/:preparationId` | canonical preparation detail | RETAIN | no replacement proposed | no migration |
| `/tracking/:trackingId` | canonical tracking detail | RETAIN | no replacement proposed | no migration |
| Workspace dynamic routes | canonical per-job join surface | RETAIN | no route change; retain shared preparation prerequisite authority | revert only a future focused change if needed |

No cleanup, deletion, route removal, data migration, new lifecycle, or
component consolidation is performed by this audit.

## Audit-test evidence

No new test was necessary: the authorized baseline already has focused evidence
for route compatibility, direct Workspace paths, dynamic/reserved/malformed
IDs, history and provenance, shortlist separation, non-actionable rows,
provider-free reads, user scoping, loading/error states, stale/session
replacement, and the current readiness behavior. The evidence is reused from
T1–T4 and B1–B2. New audit-test count: **0**. Reused focused test evidence:
**more than 40 named frontend/backend tests** across the cited suites.

## Validation record for this audit branch

| Check | Result |
| --- | --- |
| Focused frontend route/parity suites | PASS — 6 files, 353 tests |
| Full frontend suite | PASS — 15 files, 572 tests |
| Frontend TypeScript typecheck | PASS |
| Frontend production build | PASS; Vite emitted only the existing large-chunk advisory |
| Focused backend parity/scoping/provider-free suites | PASS — 110 tests |
| Full backend suite | 1,089 passed, 1 unrelated failure |
| Isolated rerun of the unrelated failure | PASS — `tests/test_candidate_adviser_profile_proposals.py::test_list_is_user_scoped_newest_first_and_bounded` |
| `git diff --check` | PASS |

The full-backend failure was in an unchanged Candidate Adviser proposal test,
outside this audit’s source and scope. Its isolated rerun passed. The branch
contains only this audit document relative to the authorized baseline; no
production source change could have caused that failure.

## Conclusion

### A. Current parity verdict

**PARITY_CONFIRMED_WITH_FOLLOWUPS** — canonical route ownership, navigation,
deep-link identity, historical/read-only behavior, shortlist separation,
provider-free reads, and user scoping are substantially aligned. Sign-off is
with the documented internal-link consolidation follow-up and the Phase 9C
accessibility/responsive follow-ups.

### B. Blocking defects

None.

### C. Phase 9B candidates

1. **Compatibility-link consolidation:** update the two internal root Profile
   links in `frontend/src/CvPage.tsx` and `frontend/src/ExternalPreparationForm.tsx`
   to `/profile`, with focused route assertions. Rollback is restoring those
   link destinations; retain `/` as compatibility-only.

### D. Phase 9C accessibility/responsive follow-ups

Review repeated card action names with item context, saved-history density,
download/action labels, keyboard order/focus after async transitions, and
screen-reader announcements. Perform an actual 390px and desktop visual pass
for navigation wrapping, Workspace tabs, long vacancy IDs, history tables/cards,
preparation forms, downloads, and Tracking event history. Record screenshots or
manual observations before changing CSS.

### E. Phase 9D sign-off checklist

- [ ] Internal Profile remediation links use `/profile` while `/` remains a compatibility redirect.
- [ ] Focused route/deep-link/history/decision/provider-free/scoping tests pass.
- [ ] Compatibility aliases still redirect with replace semantics.
- [ ] No duplicate canonical route owns Profile, Job Search, Applications, or Tracking.
- [ ] Accessibility follow-ups are resolved or explicitly accepted.
- [ ] 390px and desktop visual evidence is recorded.
- [ ] Full frontend/backend suites, typecheck, and production build pass.
- [ ] `git diff --check` is clean.
- [ ] Review confirms no Phase 9+ scope, route/API/schema migration, deletion, or live-provider validation was introduced.

### F. Deferred and out of scope

Phase 9A does not add `/jobs/find`, Profile secondary navigation, Home behavior,
CV or Adviser workflow changes, backend/API/schema changes, lifecycle changes,
data deletion/migration, CSS/accessibility fixes, new job discovery behavior,
or live provider/network validation. Phase 9B implementation, Phase 9C visual
and accessibility work, and Phase 9D sign-off remain deferred.

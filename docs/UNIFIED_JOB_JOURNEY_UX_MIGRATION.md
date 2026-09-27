# Unified Job Journey UX: information architecture and migration contract

**Status:** Phase 1 design contract only. This document describes a target and an incremental migration; it does not authorize or implement production UI, API, or schema changes.

**Evidence baseline:** repository state at `4eee48efa700de7024300de1c7198544b37a5f0e`. Current-state statements below are grounded in the named frontend and backend files. Proposed routes are contracts, not routes that exist today.

## 1. Purpose and product model

The target journey is:

```text
Profile → Find Jobs → Review → Analyse → Shortlist → Prepare → Apply → Track
```

The UX should make this one job-centred journey legible without collapsing distinct backend records into a made-up lifecycle. A public vacancy, a user's evaluation, an execution/run, a preparation snapshot, and a tracking record have different identities and retention rules. They must remain distinguishable in UI labels and links.

This is a presentation and orchestration contract. The migration should reuse existing services and authorities wherever possible, and should request only small, justified additions for durable user decisions and search-context inheritance.

## 2. Current-state map

### 2.1 Existing navigation and routes

The authenticated navigation is rendered in `frontend/src/App.tsx` (`AppShell`). It currently exposes Profile at `/`, CV at `/cv`, Career Adviser at `/adviser`, Jobs at `/jobs`, Applications at `/applications`, Tracking at `/tracking`, Saved searches at `/jobs/searches`, and Settings at `/settings/ai`. `/settings` redirects to `/settings/ai`. Login and registration are `/login` and `/register`. Preparation and tracking details use `/applications/:preparationId` and `/tracking/:trackingId`. Unknown routes redirect to `/`.

`/jobs` has no URL-addressable tab: Opportunities, Discovery runs, and Recent vacancies are React state inside `JobsPage`, so refresh/deep-link cannot select one of those views. `/jobs/searches` is a separate full page, not a tab within Jobs. Execution history is expanded inline per saved schedule. No job-centric workspace route currently exists.

### 2.2 Existing pages and responsible components

| Current surface | Route/component | Current responsibility and migration note |
|---|---|---|
| Primary shell | `AppShell` in `frontend/src/App.tsx` | Authenticated navigation and brand link; target navigation replaces the current flat CV/Adviser/Saved searches links. |
| Profile and readiness | `ProfileHome`, `OnboardingCard`, `ProfileReadView`, `ProfileRevisionWorkflow` in `frontend/src/App.tsx` and `frontend/src/ProfileRevisionWorkflow.tsx` | Current `/` workspace reads canonical candidate snapshot, readiness, profile revision workflow and provenance. It is not a dashboard today. Keep readiness gates and revision actions. |
| CV | `CvPage` in `frontend/src/CvPage.tsx` | Upload, interpret, review, overlap resolution, confirm, resume latest draft, and inspect CV history/source. Confirmed candidate context remains authoritative; a pending newer CV does not replace the confirmed context. |
| Career Adviser | `AdviserPage` in `frontend/src/AdviserPage.tsx` | Readiness-gated intake, assessment review/confirmation, clarifications, and profile proposals. Onboarding links point to `/cv` and `/adviser`. |
| Job inbox / recent vacancies | `JobsPage` Recent vacancies tab | Lists recent shared persisted public vacancies from `/api/v1/jobs/inbox`, with lifecycle/actionability/provenance and selection for evaluation. It explicitly is a recent shared slice, not all jobs or live search. |
| Current recommendation summary/detail | `JobsPage` Opportunities tab and `OpportunityDetail` | Displays the accumulated current evaluation/recommendation projection and full current evaluation detail. It is not an explicit user shortlist, nor necessarily the result just analysed. “Current” means current candidate/evaluation contract and current job content. |
| Discovery-run history | `JobsPage` Discovery runs tab, `RunSnapshot`, `RunRows` | Shows historical run funnel/outcomes and per-run job/evaluation details including runtime attribution. Keep as inspectable history, not the primary job lifecycle. |
| Saved discovery configuration | `JobsSearchesPage` in `frontend/src/JobsSearchesPage.tsx` | Creates/edits/pauses user-owned schedules, executes persisted configuration via Run now, and shows execution history. Configuration editing does not itself execute discovery. |
| Application preparation list and direct preparation | `ApplicationsPage` / `ExternalPreparationForm` in `frontend/src/ApplicationsPage.tsx` | Lists immutable generated preparation records and also supports preparing a supplied vacancy directly, outside the ranked-opportunity flow. Keep this path meaningful in Applications/Workspace and subject it to the same candidate-readiness and target-validation boundaries. |
| Preparation review/detail | `ApplicationDetailPage` and `ApplicationTrackingControl` in `ApplicationsPage.tsx` | Reads a saved package, reviews evidence/citations and artifacts, downloads CV/cover-letter files, and offers tracking creation/current tracking link. A review decision is not an employer submission. |
| Tracking list/detail | `TrackingPage`, `TrackingDetailPage` in `frontend/src/TrackingPage.tsx` | Manually records application status and append-only events; explicitly does not submit to employers or infer employer activity. Detail links back to the preparation. |
| Settings / AI models | `AiSettingsPage` in `frontend/src/AiSettingsPage.tsx` | Catalog and user AI preference/settings management at `/settings/ai`; preserve access under Settings. |
| Runtime attribution | `RuntimeAttributionPanel` in `frontend/src/RuntimeAttributionPanel.tsx` | Presents runtime provenance in job/evaluation views; preserve this evidence in the workspace and history. |

### 2.3 Existing backend/API map

All paths below are under `/api/v1`. Authenticated user-owned read/write endpoints are scoped through `CurrentUser` and the relevant service; public vacancy inbox/import endpoints are authenticated but describe shared vacancy records. Endpoint definitions are in `backend/app/api/routes/jobs.py`, `applications.py`, `application_tracking.py`, `profile.py`, `cv_ingestion.py`, `candidate_adviser.py`, `onboarding.py`, and `ai_settings.py`.

| Existing API surface | Current source of truth and present use | Target classification |
|---|---|---|
| `GET /jobs/inbox?limit=` | Recent shared imported public vacancy inbox; `OpportunityInboxService`; bounded slice and lifecycle/actionability/provenance. | **REUSE** for Inbox; compose selection/navigation in frontend. Not a search result or user-specific saved list. |
| `POST /jobs/import-discovered`, `POST /jobs/external-discovery/search-context` | External runtime imports bounded factual job data; search-context endpoint returns caller's compact search profile plus submitted `JobSearchQuery`. It does not execute Codex. | **REUSE** at the host-side integration seam; external tooling remains the initiator. Do not call this from a browser action that would imply launching Codex. |
| `POST /jobs/discover-ats`, `/jobs/discover-agentic`, `/jobs/discover-agentic-me`, `/jobs/discover-and-rank` | Structured ATS acquisition or provider-backed bounded agentic acquisition; service composition differs and some endpoints are lower-level/legacy. | **WRAP/COMPOSE** behind saved search and Find Jobs UX; choose an approved supported orchestration path, do not expose every execution primitive as user navigation. |
| `POST/GET/PATCH /jobs/discovery-schedules`, `POST .../{id}/run-now`, `GET .../{id}/executions` | User-owned recurrence/query/acquisition/evaluation configuration and immutable execution snapshots/history (`DiscoveryScheduleService`, `ScheduledDiscoveryExecutionService`). | **REUSE** for saved searches and execution history. **WRAP/COMPOSE** into Job Search; no new scheduler authority. |
| `POST /jobs/discovery-runs`, `GET /jobs/discovery-runs`, `GET /jobs/discovery-runs/{id}`, `GET .../{run}/jobs/{job}` | User-owned evaluation run history, query/run input, funnel, outcomes, historical job/evaluation detail and runtime attribution. | **REUSE** for advanced/search history and Workspace history. Keep old runs immutable. |
| `GET /jobs/opportunities?limit=`, `GET /jobs/opportunities/{evaluation_id}` | Current ranked analyses/recommendations and current evaluation detail for the authenticated user; not a “just analysed” result token or saved user shortlist. | **REUSE** for the **Recommended / Current analyses** subsection and Workspace current Fit. Keep separate from Shortlisted. Add a latest-analysis result projection only if composition cannot reliably identify the just-completed evaluation. |
| `POST /applications/prepare`, `GET /applications`, `GET /applications/{id}`, `GET /applications/{id}/review`, `GET /applications/{id}/{cv.docx,cv.pdf,cover-letter.docx,cover-letter.pdf}` | `ApplicationPreparation` immutable user-owned target/identity/result snapshot, review/citation presentation and rendered downloads. | **REUSE** for Prepare and Applications; **WRAP/COMPOSE** in a job workspace. Do not mutate historical snapshots when job/profile changes. |
| `POST/GET /application-tracking`, `GET /application-tracking/by-preparation/{id}`, `GET /application-tracking/{id}`, `POST .../{id}/status-events` | Current state plus append-only events for a tracking record linked to one preparation. Unique constraint currently allows one tracking record per preparation. | **REUSE** for Tracking and Application projection. Any one-preparation-to-many-tracking change is **DEFER** pending product/data-model decision. |
| `GET /onboarding/status`, `GET /profile/snapshot`, `GET /profile/context-summary`, `GET /profile`, Profile revision endpoints | Readiness/canonical candidate context/profile and manual revision lifecycle. Direct legacy Profile POST/PATCH are 409 barriers. | **REUSE** for Profile overview and fail-closed readiness gates. No direct profile-write path is introduced. |
| `/cv-ingestion/*` upload/read/patch/interpret/confirm/overlap-review; `/candidate-adviser/*` intake, assessment, clarification and proposal operations | CV and Adviser owned workflows; they remain separate domains even when nested in Profile navigation. | **REUSE** behind Profile secondary navigation and onboarding deep links. |
| `GET/PUT /ai/settings`, `GET /ai/models` | AI catalog and settings. | **REUSE** for Settings. |
| Manual `/jobs/analyse`, `/jobs/match-me`, `/jobs/rank-me` and related lower-level endpoints | Single-text or in-memory matching/ranking seams, not the persisted run/job workspace contract used by `JobsPage`. | **DEFER** from primary IA unless a later product requirement explicitly chooses them. |

## 3. Target information architecture

Primary authenticated navigation is exactly:

```text
Home · Profile · Job Search · Applications · Tracking · Settings
```

Proposed canonical paths are `/home`, `/profile`, `/jobs/find`, `/jobs/find/saved`, `/jobs/inbox`, `/jobs/opportunities/shortlisted`, `/jobs/opportunities/recommended`, `/jobs/history`, `/applications`, `/tracking`, and `/settings/ai`. `/jobs/find` is the canonical Job Search landing/Find jobs route; `/jobs` is a compatibility redirect to `/jobs/find`; Inbox remains `/jobs/inbox`. My opportunities is a decision-oriented workspace: Shortlisted is the explicit user-decision view, and Recommended / Current analyses is the distinct projection backed by the existing `/jobs/opportunities` API. Job Workspace routes are `/jobs/:discoveredJobId` with stable, typed subroutes or tabs `overview`, `fit`, `application`, and `tracking` (implementation should keep a canonical URL for selected subsection, e.g. `/jobs/:id/fit`). The route contract is subject to route collision review before Phase 2; use literal routes before a parameter route.

Job Search secondary navigation:

```text
Job Search
  Find jobs
  Inbox
  My opportunities
    Shortlisted
    Recommended / Current analyses
  Search history
```

Saved discovery configurations move inside Find jobs at the canonical route `/jobs/find/saved`; `/jobs/searches` remains a legacy compatibility alias/deep link. Technical execution details remain under Search history / a schedule's execution-history disclosure. They must not be presented as the user's job journey itself.

### 3.1 Home vs Profile contract

**Home (`/home`) is a bounded next-action dashboard**, not a second profile or another candidate authority. It summarizes: readiness/onboarding tasks; most recent completed analysis with a direct result link; saved-search execution status or host-side import instructions; latest preparation and tracking follow-up. A shortlisted count/card is omitted or disabled until the durable shortlist authority is implemented and authoritative. Every card is a projection linked to its owning surface. Home does not persist copied Profile/job/application status.

**Profile (`/profile`) owns the candidate-context workspace UI**: current confirmed Profile and canonical structured candidate context, readiness explanation, revision workflow, lineage/provenance, and secondary navigation to CV and Career Adviser. Keep the current `/` Profile page behavior reachable: `/` redirects to `/profile`, and pre-existing root links/bookmarks therefore still resolve to the Profile workspace. New login success and brand navigation go to `/home`. This preserves the old route's meaning while making Home explicit.

### 3.2 Profile secondary information architecture

```text
Profile
  Overview     /profile
  CV           /profile/cv
  Career Adviser  /profile/adviser
```

The Profile Overview contains canonical current candidate context and the existing revision/edit/confirm flow. No separate revision route is required in Phase 2; if implementation later needs a dedicated route, deep-link to a revision ID while leaving `/profile` as the overview. CV upload/review/history stays in `CvPage`; Adviser intake/assessment/clarifications/proposals stays in `AdviserPage`.

`/cv` and `/adviser` remain direct compatibility aliases using replace navigation to `/profile/cv` and `/profile/adviser`. Onboarding links are updated to canonical nested paths, but existing email/bookmark/in-app URLs remain functional. Readiness gates remain in the CV/Adviser flows and the shared onboarding dashboard; moving navigation does not imply Adviser completion is mandatory for job evaluation when current product rules make it optional.

## 4. Find Jobs and discovery execution boundary

### 4.1 Browser contract

A browser action **cannot invoke the user's host-side local Codex CLI in the intended web deployment model**: the backend is a web service and must not inherit or proxy desktop ChatGPT/Codex credentials. `CodexExternalDiscoveryService` and the external import/search-context service boundary are host-side integration; the backend import endpoints accept bounded factual output and do not start a Codex runtime. Do not put Codex credentials in browser storage, send them to the API, or replace this seam with a paid web-search provider.

Therefore browser **Find jobs** means:

1. set/reuse one search-intent object;
2. choose an available acquisition channel explicitly (structured ATS or configured provider-backed agentic discovery/saved execution);
3. show its actual channel, readiness and result state;
4. open Inbox to review imported/current persisted listings and submit selected eligible job IDs for evaluation;
5. for broad host-side Codex discovery, present concise instructions to run the user's approved local Codex workflow and return/import the bounded result into this same Inbox.

The browser must never show a “Run Codex search” button that implies a remote browser request launched local CLI. When a saved search has only host-side broad discovery configured, label it “Run with Codex on this device” (instructions/status, not executable web action), explain that no browser execution started, and keep the saved configuration usable for criteria reuse and import attribution. Scheduled browser channels remain separately identifiable. Do not imply ATS and Codex are the same acquisition mechanism; unify their resulting public listings in Inbox and visibly retain provenance/runtime.

### 4.2 Feedback and deferrals

If the required local host is not available, show the search criteria, saved-search name, last imported matching result time if derivable, and the next action (run local Codex then import). If there are no results yet, distinguish “not run here” from “completed with zero results.” If ATS/provider readiness is unavailable, fail closed on execution and retain read/history routes.

Phase 2 defers browser-to-host IPC, desktop-agent control, any Codex credential forwarding, paid web-search substitution, provider/model choice redesign, and a unified backend acquisition state machine. Host-side Codex search initiation itself remains external; only its imported results participate in the same Job Search workspace.

## 5. Persistent Job Workspace contract

**Stable identity:** canonical `DiscoveredJob.id` (`discovered_jobs.id`) is the shared-vacancy identity, subject to existing identity-key canonicalization. A user workspace is a read projection keyed by `(authenticated user_id, discovered_job_id)` and its related run participations, evaluations, preparations and tracking. Do not key the workspace by evaluation ID, URL alone, run ID, or preparation ID.

```text
Job Workspace
  Overview       canonical vacancy facts, current actionable state, provenance and actions
  Fit            current evaluation plus inspectable evaluation history/runtime attribution
  Application    immutable preparations for this job, their review/download and preparation-to-tracking links
  Tracking       tracking records/events reached through preparations for this job
```

Sections exist independently as evidence permits: Overview exists for a persisted vacancy; Fit shows an explicit “not analysed for your current candidate/job version” state until an applicable evaluation exists; Application is empty until a preparation exists; Tracking is empty until a preparation has a tracking record. These are derived availability states, not a new backend lifecycle.

After an analysis request completes, show the exact returned `DiscoveryRunRead` and latest run-job evaluation first in a **“Analysis just completed”** result panel with status/outcome and timestamp. Link that evaluation/run to Workspace. Refresh/compose Recommended / Current analyses separately; do not imply the accumulated ranking pool equals this one request or the explicit shortlist. For an outcome with no evaluation, show the run outcome, not a fabricated Fit result. If response is interrupted, reconcile from run history and say its association is uncertain until resolved.

The current evaluation is the latest successful evaluation for this user/job whose `job_content_hash`, candidate-evaluation fingerprint and evaluation-contract fingerprint match current authority; reuse the existing service's equivalence/currentness rules. Older evaluations remain immutable and inspectable through evaluation history/run details. A changed job content hash makes prior evaluation history, not a current evaluation; it does not rewrite the saved result. Inactive/expired/unverified/non-actionable vacancy prevents new action where the existing lifecycle says so, but keeps Workspace, past evaluation, preparation and tracking history readable. A preparation remains a frozen historical snapshot even when current job/profile/evaluation changes. Tracking displays its own saved preparation target snapshot and append-only events, never silently substitutes current vacancy content.

## 6. UX lifecycle authority matrix

“Shortlisted” and “Dismissed” are currently not persisted candidate-job states. No `shortlist` or `dismissed` authority appears in the current models/services. They must not be faked using opportunity ranking order, discovery-run participation, preparations, or tracking status.

| UX state | Authority / persistence | Existing sufficiency / required contract | Multiple evaluations/preparations behavior |
|---|---|---|---|
| Discovered | Shared `DiscoveredJob` plus `DiscoveredJobProvenance`; persisted. Lifecycle/verification fields govern actionable status. | Existing canonical public vacancy authority is sufficient. | Many provenance records and run participations remain attached to same canonical job. |
| Reviewed | Derived, not persisted: user opened a detail/Inbox row during current session. | Phase 2 may show “viewed” transiently only. A durable reviewed mark needs a separately approved extension; no silent timestamp write. | Review refers to current UI session/canonical job, not a particular evaluation. |
| Analysed | `UserJobEvaluation` persisted immutable; `DiscoveryRun`/`DiscoveryRunJob` persist attempt and participation/outcome. | Existing authority sufficient. Current is computed against current job-content/candidate/evaluation fingerprints. | Keep every successful evaluation; highlight applicable current one; never overwrite prior result. |
| Shortlisted | Required target: one user-scoped job decision authority keyed by user + canonical job; durable/persisted after the Phase 7 separately approved extension. | **SMALL EXTENSION NEEDED** for durable explicit intent. This is the authoritative My opportunities → Shortlisted view after Phase 7; do not use evaluation recommendation or create competing global job state. Before then, omit/disable Shortlisted as a saved-state view and do not label the accumulated projection a shortlist. | Decision attaches to canonical job, independent of which evaluation is shown or how many preparations exist. UI can show the latest analysis alongside the user's decision. |
| Dismissed | Same proposed user-scoped job decision authority; persisted explicit user decision. | **SMALL EXTENSION NEEDED**, sharing the one decision authority with shortlist; no deletion from shared vacancy, histories or runs. | Hides from default active inbox/opportunity projection only; history and workspace stay available; an explicit undo returns to undecided. |
| Preparing | Derived/transient UI operation (request in progress); not a durable lifecycle state. | Existing preparation request and feedback suffice. | Multiple generated snapshots may exist for one canonical job. |
| Ready | `ApplicationPreparation` persisted, immutable generated snapshot (target, identity, result, fingerprints and attribution). | Existing authority sufficient; “Ready” means preparation saved/reviewable, not application submitted. | Each prep has its own ID and frozen evidence. New prep does not mutate older prep or tracking. |
| Applied | `ApplicationTrackingRecord.current_status` plus event history; persisted. | Existing tracking authority sufficient. Applications page may show Applied only as a joined projection from tracking. | Current application status is per tracked preparation, not per canonical job. Never infer it from preparation creation. |
| Interview | Tracking current status plus append-only `ApplicationTrackingEvent`; persisted. | Existing authority sufficient. | Same per-preparation behavior; prior events remain. |
| Offer | Tracking current status plus append-only events; persisted. | Existing authority sufficient. | Same per-preparation behavior. |
| Rejected | Tracking current status plus append-only events; persisted. | Existing authority sufficient. | Same per-preparation behavior; does not erase other preparations for the job. |
| Withdrawn | Tracking current status plus append-only events; persisted. | Existing authority sufficient. | Same per-preparation behavior. |

Each target projection must retain `user_id` scoping for user-owned evaluation, schedule, preparation, tracking, and future decision data. Shared vacancy facts/provenance remain distinct from private user decisions.

## 7. Applications vs Tracking authority

**Applications owns preparation artifacts and preparation history.** It may group by canonical job and show each immutable preparation as Draft/Ready only if those labels describe the preparation artifact itself. It owns neither an “Applied” status nor employer lifecycle.

**Tracking owns post-preparation lifecycle.** “Applied” in Applications is a derived, read-only projection of the linked tracking record's current status. Updating status happens only through Tracking's append-only event API, including from a deep link in Workspace. Application preparation POST creates a snapshot; it does not claim the user applied.

Currently `ApplicationTrackingRecord.preparation_id` is unique, and a tracking record points to exactly one preparation; therefore one preparation can have at most one tracking record, and this model cannot express multiple applications/targets per preparation. Phase 2 must honor this cardinality; the UX must not promise “one prep links to many tracked applications.” If product requires one-to-many or a tracking record independent of preparation, raise it as a separate data-model decision before implementation.

If old preparation A has no tracking and preparation B for the same job has tracking progressed to Interview, do not copy Interview onto A or treat the job-level application as globally Interview. Show B's linked lifecycle beside B, and show A as untracked. A job workspace may summarize “one tracked preparation at Interview” with a drill-through; it must not collapse distinct records into one status. When the saved preparation's target differs from current vacancy/evaluation, make its historical snapshot explicit and show current facts separately.

## 8. Search-context ownership and inheritance

Search intent is not candidate eligibility and is not evaluation evidence. It is the user's prioritisation/query criteria. `JobSearchQuery` currently holds keywords, locations, `remote_ok`, companies, excluded companies, excluded title terms, employment types and max-results. Candidate eligibility comes from the authenticated user's canonical candidate context/readiness. Evaluation evidence comes from job snapshot + candidate evidence and is fingerprinted separately. Do not use query terms as candidate facts, hard eligibility, or evidence supporting a fit claim.

### 8.1 Ownership matrix

| Context | Canonical owner / storage | Inheritance and history | Fallback | API surface / target consumers |
|---|---|---|---|---|
| Reusable user search intent (themes, locations, remote policy, exclusions, employment types) | Proposed frontend `SearchIntent` typed contract, composed from an explicitly selected saved `DiscoverySchedule.query` or current Find Jobs draft. Existing persistent owner is each schedule's `query_json`; there is no standalone user-default search-intent record today. | Editing a schedule changes future configuration only; each claimed execution stores `config_snapshot_json`. A manual run stores normalized query in `DiscoveryRun.search_input_json`. Never rewrite old snapshots. | Minimal explicit “General search” query selected by user; never silently invent eligibility. If no themes can be established, require user input before discovery. | Schedule GET/POST/PATCH; Find Jobs form; `/jobs/discovery-runs` query. |
| Discovery execution context | Each `ScheduledDiscoveryExecution.config_snapshot_json` or `DiscoveryRun.search_input_json`/`run_input`; persisted historical snapshot. | Immutable execution-time intent. Editing the saved schedule affects later runs only. | For imported job outside a known execution, leave origin context unknown rather than associate the latest arbitrary query. | Schedule execution history; discovery run history/detail. |
| Job-to-search participation | Proposed composed projection over `DiscoveryRunJob` and `ScheduledDiscoveryExecution.discovery_run_id`, not a mutable single context field on `DiscoveredJob`. Currently run participation connects run/job; scheduled execution optionally connects run; imported provenance is separate. | One canonical vacancy can participate in multiple runs/searches; show each historical context with the corresponding run. Do not overwrite a job's context when another search discovers it. | Imported job without matching execution can be analysed using a clearly labelled minimal/general context; preserve “originating search unavailable.” | Compose Inbox/Workspace from run participation and execution links. A small extension is needed only if schedule-to-job participation cannot be reconstructed through its linked discovery run. |
| User's most recent explicit search choice for manual Analyse Fit | Proposed client session state for navigation only; durable owner should be an explicit selection of a saved search and the run created from it. Do not store as canonical context on shared job. | Navigating from a search carries its exact context into the run request. Direct persisted-vacancy evaluation from Inbox asks the user to select saved search or use explicit General context; no hidden inference from last-used state across sessions. | Permit the current backend-required `JobSearchQuery` with explicit “General evaluation context” defaults only if product approves generic keyword derivation; show the exact derived/default query before submit and persist it in `DiscoveryRun`. | Find Jobs selection and `/jobs/discovery-runs` payload. Existing API requires a non-empty keywords list; there is no context-free run request. |
| External Codex search context | Host invokes `POST /jobs/external-discovery/search-context` with its selected query; authenticated response combines it with compact `CandidateSearchProfile`; host import posts bounded jobs plus query to `/jobs/import-discovered`. | The query is used for screening/bounding the import but is not currently stored as a durable per-job search association. Persisted `DiscoveredJobProvenance` keeps bounded factual source/runtime attribution, not search intent. Multiple import/run origins must not be collapsed. | If no context was submitted or no run relation exists, do not fabricate one from candidate profile or the latest unrelated search. | Host-side tool seam; surfaced through Inbox and persisted provenance, not browser execution. |

The only authoritative front-end representation during a flow should be the typed `SearchIntent` form state serialized to the existing `JobSearchQuery`; schedule form adapts to that type. Avoid parallel keyword/location/remote forms with slightly different meanings. Existing schedule `query_json`, execution snapshot, run query, and run-job relation remain their respective persistence/history authorities, not competing “latest intent” fields.

Conflicting contexts are normal: the same job may match several schedules. Display the originating run(s), their timestamps/names and criteria as provenance/history. Current Fit is independent of which search produced the job. My opportunities → Shortlisted is based only on explicit user decisions; Recommended / Current analyses is the evaluation projection; neither replaces the exact just-completed result, Inbox, or historical evaluations. When a saved search was edited after an old run, label the old run with its frozen criteria. A manually evaluated Inbox job with no usable originating run must offer saved-search selection or an explicit General context; show which was used in the resulting run.

## 9. Historical job-workspace behavior

| Accumulated condition | Required behavior |
|---|---|
| Multiple discovery provenances | Keep canonical job once; retain and expose bounded source/runtime provenance; do not pick one provenance as exclusive origin. |
| Multiple discovery-run participations | Show every run participation and per-run outcome through history; a run's stored query/funnel stays immutable. |
| Multiple evaluations over time | Highlight the latest evaluation current under matching job content, candidate and evaluation-contract fingerprints; keep earlier successful evaluations inspectable and unchanged. |
| Job content/hash changed | Mark old evaluation stale relative to current vacancy; retain its target snapshot/results. New analysis creates/reuses only under current fingerprint rules. |
| Latest completed analysis after submit | Prioritize exact run/job result in an immediate result panel; do not wait for the accumulated-opportunity refresh to communicate completion. |
| Accumulated opportunity pool | Label as current ranked opportunities across current applicable evaluations. It is distinct from the just-submitted run and from discovered inbox listings. |
| Inactive, expired, stale, unverified, or otherwise non-actionable vacancy | Disable new evaluation/preparation where current backend/service policy requires; retain read-only Workspace, run/evaluation history, immutable preparations, downloads and tracking records. Do not delete or rewrite history. |
| One or more preparations | List all preparation IDs/timestamps, each with its immutable target/identity/result snapshot and evidence review. Current job/profile changes do not regenerate or revise them. |
| Tracking history | Follow each record's preparation link and snapshot; current status is a projection of tracking and its append-only events. Do not infer status from the job's latest prep. |

“Latest” needs a visible basis (timestamp plus current/stale applicability), not a silent sort alone. “Current” is an applicability predicate, not a mutation of history.

## 10. API reuse/extension matrix by target capability

| Target need | Existing API/domain | Classification | Contract / seam |
|---|---|---|---|
| Find jobs via configured ATS and browser-supported discovery | discovery schedules, Run now, structured ATS/agentic endpoints | **WRAP/COMPOSE** | Find Jobs owns the SearchIntent UI; adapter calls existing schedule or an approved composition. Clearly identify acquisition channel and readiness. |
| Broad Codex discovery | external search-context + import | **REUSE** | Initiated externally on host; no web request invokes Codex. Import result joins shared vacancy inbox with provenance. |
| Review imported vacancies | `/jobs/inbox` | **REUSE** | Shared listing read; no implied user shortlist state. |
| Analyse selected job(s) | `/jobs/discovery-runs` and current run history/read APIs | **REUSE** | Exact run result is the immediate result; query is required and historical. |
| My opportunities → Shortlisted | No authority today | **SMALL EXTENSION NEEDED** | Required explicit user-intent destination. Phase 7 makes it authoritative only after the separately approved user-scoped decision-state extension exists; until then do not present the accumulated ranking as a shortlist. |
| My opportunities → Recommended / Current analyses | `/jobs/opportunities` and detail | **REUSE** | Existing current ranked/evaluation projection remains useful and readable, but is expressly not a saved shortlist and not the just-completed result, Inbox, or historical evaluations. |
| Candidate readiness/Profile | onboarding + canonical Profile reads/revisions | **REUSE** | Fail closed when readiness authority cannot be verified; preserve current no-direct-write Profile contract. |
| Prepare/review/download | applications endpoints | **REUSE** | Snapshot immutable; adapt detail in Workspace; no result mutation on later job/profile changes. |
| Track and append status | application-tracking endpoints | **REUSE** | Existing preparation-linked authority and append-only events. |
| Cross-surface job workspace aggregation | No single aggregate endpoint | **WRAP/COMPOSE** initially | Frontend composes user-scoped detail/history with existing bounded APIs only where efficient and ownership remains clear. If request fan-out/pagination cannot meet needs, specify a narrowly read-only projection endpoint with tests before expanding. |
| Search context associated with multiple search/run origins | run input + run-job relations; schedule execution-to-run link | **WRAP/COMPOSE**, possible **SMALL EXTENSION NEEDED** | Prefer relational projection; add only a user-scoped historical join/association if schedule participation cannot be reconstructed. Never write latest context onto shared `DiscoveredJob`. |
| Mark a durable review | None today | **DEFER** | “Reviewed” is transient UI state in this contract. Add persistent review only with separately validated user need. |

## 11. Canonical-route, deep-link, and component migration matrix

| Existing route / deep link | Target canonical route | Compatibility contract |
|---|---|---|
| `/` (currently Profile workspace) | `/profile` | Replace redirect to `/profile` so historic root bookmarks preserve meaning. New `/home` is explicit; update brand/login destination to `/home`. |
| `/cv` | `/profile/cv` | Replace alias; preserve CV draft ID state in backend, and if a future draft detail route exists, retain draft ID in URL. Onboarding CTA uses canonical path. |
| `/adviser` | `/profile/adviser` | Replace alias; preserve assessment/clarification/proposal IDs when those are later made routable. |
| `/jobs` | `/jobs/find` | Compatibility redirect/alias to the canonical Job Search landing and Find jobs route. Inbox remains `/jobs/inbox`; retain links to Inbox, My opportunities and Search history. |
| `/jobs/searches` | `/jobs/find/saved` | Legacy compatibility alias/deep link to the canonical saved-search route; open saved configurations, including selected schedule ID if included in a future URL. Existing history disclosures remain reachable. |
| `/jobs` in-page Opportunities tab | `/jobs/opportunities/recommended` | During migration, expose the existing accumulated evaluation view under a clearly non-shortlist label: Recommended / Current analyses. Do not label it My opportunities or Shortlisted. |
| `/jobs` in-page Discovery runs tab | `/jobs/history` | Canonical URL selects run/evaluation history; retain run IDs and job IDs in detail deep links. |
| `/jobs` in-page Recent vacancies tab | `/jobs/inbox` | Canonical URL selects Inbox; selected IDs should survive navigation only if explicitly encoded or retained as transient draft, not silently persisted. |
| New job detail | `/jobs/:discoveredJobId` | Stable canonical vacancy ID; default Overview. Unknown/not-owned child data should produce safe not-found/empty section, not cross-user leakage. |
| New Workspace section | `/jobs/:id/fit`, `/application`, `/tracking` | Stable job ID plus selected section. Detail/record IDs remain explicit; ensure literal `/jobs/history` is matched before `:id`. |
| `/applications` | `/applications` | Keep as preparation-centric list and add tracking projection/link per preparation; no route semantic change. |
| `/applications/:preparationId` | Same | Preserve preparation ID deep links; show frozen snapshot; if tracking exists link to its ID. |
| `/tracking` | `/tracking` | Keep lifecycle inbox. |
| `/tracking/:trackingId` | Same | Preserve tracking ID deep links and append-only history. |
| `/settings` | `/settings/ai` (existing) | Keep replace alias; target Settings landing can contain AI Models as default subsection. |
| `/settings/ai` | Same | Keep direct settings deep link. |

Component moves/renames should be composition first: `JobsPage` becomes Search landing/Inbox/Opportunities/history views; `JobsSearchesPage` becomes saved-search child view; current opportunity detail and historical run detail render inside Workspace as reusable components; `OpportunityPreparation` becomes a Workspace Application action but can remain available from opportunities during rollout; `ApplicationDetailPage` and `TrackingDetailPage` remain durable record views linked from workspace. CV, Adviser, readiness, runtime-attribution and Profile revision components are relocated by route/shell, not rewritten as part of IA work.

Frontend API types should initially be thin adapters over current `frontend/src/api.ts` response contracts. No API response field should be renamed as a UX cleanup. If a composed read model is later added, preserve old typed endpoints during rollout and keep compatibility mapping in one adapter module; never spread ad hoc old/new shape checks across page components.

## 12. Phase 2–9 migration and rollback plan

Each phase leaves old routes/API contracts usable. Feature flags or route-level incremental deployment should permit rollback to prior components; do not delete old surfaces until deep-link and history parity is demonstrated. No phase in this document authorizes implementation.

| Phase | Scope / dependency | Acceptance criteria for later implementation | Rollback boundary |
|---|---|---|---|
| 2. Route/shell foundation | Depends on this contract; add Home, Profile canonical route/aliases and primary nav, preserving readiness and all old deep links. | Home has bounded projection purpose; Profile still loads canonical snapshot/revision flow; `/`, `/cv`, `/adviser`, `/jobs`, `/jobs/searches`, application/tracking IDs and Settings links land meaningfully; no code/data deletion. | Restore old navigation/router while keeping additive routes; aliases can remain. |
| 3. Profile nesting | Place CV and Adviser beneath Profile secondary nav; migrate onboarding/CTA links; retain old direct aliases. | CV upload/review/history and Adviser intake/assessment/clarification/proposal workflows remain complete and readiness-gated; canonical candidate authority unchanged. | Revert nav shell; old routes continue. |
| 4. Search-intent contract and Find Jobs | Implement single typed frontend SearchIntent adapter and browser-available acquisition chooser/readiness. Codex instructions/import remain host-side. | Same criteria reused in saved schedule and run submission; channel is explicit; unavailable host/provider cannot appear as successful run; no credential leakage or paid-search substitution. | Revert Find Jobs view; saved schedule and execution APIs/routes still work. |
| 5. Job Search IA | Promote `/jobs/find`, Inbox, My opportunities and Search history to stable routes; canonical saved searches live at `/jobs/find/saved`; move schedule config under Find jobs and preserve technical history. | Deep links select the correct view; `/jobs` redirects to `/jobs/find`; `/jobs/searches` aliases `/jobs/find/saved`; existing inbox/opportunity/run/history data stays readable and correctly labelled. The existing `/jobs/opportunities` API remains available as Recommended / Current analyses, never as a saved shortlist. Until Phase 7 authority exists, show Recommended / Current analyses under its own label and omit/disable persistent Shortlisted actions or explain that saving is not yet available. Run data is never rewritten. | Route aliases back to old tab page and `/jobs/searches`; keep the recommendation projection readable. |
| 6. Job Workspace + latest result | Compose stable canonical-job Workspace and exact just-completed run result with current opportunity projection. | Workspace identity is DiscoveredJob ID; current vs historical evaluations are correct under existing fingerprints; history and inactive job reads remain accessible; attribution preserved. | Keep old Jobs tabs as fallback; disable workspace route only, no data migration. |
| 7. Decision state (separate implementation approval required) | Implement the already-required explicit shortlist semantics through one user-scoped canonical job-decision authority; determine schema/API and migration details under separate approval. | This is the point My opportunities → Shortlisted becomes a persisted, authoritative user-intent view. One decision per user/job; explicit allowed states, undo/revision and concurrency semantics; no inference from ranking/preparation/tracking; data isolation and audit tests. Recommended / Current analyses remains backed by `/jobs/opportunities` and separate. | Feature flag hides decision UI; additive decision records remain retained/readable; no rollback that deletes user choices. |
| 8. Applications/Tracking within Workspace | Reuse prep/tracking records and expose grouped projections/links without changing authority. | Applications owns snapshots; Tracking owns lifecycle; per-preparation cardinality respected; appended event history and downloads remain available; no employer action. | Keep existing Applications and Tracking pages as canonical record views; workspace sections link out. |
| 9. Consolidation/accessibility/history parity | Only after all preceding routes and read APIs pass parity; remove duplicate primary links, not historical routes/components until separately approved. | keyboard/accessibility, mobile layout, loading/error/readiness states, direct links, user scoping, historical provenance and rollback verified; no provider calls from read-only paths. | Restore duplicate navigation/views behind compatibility routes; preserve records and APIs. |

No DB migration is required by Phases 2–6 or 8–9 as currently specified. Phase 7's implementation shape and any unprojectable schedule-to-job historical association are explicit design/approval gates; explicit shortlist semantics themselves are settled by this contract.

## 13. Compatibility and history guarantees

1. Existing successful evaluations, discovery runs, run-job outcomes and schedule execution snapshots remain readable and unmodified.
2. Existing ApplicationPreparation target/identity/result documents and downloads remain immutable and directly addressable by preparation ID.
3. Tracking remains user-recorded, preparation-linked, append-only history. No UI move can infer employer status or submit an application.
4. Runtime/provenance remains visible from the new Workspace and Search history, including after routes move.
5. Existing links to `/`, `/cv`, `/adviser`, `/jobs`, `/jobs/searches`, `/applications/:preparationId`, `/tracking/:trackingId`, `/settings`, and `/settings/ai` resolve to a meaningful target during staged rollout. `/jobs` redirects to `/jobs/find`; `/jobs/searches` aliases canonical `/jobs/find/saved`; the `/jobs/opportunities` recommendation API remains readable throughout migration.
6. No old route or component is deleted in Issue #222. Old APIs stay in place; adapters are additive and typed.
7. Canonical candidate state remains the sole candidate authority. Profile direct-write 409 tombstones remain; readiness stays fail-closed at semantic/preparation boundaries.
8. Read-only/provider-free surfaces remain provider-free. External Codex discovery stays host-side and credential-separated.
9. Shared DiscoveredJob facts/provenance are not used to store one user's private shortlist/search preferences; all user-specific data stays scoped by authenticated owner.

## 14. Explicit invariants

- Candidate canonical state remains the sole intended candidate authority; no legacy direct Profile mutation path is reintroduced.
- Candidate readiness remains fail-closed at semantic/evaluation/preparation boundaries already requiring it.
- Discovery runs, evaluations, schedules, preparations and tracking are read/written in user scope. Shared public vacancy records remain distinct.
- Discovery executions and run inputs are historical snapshots, never silently rewritten after configuration edits.
- Existing successful evaluation outputs are not retroactively mutated by a UI migration.
- Application preparation snapshots remain immutable historical evidence.
- Tracking history remains append-only and user-recorded.
- Discovery provenance and semantic/runtime attribution remain inspectable.
- Provider-free read paths remain provider-free where they are today.
- External Codex discovery remains host-side and credential-separated unless a later separately authorized issue changes that architecture.
- Search/prioritisation intent never becomes candidate eligibility or evaluation evidence.
- Ranked recommendation/current analysis is not user intent. My opportunities → Shortlisted represents explicit user intent and becomes persisted/authoritative only after Phase 7's separately approved decision-state extension; until then no recommendation projection is presented as a saved shortlist.

## 15. Open questions and explicit deferrals

1. **Decision-state implementation details:** explicit shortlist semantics are required by the target contract. Before Phase 7, decide the exact model/table/API shape, whether Dismissed shares the decision authority, allowed states, undo/revision and concurrency/audit semantics, and migration behavior. Keep the target distinction between Shortlisted and Recommended / Current analyses fixed.
2. **Find Jobs channel availability:** which browser-callable acquisition channel is supported in each deployment, and what server readiness fields can be trusted? This contract avoids promising a channel that is not configured.
3. **Codex return/import UX:** exact supported local invocation and user-confirmed import handoff should be documented by the host integration owner; this design does not build desktop IPC or credential transfer.
4. **Search context for manually selected old inbox jobs:** decide whether General context can be deterministically defaulted from job title/location or whether user must select a saved search/type criteria. Current run API requires non-empty keywords, so this cannot be hidden.
5. **Schedule/run participation:** confirm whether an execution's `discovery_run_id` plus `DiscoveryRunJob` is sufficient to reconstruct schedule-to-job participation in every outcome; add only a minimal read-side seam if not.
6. **One preparation to one tracking record:** current uniqueness is explicit. Decide separately if product needs multiple employer submissions/tracking records from one preparation or job-level tracking independent of a preparation.
7. **Home KPI definitions:** counts and “latest” cards must use clear bounded APIs and match current applicability; do not add data writes or costly unbounded fan-out to create a dashboard.
8. **`remote_ok` semantics:** the current UI/schema includes historical values with legacy-preservation wording. Before relabeling, define whether true means no restriction and null means unspecified, and preserve old stored queries.
9. **Workspace aggregation/pagination:** measure current API fan-out and limits in implementation design; use a narrow read-only projection only where bounded composition is insufficient.

Explicitly deferred: fit/recommendation tuning, generated CV/cover-letter quality or regeneration, Codex model selection, unrelated smoke defects, #194, desktop/host invocation, paid web-search substitution, route/component deletion, schema/API migration beyond separately approved seams, and all production UX implementation in this issue.

## 16. Validation and Phase 1 completion criteria

Phase 1 validation is source inspection and documentation consistency only. The document is checked against `frontend/src/App.tsx`, `JobsPage.tsx`, `JobsSearchesPage.tsx`, `ApplicationsPage.tsx`, `TrackingPage.tsx`, `CvPage.tsx`, `AdviserPage.tsx`, `AiSettingsPage.tsx`; route handlers/schemas/models/services under `backend/app`; and existing current user/job/tracking ownership contracts. No live provider, external search, Codex, or application workflow is invoked.

Phase 1 is complete when the acceptance checklist in Issue #222 is satisfied: current route/component/API inventory is present; every mapped API is classified; route, navigation, authority and historical contracts are explicit; SearchIntent ownership/inheritance is defined; Phases 2–9 and rollback boundaries are sequenced; and only documentation has changed. No Phase 2 work starts without separate authorization.

# Unified Job Journey Phase 9C Evidence

## Scope and baseline

- Issue: #248 — Phase 9C Accessibility & responsive hardening
- Authorized baseline: `5fa87fa0f653ffd520d169fc2ceab4f2535f1e38`
- Branch: `codex/issue-248-phase-9c-accessibility-responsive`
- Review method: local rendered UI inspection at approximately 390px × 844px and 1280px × 720px, DOM/semantic inspection, and keyboard focus smoke.
- No live provider, web-search, or Codex calls were used.
- The local render used a copied local SQLite fixture and a synthetic account. The copied fixture was repaired only in the temporary render database to add schema columns expected by the already-reviewed backend; no backend source, schema, migration, or durable user data was changed.

The baseline render was captured before the CSS change. The baseline showed no clipping in the inspected pages, but `.jobs-tabs` styled `button` and `aria-pressed` while the production route shells render anchor/NavLink elements with `aria-current="page"`. As a result, Job Search, nested opportunities, and Workspace tabs rendered as plain links without the intended active-state and mobile tab sizing treatment. Repeated job/application/tracking actions also exposed generic accessible names without the surrounding job or application context.

Status vocabulary used below:

- `PASS`: inspected state met the check.
- `ISSUE_FOUND`: a scoped issue was observed before the repair.
- `FIXED`: the observed issue was verified after the repair.
- `NOT_REACHABLE`: the state required persisted data that was not present in the local synthetic fixture.
- `NOT_PERFORMED`: a before-change capture was not made; no conclusion is drawn from that row.

## Rendered state matrix

### Baseline, before the Phase 9C changes

| Surface/state | 390px | 1280px | Evidence |
| --- | --- | --- | --- |
| Primary navigation and Home | PASS | PASS | Navigation wrapped naturally on mobile; no clipping observed. |
| Profile Overview and Profile secondary navigation | PASS | PASS | Current primary and secondary links were visible and identifiable. |
| Profile CV | PASS | NOT_PERFORMED | Mobile form and source history fit the viewport. |
| Profile Career Adviser | PASS | NOT_PERFORMED | Mobile remediation state fit the viewport. |
| Job Search Find/SearchIntent | PASS | NOT_PERFORMED | SearchIntent controls and labels were reachable; no overflow observed. |
| Job Search Inbox | ISSUE_FOUND | PASS | Mobile links wrapped but tab styling did not apply; repeated job actions had generic names. |
| My opportunities / Recommended | ISSUE_FOUND | NOT_PERFORMED | Nested active link had no selected-state styling; repeated job actions had generic names. |
| My opportunities / Shortlisted | ISSUE_FOUND | NOT_PERFORMED | Nested active link had no selected-state styling. |
| Search history | PASS | NOT_PERFORMED | Empty/loading state fit the mobile viewport. |
| Saved discovery history | PASS | NOT_PERFORMED | Saved-discovery empty state fit the mobile viewport. |
| Workspace Overview | ISSUE_FOUND | NOT_PERFORMED | Workspace tab anchors lacked the intended active-state styling. |
| Workspace Fit | NOT_REACHABLE | NOT_REACHABLE | No persisted current evaluation was available in the local synthetic fixture. |
| Workspace Application | NOT_REACHABLE | NOT_REACHABLE | The initial copied fixture was stale for the current workspace projection; the render copy was repaired for post-fix verification. |
| Workspace Tracking | NOT_REACHABLE | NOT_REACHABLE | No persisted preparation/tracking rows were available in the initial fixture state. |
| Applications list | NOT_PERFORMED | NOT_PERFORMED | No before-change capture was made for this unaffected layout surface. |
| Application detail and downloads | NOT_REACHABLE | NOT_REACHABLE | No persisted preparation detail existed for the synthetic account. |
| Tracking list | NOT_PERFORMED | NOT_PERFORMED | No before-change capture was made for this unaffected layout surface. |
| Tracking detail/history | NOT_REACHABLE | NOT_REACHABLE | No persisted tracking detail existed for the synthetic account. |
| Settings / AI Models | NOT_PERFORMED | NOT_PERFORMED | No before-change capture was made for this unaffected layout surface. |

### Post-fix verification

| Surface/state | 390px | 1280px | Result |
| --- | --- | --- | --- |
| Job Search Find/SearchIntent | PASS | PASS | Controls remain reachable and fit the viewport. |
| Job Search Inbox and tabs | FIXED | PASS | Anchor tabs receive the intended selected styling and mobile flex sizing; repeated actions expose job context. |
| My opportunities / Recommended | FIXED | PASS | Both the primary and nested selected links are visibly styled. |
| My opportunities / Shortlisted | FIXED | PASS | Nested selected link is visibly styled. |
| Workspace Overview/Application tabs | FIXED | PASS | Selected workspace tab is visibly styled; mobile tabs wrap without clipping. |
| Applications list | PASS | PASS | Empty history state fits at both sizes. |
| Tracking list | PASS | PASS | Empty tracking state fits at both sizes. |
| Settings / AI Models | PASS | PASS | Long settings content scrolls vertically; no horizontal overflow observed. |
| Workspace Application empty history/readiness | PASS | PASS | Workspace remains reachable; preparation history and readiness messaging fit. |

The post-fix DOM checks reported `scrollWidth <= innerWidth` for the inspected post-fix pages. A vertical scrollbar on long pages was expected and was not treated as horizontal overflow.

## Accessibility and semantic checks

### Repeated actions

The visible action wording and navigation mechanics were preserved. The accessible names now include existing human-readable context:

- `JobsPage.tsx`: Open workspace, View Fit, Open vacancy, Open exact run result, View detail, and Historical detail include job title/company/location context.
- `JobWorkspacePage.tsx`: Open preparation, Open tracking history, and Start tracking include job title/company/location and creation time; tracked actions also include status and update time.
- `ApplicationsPage.tsx`: Review preparation and Open tracking include title/company/location and preparation creation time; tracking links also include status/update time.
- `TrackingPage.tsx`: Open tracking history and Open preparation include title/company/location and status/update time.

The repeated-title regression assertions use two same-shaped application/tracking records and verify that their `aria-label` values remain distinct. Visible labels remain `Open workspace`, `Open preparation`, `Open tracking history`, `Start tracking`, `Review preparation`, and related existing copy.

### Keyboard and focus smoke

`PASS` for the reachable Workspace Application state. Starting at the page, Tab traversal reached:

1. primary navigation links;
2. saved-discovery link;
3. Job Search section links;
4. decision controls;
5. Overview, Fit, Application, and Tracking workspace tabs;
6. Refresh applications.

The active element retained the existing visible focus treatment (`outline: rgb(147, 180, 255) solid 2.4px` in the rendered browser). No positive `tabIndex` or pointer-only replacement was introduced. Application-detail and Tracking-detail controls could not be traversed because those data-backed detail states were not reachable for the synthetic account.

### Downloads and status semantics

- `PASS`: existing download labels are already distinct (`Download CV DOCX`, `Download CV PDF`, and the distinct cover-letter variants when present); no download markup or behavior was changed.
- `PASS`: existing `role="status"` and `role="alert"` semantics remain in place for loading, success, retry, and error messages. No blanket `aria-live` region was added.

## Scoped production changes

| File | Change mapped to evidence |
| --- | --- |
| `frontend/src/App.css` | Updated the existing `.jobs-tabs` selectors from production-mismatched buttons to the rendered anchor/NavLink elements, using `aria-current="page"` for selected styling and the existing mobile flex rule. |
| `frontend/src/JobsPage.tsx` | Added contextual accessible names to repeated job/result actions without changing visible text, routes, or handlers. |
| `frontend/src/JobWorkspacePage.tsx` | Added contextual accessible names to repeated preparation/tracking actions without changing visible text, routes, or handlers. |
| `frontend/src/ApplicationsPage.tsx` | Added contextual accessible names to repeated preparation/tracking actions without changing visible text, routes, or handlers. |
| `frontend/src/TrackingPage.tsx` | Added contextual accessible names to repeated tracking/preparation actions without changing visible text, routes, or handlers. |
| `frontend/src/JobsPage.test.tsx` | Updated affected accessible-name queries and added coverage for contextual result actions. Existing route/workspace/readiness tests remain in place. |
| `frontend/src/ApplicationsPage.test.tsx` | Added assertions that repeated Review preparation accessible names are distinct. |
| `frontend/src/TrackingPage.test.tsx` | Added assertions that repeated Open tracking history accessible names are distinct. |

No route, readiness authority, candidate authority, application-preparation behavior, tracking behavior, backend/API/schema, migration, lifecycle, or provider behavior was changed. No Phase 9D work was introduced.

## Outstanding evidence limits

Application-detail and Tracking-detail states remain `NOT_REACHABLE` in the local synthetic render because no preparation or tracking record was available for that account. The pre-change capture was also not performed for Applications, Tracking, and Settings because those surfaces are not affected by the tab-selector issue. This document records those limits explicitly; it does not treat them as merge-ready evidence.

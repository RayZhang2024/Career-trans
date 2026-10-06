import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation, useNavigate, useNavigationType } from "react-router-dom";
import type { ApplicationPreparation, CreateDiscoveryRun, DiscoveryRunDetail, DiscoveryRunSummary, DiscoveryScheduleRead, HistoricalRunJobDetail, InboxSummary, OnboardingStatus, OneOffExecution, OneOffPreflight, RankedJobOpportunity, ScheduledExecutionRead, User, UserOpportunitySummary } from "./api";
import { App } from "./App";
import { AuthProvider, useAuth } from "./auth";
import { undecidedDecision, useJobDecisionMutator } from "./jobDecisions";

const TOKEN = "career-trans.access-token";
const user: User = { id: "user-1", email: "jobs@example.test", created_at: "2026-01-01T00:00:00Z" };
const ready: OnboardingStatus = { profile_exists: true, candidate_context_ready: true, latest_cv_draft: { id: "cv-1", state: "confirmed", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 0, journey: { candidate_context_ready: true, job_search_ready: true, intake_exists: false, assessment_status: null, confirmed_guidance_active: false, clarification_session_active: false, current_follow_up_available: false, clarification_interpretation_awaiting_confirmation: false, unresolved_profile_enrichment_count: 0, next_enrichment_clarification_id: null, next_enrichment: null, active_profile_draft: false, next_action: "start_intake", status_category: "setup", confirmed_clarification_count: 0 } } };
const decision = (id: string, value: "undecided" | "shortlisted" | "dismissed" = "undecided"): { discovered_job_id: string; decision: "undecided" | "shortlisted" | "dismissed"; revision: number | null; created_at: string | null; updated_at: string | null } => ({ discovered_job_id: id, decision: value, revision: null, created_at: null, updated_at: null });
const op = (id: string, title = id, rank = 999): UserOpportunitySummary => ({ evaluation_id: `eval-${id}`, discovered_job_id: `job-${id}`, recommendation: "consider", title, company: "Example Co", location: "London", work_arrangement: "Hybrid", fit_score: 72, career_alignment_score: 84, career_alignment_confidence: "medium", relevance_score: 0.91, archetype: "ai_forward_deployed", url: `https://jobs.example.test/${id}`, posting_recency: { legitimacy: "unknown", reasoning: "The listing date is not independently verified." }, decision: decision(`job-${id}`), ...( { rank } as object) });
const ranked = (title: string): RankedJobOpportunity => ({
  job: { title, company: "Example Co", location: "London", work_arrangement: "Hybrid", employment_type: "Full-time", url: "https://jobs.example.test/detail", posted_at: null },
  relevance: { relevant: true, score: 0.91, reasoning: "Backend relevance reasoning." }, archetype: { archetype: "ai_forward_deployed", reasoning: "Backend archetype reasoning." },
  fit_assessment: { fit_score: 72, strengths: [0, 99], hard_blockers: [], gaps: [{ requirement_index: 9, requirement: { text: "Canonical requirement", importance: "essential", category: "technical" }, gap_type: "evidence_gap", severity: "moderate", reason: "Backend gap reasoning." }] },
  career_assessment: { career_alignment_score: 84, confidence: "medium", dimensions: [{ dimension: "direction", score: 0.8, reasoning: "Dimension reason." }], strategic_strengths: ["Strategic strength"], strategic_tradeoffs: ["Strategic trade-off"], reasoning: "Career reasoning." },
  recommendation_assessment: { recommendation: "consider", fit_score: 72, career_alignment_score: 84, career_alignment_confidence: "medium", rule_id: "rule", reasoning: "Recommendation reasoning.", key_strengths: ["Key strength"], key_tradeoffs: ["Key trade-off"], hard_blockers: [] },
  legitimacy: { legitimacy: "unknown", reasoning: "Historical timestamp signal." }, rank: 1,
  job_profile: { title, company: "Example Co", location: "London", work_arrangement: "Hybrid", seniority: "Senior", salary: null, employment_type: "Full-time", application_deadline: null, responsibilities: ["Build systems"], requirements: [{ text: "Canonical requirement", importance: "essential", category: "technical" }], technical_skills: ["Python"], domain_knowledge: ["AI"], security_requirements: [], work_authorization_requirements: [] },
  requirement_matches: [{ requirement_index: 0, requirement: { text: "Canonical requirement", importance: "essential", category: "technical" }, match_type: "demonstrated", score: 0.8, evidence_ids: ["private-evidence-id-must-not-render"], reasoning: "Match reasoning." }, { requirement_index: 7, requirement: { text: "invented text", importance: "essential", category: "technical" }, match_type: "missing", score: 0, evidence_ids: [], reasoning: "Bad index." }],
});
const run = (id = "run-1", status: DiscoveryRunSummary["status"] = "completed"): DiscoveryRunSummary => ({ id, status, run_input: { query: { keywords: ["AI Engineer"], locations: ["London"], remote_ok: false } }, funnel: { submitted: 3, geography_eligible: 2, geography_incompatible: 1, geography_unknown: 1, remote_policy_filtered: 0, reused: 1, relevance_screened: 2, full_analysis_attempts: 1, analysed: 1 }, failure_summary: { provider_failure: 1 }, started_at: "2026-02-01T12:00:00Z", completed_at: status === "running" ? null : "2026-02-01T12:05:00Z" });
const savedSchedule = (patch: Partial<DiscoveryScheduleRead> = {}): DiscoveryScheduleRead => ({
  id: "s-1", name: "AI roles", enabled: false,
  schedule: { cadence: "daily", timezone: "UTC", local_time: "09:00:00", weekdays: [] },
  query: { keywords: ["AI"], locations: ["London"], remote_ok: false, companies: ["Compatibility Co"], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 73 },
  acquisition: { structured_ats: { enabled: true, companies: [], providers: [], all_resolved_sources: true, max_sources: 20, max_results: 100 }, agentic_web: { enabled: false, country: "gb", max_search_queries: 6, max_search_results_per_query: 10, max_pages_to_open: 20, max_discovered_jobs: 20 } },
  evaluation: { max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 }, next_run_at: null, last_execution_at: null, ...patch,
});
const savedExecution = (status: ScheduledExecutionRead["status"] = "completed"): ScheduledExecutionRead => ({
  id: "execution-1", trigger_kind: "manual", scheduled_for: null, status,
  config_snapshot: { schedule: savedSchedule().schedule, query: savedSchedule().query, acquisition: savedSchedule().acquisition, evaluation: savedSchedule().evaluation },
  discovery_run_id: status === "running" ? null : "run-1", acquisition_summary: {}, failure_summary: {}, started_at: "2026-02-01T12:00:00Z", completed_at: status === "running" ? null : "2026-02-01T12:05:00Z",
});
const oneOffPreflight = (patch: Partial<OneOffPreflight> = {}): OneOffPreflight => ({ effective_provider: "tavily", readiness: "configured_for_launch", available: true, reason: null, policy: { version: "v2", country: "gb", max_search_queries: 6, max_search_results_per_query: 10, max_pages_to_open: 20, max_discovered_jobs: 20, max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 }, provider_settings_revision: 2, launch_fingerprint: "a".repeat(64), ...patch });
const oneOffExecution = (patch: Partial<OneOffExecution> = {}): OneOffExecution => ({ id: "one-off-1", client_request_id: "request-1", query: { keywords: ["AI"], locations: [], remote_ok: null, companies: [], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 50 }, policy: oneOffPreflight().policy, provider: { provider: "tavily", credential_source: "user", search_depth: "basic" }, status: "completed", started_at: "2026-10-01T00:00:00Z", completed_at: "2026-10-01T00:01:00Z", acquisition_summary: { canonical_jobs: 0, relevance_screened: 0, analysed: 0 }, failure_summary: {}, discovery_run_id: null, ...patch });
const runDetail = (status: DiscoveryRunSummary["status"] = "completed"): DiscoveryRunDetail => ({ ...run("run-1", status), jobs: (["newly_evaluated", "reused_evaluation", "not_actionable", "presemantic_filtered", "geography_incompatible", "geography_unknown", "outside_semantic_budget", "semantic_rejected", "outside_deep_analysis_budget", "analysis_failed"] as const).map((outcome, i) => ({ discovered_job_id: `job-${i}`, evaluation_id: null, outcome, failure_stage: outcome === "analysis_failed" ? "career_analysis" : null, failure_kind: null, opportunity: null })) });
const inboxItem = (id: string, actionable = true): InboxSummary => ({ discovered_job_id: id, title: `Inbox ${id}`, company: "Public Co", location: "London", work_arrangement: "Hybrid", employment_type: "Full-time", url: `https://public.example.test/${id}`, state: "new", verification_status: actionable ? "verified" : "unverified", verification_reason: actionable ? null : "provider_detail_unavailable", actionable, first_seen_at: "2026-02-01T00:00:00Z", last_seen_at: "2026-02-02T00:00:00Z", provenance: [{ runtime: "codex", source_ref: "not-rendered", discovered_via: "external_import", imported_at: "2026-02-02T00:00:00Z" }], provenance_count: 1, decision: decision(id) });
const workspaceJob = (patch: Record<string, unknown> = {}) => ({ id: "actionable", title: "Workspace role", company: "Public Co", location: "London", url: "https://public.example.test/actionable", description: "Public description", posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", detail_authority: "provider_detail", verification_status: "verified", verification_reason: null, state: "new", actionable: true, first_seen_at: "2026-02-01T00:00:00Z", last_seen_at: "2026-02-02T00:00:00Z", last_changed_at: "2026-02-01T00:00:00Z", ...patch });
const workspacePayload = (workspaceDecision = decision("actionable"), patch: Record<string, unknown> = {}) => ({ job: workspaceJob(), decision: workspaceDecision, provenance: { items: [], count: 0, limit: 20, truncated: false }, current_fit: { status: "none", reason: "no_current_evaluation", evaluation: null }, evaluations: { items: [], limit: 20, truncated: false }, applications: { items: [], limit: 20, truncated: false }, ...patch });
const workspacePreparation = (id = "prep-1", title = "Saved workspace preparation") => ({ preparation_id: id, created_at: "2026-03-01T12:00:00Z", target: { source_kind: "discovered_job", canonical_discovered_job_id: "actionable", title, company: "Public Co", location: "London", public_url: null, work_arrangement: null, employment_type: null, job_content_hash: "hash" }, snapshot_status: "current_job_content", result_summary: null, tracking: null });
const listedDecision = (id: string, value: "shortlisted" | "dismissed", revision: number, title = `${value} ${id}`) => ({ discovered_job_id: id, title, company: "Public Co", location: "London", url: `https://public.example.test/${id}`, posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", state: "new" as const, verification_status: "verified" as const, verification_reason: null, actionable: true, last_seen_at: "2026-02-02T00:00:00Z", decision: value, revision, created_at: "2026-02-01T00:00:00Z", updated_at: "2026-02-02T00:00:00Z" });
const historyDetail: HistoricalRunJobDetail = { discovered_job_id: "job-0", evaluation_id: "historical-eval", outcome: "newly_evaluated", failure_stage: null, failure_kind: null, opportunity: ranked("Historical role"), runtime_attribution: { status: "available", provider: "openai", operations: { job_relevance: { model: "old-job-relevance", reasoning_effort: null }, job_archetype: { model: "old-archetype", reasoning_effort: "low" }, job_extraction: { model: "old-extraction", reasoning_effort: "medium" }, requirement_matching: { model: "old-matching", reasoning_effort: "high" }, career_alignment: { model: "old-alignment", reasoning_effort: "max" } } } };
const json = (body: unknown, status = 200) => new Response(body === undefined ? "" : JSON.stringify(body), { status });
type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>;
const page = (items: unknown[], truncated = false) => ({ items, limit: 20, truncated });
const profileSnapshot = () => ({ profile: null, structured_profile: null, active_evidence: [], adviser_intake: null, eligibility: { work_authorisation: [], security_clearances: [], locations: [] }, adviser_assessment: null, adviser_assessment_status: "not_available", readiness: { structured_profile_available: false, ready_for_candidate_context: false, evidence_materialization_status: "not_applicable", expected_evidence_count: 0, materialized_evidence_count: 0, missing_evidence_count: 0, stale_evidence_count: 0, latest_cv_draft_state: null } });
function fakeFetch(overrides: Record<string, Handler> = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const path = url.pathname;
    const override = overrides[`${init?.method ?? "GET"} ${path}`] ?? overrides[path];
    if (override) return Promise.resolve(override(url, init));
    if (path === "/api/v1/jobs/search-history") {
      const legacyFixture = overrides["/api/v1/jobs/discovery-runs"];
      if (legacyFixture) return Promise.resolve(legacyFixture(url, init)).then(async (response) => {
        if (!response.ok) return response;
        const payload = await response.clone().json() as { items?: DiscoveryRunSummary[]; limit?: number; truncated?: boolean };
        return json({ ...payload, items: (payload.items ?? []).map((item) => ({ type: "discovery_run", id: item.id, started_at: item.started_at, run: item })) });
      });
      return Promise.resolve(json(page([{ type: "discovery_run", id: run().id, started_at: run().started_at, run: run() }])));
    }
    if (path === "/api/v1/users/me") return Promise.resolve(json(user));
    if (path === "/api/v1/onboarding/status") return Promise.resolve(json(ready));
    if (path === "/api/v1/profile") return Promise.resolve(json({ id: "profile", user_id: user.id, display_name: "Current Person", created_at: "", updated_at: "" }));
    if (path === "/api/v1/profile/snapshot") return Promise.resolve(json(profileSnapshot()));
    if (path === "/api/v1/profile/revisions/active") return Promise.resolve(json(null));
    if (path === "/api/v1/jobs/opportunities") return Promise.resolve(json(page([op("alpha", "Alpha", 99), op("beta", "Beta", 1)])));
    if (path === "/api/v1/jobs/discovery-runs") return Promise.resolve(json(page([run()])));
    if (path === "/api/v1/jobs/inbox") return Promise.resolve(json(page([inboxItem("actionable"), inboxItem("blocked", false)])));
    if (path === "/api/v1/jobs/decisions") return Promise.resolve(json(page([])));
    if (path === "/api/v1/jobs/discovery-schedules") return Promise.resolve(json([]));
    if (path === "/api/v1/jobs/one-off-discovery/preflight") return Promise.resolve(json(oneOffPreflight()));
    if (path === "/api/v1/jobs/one-off-discovery/executions") return Promise.resolve(json(init?.method === "POST" ? oneOffExecution() : []));
    if (path === "/api/v1/jobs/opportunities/eval-alpha") return Promise.resolve(json(ranked("Alpha detail")));
    if (path === "/api/v1/jobs/discovery-runs/run-1") return Promise.resolve(json(runDetail()));
    if (path === "/api/v1/jobs/discovery-runs/run-1/jobs/job-0") return Promise.resolve(json(historyDetail));
    if (path === "/api/v1/jobs/discovery-runs" && init?.method === "POST") return Promise.resolve(json({ ...run("run-new"), jobs: [] }));
    throw new Error(`Unexpected request ${init?.method ?? "GET"} ${path}${url.search}`);
  });
}
function renderJobs(fetch = fakeFetch(), path = "/jobs/find", showRouteLocation = false, strict = false) {
  sessionStorage.setItem(TOKEN, "test-token");
  vi.stubGlobal("fetch", fetch);
  const app = <MemoryRouter initialEntries={[path]}><AuthProvider><App />{showRouteLocation && <RouteLocation />}</AuthProvider></MemoryRouter>;
  return { ...render(strict ? <StrictMode>{app}</StrictMode> : app), fetch };
}
function RouteLocation() { const location = useLocation(); const action = useNavigationType(); return <output aria-label="Route location">{location.pathname}{location.search}:{action}</output>; }
function HistoryControls() { const navigate = useNavigate(); return <div><button type="button" onClick={() => navigate(-1)}>Back history</button><button type="button" onClick={() => navigate(1)}>Forward history</button></div>; }
function DecisionProbe() {
  const { api, user } = useAuth();
  const mutator = useJobDecisionMutator();
  const mutate = (jobId: string, target: "shortlisted" | "dismissed") => void mutator.mutate(undecidedDecision(jobId), target);
  return <div><output>{user?.id ?? "loading"}</output><button type="button" onClick={() => { mutate("job-a", "shortlisted"); mutate("job-a", "dismissed"); }}>Mutate A twice</button><button type="button" onClick={() => mutate("job-b", "shortlisted")}>Mutate B</button><button type="button" onClick={() => api.replaceToken("replacement-token")}>Replace session</button></div>;
}
function AuthSwitcher() { const { api, retryRestore } = useAuth(); return <button type="button" onClick={() => { api.replaceToken("user-b-token"); retryRestore(); }}>Switch user</button>; }
function renderJobsWithAuthSwitcher(fetch: ReturnType<typeof fakeFetch>) {
  sessionStorage.setItem(TOKEN, "test-token");
  vi.stubGlobal("fetch", fetch);
  return render(<MemoryRouter initialEntries={["/jobs/find"]}><AuthProvider><App /><AuthSwitcher /></AuthProvider></MemoryRouter>);
}
function requestPaths(fetch: ReturnType<typeof fakeFetch>) { return fetch.mock.calls.map(([input]) => { const url = new URL(String(input), window.location.origin); return `${url.pathname}${url.search}`; }); }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function loaded() { await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); await screen.findByText("Alpha"); }
async function selectSavedSchedule() { await screen.findByRole("heading", { name: "Find jobs" }); await screen.findByRole("option", { name: "AI roles" }); fireEvent.change(screen.getByLabelText("Saved search configuration"), { target: { value: "s-1" } }); await waitFor(() => expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled()); }
async function editIntentOnFind(themes: string, locations?: string, remote?: string) { fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: themes } }); if (locations !== undefined) fireEvent.change(screen.getByLabelText("Locations (search criteria, not eligibility)"), { target: { value: locations } }); if (remote !== undefined) fireEvent.change(screen.getByRole("combobox", { name: "Remote policy" }), { target: { value: remote } }); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" }); }
async function expectStatusContaining(text: string) { await waitFor(() => expect(screen.getAllByRole("status").some((status) => status.textContent?.includes(text))).toBe(true)); }
async function growToLimit(fetch: ReturnType<typeof fakeFetch>, endpoint: string, buttonName: string) {
  for (const limit of [40, 60, 80, 100]) {
    fireEvent.click(screen.getByRole("button", { name: buttonName }));
    await waitFor(() => expect(requestPaths(fetch)).toContain(`${endpoint}?limit=${limit}`));
  }
}

beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });
afterEach(cleanup);

describe("Issue #171 Jobs workspace", () => {
  it("loads all bounded summaries on the protected direct /jobs route and preserves backend order", async () => {
    const { fetch } = renderJobs(); await loaded();
    expect(await screen.findByRole("link", { name: "Job Search" })).toHaveAttribute("href", "/jobs/find");
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities?limit=20");
    expect(requestPaths(fetch)).not.toContain("/api/v1/applications");
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/discovery-runs?limit=20");
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/inbox?limit=20");
    const titles = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);
    expect(titles.slice(0, 2)).toEqual(["Alpha", "Beta"]);
    expect(screen.getByText("1")).toBeInTheDocument(); expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.queryByText("99")).not.toBeInTheDocument();
  });

  it("disambiguates repeated current opportunity actions with the visible result ordinal", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([op("first", "Repeated role", 1), op("second", "Repeated role", 2)])) });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.click(screen.getByRole("link", { name: "My opportunities" }));
    await screen.findByRole("heading", { name: "Recommended / Current analyses" });
    const workspaceLinks = await screen.findAllByRole("link", { name: /Open workspace for Repeated role/ });
    expect(workspaceLinks).toHaveLength(2);
    expect(new Set(workspaceLinks.map((link) => link.getAttribute("aria-label"))).size).toBe(2);
    expect(workspaceLinks.map((link) => link.getAttribute("aria-label"))).toEqual(expect.arrayContaining([
      "Open workspace for Repeated role · Example Co · London · result 1",
      "Open workspace for Repeated role · Example Co · London · result 2",
    ]));
    expect(workspaceLinks.every((link) => !link.getAttribute("aria-label")?.includes("first") && !link.getAttribute("aria-label")?.includes("second"))).toBe(true);
  });

  it("keeps repeated opportunity preparation and decision actions distinguishable", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([op("first", "Repeated role", 1), op("second", "Repeated role", 2)])) });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.click(screen.getByRole("link", { name: "My opportunities" }));
    await screen.findByRole("heading", { name: "Recommended / Current analyses" });
    const prepareButtons = screen.getAllByRole("button", { name: /^Prepare application for Repeated role/ });
    expect(prepareButtons).toHaveLength(2);
    expect(new Set(prepareButtons.map((button) => button.getAttribute("aria-label"))).size).toBe(2);
    expect(prepareButtons.every((button) => button.textContent === "Prepare application")).toBe(true);
    const shortlistButtons = screen.getAllByRole("button", { name: /^Shortlist Repeated role/ });
    expect(shortlistButtons).toHaveLength(2);
    expect(new Set(shortlistButtons.map((button) => button.getAttribute("aria-label"))).size).toBe(2);
    expect(shortlistButtons.every((button) => button.textContent === "Shortlist")).toBe(true);
    fireEvent.click(prepareButtons[0]);
    const closeButton = await screen.findByRole("button", { name: /^Close preparation options for Repeated role/ });
    expect(closeButton).toHaveTextContent("Close preparation options");
    expect(closeButton).toHaveAttribute("aria-expanded", "true");
  });

  it("uses visible timestamps to distinguish repeated Inbox, Shortlisted, and Dismissed actions", async () => {
    const sameInbox = (id: string, lastSeen: string) => ({ ...inboxItem(id), title: "Repeated role", company: "Public Co", location: "London", last_seen_at: lastSeen });
    const sameDecision = (id: string, value: "shortlisted" | "dismissed", updatedAt: string) => ({ ...listedDecision(id, value, 1, "Repeated role"), company: "Public Co", location: "London", updated_at: updatedAt });
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => json(page([sameInbox("inbox-a", "2026-02-02T00:00:00Z"), sameInbox("inbox-b", "2026-02-03T00:00:00Z")])),
      "/api/v1/jobs/decisions": (url) => json(page(url.searchParams.get("decision") === "shortlisted"
        ? [sameDecision("short-a", "shortlisted", "2026-02-02T00:00:00Z"), sameDecision("short-b", "shortlisted", "2026-02-03T00:00:00Z")]
        : [sameDecision("dismiss-a", "dismissed", "2026-02-04T00:00:00Z"), sameDecision("dismiss-b", "dismissed", "2026-02-05T00:00:00Z")])),
    });

    renderJobs(fetch, "/jobs/inbox");
    await screen.findByRole("heading", { name: "Inbox" });
    const inboxCheckboxes = await screen.findAllByRole("checkbox", { name: /^Select Repeated role/ });
    expect(inboxCheckboxes).toHaveLength(2);
    const checkboxLabels = inboxCheckboxes.map((checkbox) => checkbox.getAttribute("aria-label") ?? "");
    expect(checkboxLabels.every((label) => label.includes("Select Repeated role"))).toBe(true);
    expect(new Set(checkboxLabels).size).toBe(2);
    expect(checkboxLabels.every((label) => label.includes("last seen"))).toBe(true);
    expect(checkboxLabels.every((label) => !/inbox-a|inbox-b|discovered_job_id/.test(label))).toBe(true);
    fireEvent.click(inboxCheckboxes[0]);
    expect(inboxCheckboxes[0]).toBeChecked();
    expect(inboxCheckboxes[1]).not.toBeChecked();
    fireEvent.click(inboxCheckboxes[0]);
    expect(inboxCheckboxes[0]).not.toBeChecked();
    for (const label of ["Open workspace", "View Fit", "Open vacancy"]) {
      const actions = await screen.findAllByRole("link", { name: new RegExp(`^${label} for Repeated role`) });
      expect(actions).toHaveLength(2);
      expect(new Set(actions.map((link) => link.getAttribute("aria-label"))).size).toBe(2);
      expect(actions.every((link) => link.getAttribute("aria-label")?.includes("last seen"))).toBe(true);
    }
    const inboxShortlist = screen.getAllByRole("button", { name: /^Shortlist Repeated role/ });
    expect(inboxShortlist).toHaveLength(2);
    expect(new Set(inboxShortlist.map((button) => button.getAttribute("aria-label"))).size).toBe(2);
    expect(inboxShortlist.every((button) => button.textContent === "Shortlist")).toBe(true);

    fireEvent.click(screen.getByRole("link", { name: "My opportunities" }));
    await screen.findByRole("heading", { name: "Recommended / Current analyses" });
    fireEvent.click(screen.getByRole("link", { name: "Shortlisted" }));
    await screen.findByRole("heading", { name: "Shortlisted" });
    const shortlistedCards = screen.getAllByRole("heading", { name: "Repeated role" }).map((heading) => heading.closest("li")).filter(Boolean) as HTMLElement[];
    expect(shortlistedCards).toHaveLength(2);
    for (const label of ["Open workspace", "View Fit", "Open vacancy"]) {
      const links = shortlistedCards.flatMap((card) => within(card).getAllByRole("link", { name: new RegExp(`^${label} for Repeated role`) }));
      expect(links).toHaveLength(2);
      expect(new Set(links.map((link) => link.getAttribute("aria-label"))).size).toBe(2);
      expect(links.every((link) => link.getAttribute("aria-label")?.includes("decision updated"))).toBe(true);
    }
    const removeButtons = shortlistedCards.flatMap((card) => within(card).getAllByRole("button", { name: /^Remove from shortlist Repeated role/ }));
    expect(removeButtons).toHaveLength(2);
    expect(new Set(removeButtons.map((button) => button.getAttribute("aria-label"))).size).toBe(2);
    expect(removeButtons.every((button) => button.textContent === "Remove from shortlist")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Manage dismissed jobs" }));
    await screen.findByRole("heading", { name: "Dismissed jobs" });
    const dismissedSection = screen.getByRole("heading", { name: "Dismissed jobs" }).closest("section")!;
    const dismissedCards = within(dismissedSection).getAllByRole("heading", { name: "Repeated role" }).map((heading) => heading.closest("li")).filter(Boolean) as HTMLElement[];
    expect(dismissedCards).toHaveLength(2);
    for (const label of ["Open workspace", "View Fit"]) {
      const links = dismissedCards.flatMap((card) => within(card).getAllByRole("link", { name: new RegExp(`^${label} for Repeated role`) }));
      expect(links).toHaveLength(2);
      expect(new Set(links.map((link) => link.getAttribute("aria-label"))).size).toBe(2);
      expect(links.every((link) => link.getAttribute("aria-label")?.includes("decision updated"))).toBe(true);
    }
    const undoButtons = dismissedCards.flatMap((card) => within(card).getAllByRole("button", { name: /^Undo dismissal Repeated role/ }));
    expect(undoButtons).toHaveLength(2);
    expect(new Set(undoButtons.map((button) => button.getAttribute("aria-label"))).size).toBe(2);
    expect(undoButtons.every((button) => button.textContent === "Undo dismissal")).toBe(true);
  });

  it("contextualizes repeated Search history run toggles with status and start time", async () => {
    const first = { ...run("run-a"), started_at: "2026-02-01T12:00:00Z" };
    const second = { ...run("run-b"), started_at: "2026-02-02T12:00:00Z" };
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs": () => json(page([first, second])) });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.click(screen.getByRole("link", { name: "Search history" }));
    await screen.findByRole("heading", { name: "Search history" });
    expect(await screen.findAllByText("Geography incompatible")).toHaveLength(2);
    expect(screen.getAllByText("Geography unknown")).toHaveLength(2);
    const viewButtons = screen.getAllByRole("button", { name: /^View run/ });
    expect(viewButtons).toHaveLength(2);
    expect(new Set(viewButtons.map((button) => button.getAttribute("aria-label"))).size).toBe(2);
    expect(viewButtons.every((button) => /Completed · started/.test(button.getAttribute("aria-label") ?? ""))).toBe(true);
    expect(viewButtons.every((button) => !/run-a|run-b/.test(button.getAttribute("aria-label") ?? ""))).toBe(true);
    fireEvent.click(viewButtons[0]);
    const closeButton = await screen.findByRole("button", { name: /^Close run for Completed · started/ });
    expect(closeButton).toHaveAttribute("aria-expanded", "true");
    expect(closeButton).toHaveTextContent("Close run");
  });

  it("renders all safe integer funnel counters with readable labels and omits non-integer values", async () => {
    const legacyAndCurrentFunnel = {
      submitted: 8,
      fresh_selected: 4,
      reused: 2,
      full_analysis_attempts: 3,
      search_results_raw: 20,
      deterministic_filtered_count: 5,
      geography_eligible: 6,
      geography_incompatible: 2,
      geography_unknown: 1,
      remote_policy_filtered: 1,
      future_stage_count: 7,
      fractional_stage: 7.5,
      unsafe_integer_stage: Number.MAX_SAFE_INTEGER + 1,
      non_integer_stage: "private text must not render",
    } as unknown as Record<string, number>;
    const historyRun = { ...run("funnel-run"), funnel: legacyAndCurrentFunnel };
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs": () => json(page([historyRun])) });
    renderJobs(fetch);
    await loaded();
    fireEvent.click(screen.getByRole("link", { name: "Search history" }));
    await screen.findByRole("heading", { name: "Search history" });

    const funnel = screen.getByLabelText("Discovery funnel");
    for (const label of [
      "Submitted", "Fresh selected", "Reused", "Full-analysis attempts", "Search results",
      "Search results filtered", "Geography eligible", "Geography incompatible", "Geography unknown",
      "Remote policy filtered", "Future Stage",
    ]) expect(within(funnel).getByText(label)).toBeInTheDocument();
    expect(within(funnel).queryByText("Non-vacancy results filtered")).not.toBeInTheDocument();
    expect(within(funnel).getByText("7")).toBeInTheDocument();
    expect(within(funnel).queryByText("7.5")).not.toBeInTheDocument();
    expect(within(funnel).queryByText(String(Number.MAX_SAFE_INTEGER + 1))).not.toBeInTheDocument();
    expect(within(funnel).queryByText("private text must not render")).not.toBeInTheDocument();
  });

  it("prepares from the canonical discovered job ID, cleans questions, and does not gate on SKIP", async () => {
    let body: unknown;
    const alpha = { ...op("alpha", "Alpha", 99), recommendation: "apply" as const };
    const beta = { ...op("beta", "Beta", 1), recommendation: "skip" as const };
    const created = { id: "prep-alpha" } as ApplicationPreparation;
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([alpha, beta, op("gamma", "Gamma", 2)])), "POST /api/v1/applications/prepare": (_url, init) => { body = JSON.parse(String(init?.body)); return json(created, 201); } });
    renderJobs(fetch); await loaded();
    const prepareButtons = screen.getAllByRole("button", { name: /^Prepare application/ });
    expect(prepareButtons).toHaveLength(3); expect(prepareButtons.every((button) => !button.hasAttribute("disabled"))).toBe(true);
    fireEvent.click(prepareButtons[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    expect(within(form).getByRole("combobox", { name: "Target CV pages" })).toHaveValue("2");
    expect(within(form).getByRole("checkbox", { name: "Include a cover letter" })).toBeChecked();
    fireEvent.change(within(form).getByRole("textbox", { name: "Question 1" }), { target: { value: "  Why this role?  " } });
    fireEvent.click(within(form).getByRole("button", { name: "Add question" }));
    fireEvent.change(within(form).getByRole("textbox", { name: "Question 2" }), { target: { value: "   " } });
    fireEvent.change(within(form).getByRole("combobox", { name: "Target CV pages" }), { target: { value: "3" } });
    fireEvent.click(within(form).getByRole("checkbox", { name: "Include a cover letter" }));
    await waitFor(() => expect(within(form).getByRole("button", { name: "Create preparation" })).toBeEnabled());
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    await waitFor(() => expect(body).toEqual({ target: { discovered_job_id: "job-alpha" }, target_pages: 3, include_cover_letter: false, application_questions: ["Why this role?"] }));
    expect(await screen.findByRole("link", { name: "Review this preparation" })).toHaveAttribute("href", "/applications/prep-alpha");
    expect(requestPaths(fetch)).not.toContain("/api/v1/applications");
  });

  it("fails closed when profile display name is missing without requiring preferred email", async () => {
    let posts = 0;
    const fetch = fakeFetch({ "/api/v1/profile": () => json({ detail: "not found" }, 404), "POST /api/v1/applications/prepare": () => { posts += 1; return json({ id: "unexpected" }, 201); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: /^Prepare application/ })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    expect(await within(form).findByRole("alert")).toHaveTextContent("application display name is required");
    expect(within(form).getByRole("link", { name: "Update your profile" })).toHaveAttribute("href", "/profile");
    expect(within(form).queryByRole("button", { name: "Create preparation" })).not.toBeInTheDocument();
    expect(posts).toBe(0);
  });

  it("reports an unavailable target truthfully when the shortlist refresh fails", async () => {
    let opportunityCalls = 0; let posts = 0;
    const fetch = fakeFetch({ "/api/v1/profile": () => json({ display_name: "Current Person" }), "/api/v1/jobs/opportunities": () => { opportunityCalls += 1; return opportunityCalls === 1 ? json(page([op("alpha", "Alpha")] )) : Promise.reject(new TypeError("offline")); }, "POST /api/v1/applications/prepare": () => { posts += 1; return json({ detail: "not found" }, 404); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: /^Prepare application/ }));
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("opportunity refresh could not be confirmed");
    expect(within(form).queryByText(/created/)).not.toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Create preparation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^Close preparation options/ })).toBeEnabled();
    fireEvent.submit(form);
    expect(posts).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: /^Close preparation options/ }));
    expect(screen.queryByRole("form", { name: "Prepare application for Alpha" })).not.toBeInTheDocument();
    expect(opportunityCalls).toBe(2);
  });

  it.each([[422, "did not contain sufficient usable information"], [503, "temporarily unavailable"]] as const)("handles HTTP %s without raw server detail", async (status, message) => {
    const fetch = fakeFetch({ "/api/v1/profile": () => json({ display_name: "Current Person" }), "POST /api/v1/applications/prepare": () => json({ detail: "private provider diagnostic" }, status) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: /^Prepare application/ })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent(message); expect(alert).not.toHaveTextContent("private provider diagnostic");
    expect(within(form).queryByRole("link", { name: "Review this preparation" })).not.toBeInTheDocument();
  });

  it("routes a refreshed candidate-not-ready 409 to CV onboarding", async () => {
    let readinessCalls = 0; let posts = 0;
    const fetch = fakeFetch({ "/api/v1/profile": () => json({ display_name: "Current Person" }), "/api/v1/onboarding/status": () => json(++readinessCalls === 1 ? ready : { ...ready, candidate_context_ready: false }), "POST /api/v1/applications/prepare": () => { posts += 1; return json({ detail: "profile invalid" }, 409); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: /^Prepare application/ })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("confirmed candidate CV is required");
    expect(within(form).getByRole("link", { name: "Continue CV onboarding" })).toHaveAttribute("href", "/profile/cv");
    expect(screen.getByRole("button", { name: /^Close preparation options/ })).toBeEnabled();
    expect(within(form).getByRole("button", { name: "Create preparation" })).toBeDisabled();
    fireEvent.submit(form);
    expect(posts).toBe(1);
  });

  it("routes a refreshed missing-display-name 409 to Profile", async () => {
    let profileCalls = 0; let posts = 0;
    const fetch = fakeFetch({ "/api/v1/profile": () => json(++profileCalls === 1 ? { display_name: "Current Person" } : { display_name: "   " }), "POST /api/v1/applications/prepare": () => { posts += 1; return json({ detail: "profile invalid" }, 409); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: /^Prepare application/ })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("application display name is required");
    expect(within(form).getByRole("link", { name: "Update your profile" })).toHaveAttribute("href", "/profile");
    expect(within(form).queryByRole("button", { name: "Create preparation" })).not.toBeInTheDocument();
    fireEvent.submit(form);
    expect(posts).toBe(1);
  });

  it.each(["onboarding", "profile", "both"] as const)("fails closed after 409 when %s prerequisite refresh fails", async (failedRefreshes) => {
    const onboardingRefreshSucceeds = failedRefreshes === "profile";
    const profileRefreshSucceeds = failedRefreshes === "onboarding";
    let readinessCalls = 0; let profileCalls = 0; let posts = 0;
    const fetch = fakeFetch({
      "/api/v1/onboarding/status": () => {
        readinessCalls += 1;
        if (readinessCalls === 1 || onboardingRefreshSucceeds) return json(ready);
        return Promise.reject(new TypeError("offline"));
      },
      "/api/v1/profile": () => {
        profileCalls += 1;
        if (profileCalls === 1 || profileRefreshSucceeds) return json({ display_name: "Current Person" });
        return Promise.reject(new TypeError("offline"));
      },
      "POST /api/v1/applications/prepare": () => { posts += 1; return json({ detail: "conflict" }, posts === 1 ? 409 : 201); },
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: /^Prepare application/ })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByText("Career-trans could not confirm the current preparation prerequisites. Review your CV and profile, then try again.")).toBeInTheDocument();
    const create = within(form).queryByRole("button", { name: "Create preparation" });
    if (create) expect(create).toBeDisabled();
    expect(screen.getByRole("button", { name: /^Close preparation options/ })).toBeEnabled();
    fireEvent.submit(form);
    expect(posts).toBe(1);
  });

  it("restores preparation after readiness is authoritatively confirmed again", async () => {
    let readinessCalls = 0; let posts = 0;
    const fetch = fakeFetch({
      "/api/v1/onboarding/status": () => {
        readinessCalls += 1;
        if (readinessCalls === 2) return Promise.reject(new TypeError("offline"));
        return json(ready);
      },
      "/api/v1/profile": () => json({ display_name: "Current Person" }),
      "POST /api/v1/applications/prepare": () => { posts += 1; return json(posts === 1 ? { detail: "conflict" } : { id: "prep-alpha" }, posts === 1 ? 409 : 201); },
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: /^Prepare application/ })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByText("Career-trans could not confirm the current preparation prerequisites. Review your CV and profile, then try again.")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Create preparation" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Retry readiness" }));
    await waitFor(() => expect(within(form).getByRole("button", { name: "Create preparation" })).toBeEnabled());
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByRole("link", { name: "Review this preparation" })).toHaveAttribute("href", "/applications/prep-alpha");
    expect(posts).toBe(2);
  });

  it("keeps opportunity B usable when A becomes unavailable after a failed shortlist refresh", async () => {
    let opportunityCalls = 0; let posts = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/opportunities": () => ++opportunityCalls === 1 ? json(page([op("alpha", "Alpha"), op("beta", "Beta")])) : Promise.reject(new TypeError("offline")),
      "POST /api/v1/applications/prepare": (_url, init) => {
        posts += 1;
        const body = JSON.parse(String(init?.body)) as { target: { discovered_job_id: string } };
        return body.target.discovered_job_id === "job-alpha" ? json({ detail: "gone" }, 404) : json({ id: "prep-beta" }, 201);
      },
    });
    renderJobs(fetch); await loaded();
    const panels = screen.getAllByRole("button", { name: /^Prepare application/ });
    fireEvent.click(panels[0]); fireEvent.click(panels[1]);
    const forms = screen.getAllByRole("form", { name: /Prepare application for/ });
    await within(forms[0]).findByRole("button", { name: "Create preparation" });
    await within(forms[1]).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(forms[0]).getByRole("button", { name: "Create preparation" }));
    expect(await within(forms[0]).findByRole("alert")).toHaveTextContent("opportunity refresh could not be confirmed");
    expect(within(forms[0]).getByRole("button", { name: "Create preparation" })).toBeDisabled();
    expect(within(forms[1]).getByRole("button", { name: "Create preparation" })).toBeEnabled();
    fireEvent.click(within(forms[1]).getByRole("button", { name: "Create preparation" }));
    expect(await within(forms[1]).findByRole("link", { name: "Review this preparation" })).toHaveAttribute("href", "/applications/prep-beta");
    expect(posts).toBe(2);
  });

  it("keeps preparation pending/results independent for two opportunity cards", async () => {
    const pending = new Map<string, ReturnType<typeof deferred<Response>>>();
    const fetch = fakeFetch({ "POST /api/v1/applications/prepare": (_url, init) => {
      const body = JSON.parse(String(init?.body)) as { target: { discovered_job_id: string } };
      const request = deferred<Response>(); pending.set(body.target.discovered_job_id, request); return request.promise;
    } });
    renderJobs(fetch); await loaded();
    const buttons = screen.getAllByRole("button", { name: /^Prepare application/ });
    fireEvent.click(buttons[0]); fireEvent.click(buttons[1]);
    const forms = screen.getAllByRole("form", { name: /Prepare application for/ });
    for (const form of forms) await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(forms[0]).getByRole("button", { name: "Create preparation" }));
    expect(await within(forms[0]).findByRole("status")).toHaveTextContent("Alpha: Preparing application");
    expect(within(forms[0]).getByRole("button", { name: "Preparing…" })).toBeDisabled();
    fireEvent.submit(forms[0]);
    expect(within(forms[1]).getByRole("button", { name: "Create preparation" })).toBeEnabled();
    fireEvent.click(within(forms[1]).getByRole("button", { name: "Create preparation" }));
    await waitFor(() => expect(within(forms[1]).getByRole("status")).toHaveTextContent("Beta: Preparing application"));
    pending.get("job-beta")!.resolve(json({ id: "prep-beta" }, 201));
    expect(await within(forms[1]).findByRole("link", { name: "Review this preparation" })).toHaveAttribute("href", "/applications/prep-beta");
    expect(within(forms[0]).queryByRole("link", { name: "Review this preparation" })).not.toBeInTheDocument();
    pending.get("job-alpha")!.resolve(json({ id: "prep-alpha" }, 201));
    expect(await within(forms[0]).findByRole("link", { name: "Review this preparation" })).toHaveAttribute("href", "/applications/prep-alpha");
    expect(requestPaths(fetch).filter((path) => path === "/api/v1/applications/prepare")).toHaveLength(2);
  });

  it("keeps B editor state intact when A fails after B has been opened", async () => {
    const fetch = fakeFetch({ "POST /api/v1/applications/prepare": () => json({ detail: "private provider diagnostic" }, 503) });
    renderJobs(fetch); await loaded();
    const buttons = screen.getAllByRole("button", { name: /^Prepare application/ });
    fireEvent.click(buttons[0]); fireEvent.click(buttons[1]);
    const forms = screen.getAllByRole("form", { name: /Prepare application for/ });
    await within(forms[0]).findByRole("button", { name: "Create preparation" });
    await within(forms[1]).findByRole("button", { name: "Create preparation" });
    fireEvent.change(within(forms[1]).getByRole("textbox", { name: "Question 1" }), { target: { value: "B-only question" } });
    fireEvent.click(within(forms[0]).getByRole("button", { name: "Create preparation" }));
    expect(await within(forms[0]).findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(within(forms[0]).queryByText("private provider diagnostic")).not.toBeInTheDocument();
    expect(within(forms[1]).getByRole("textbox", { name: "Question 1" })).toHaveValue("B-only question");
    expect(within(forms[1]).getByRole("button", { name: "Create preparation" })).toBeEnabled();
  });

  it("does not retry an interrupted preparation and truthfully reconciles persisted history", async () => {
    let posts = 0; let gets = 0;
    const fetch = fakeFetch({ "/api/v1/profile": () => json({ display_name: "Current Person" }), "POST /api/v1/applications/prepare": () => { posts += 1; return Promise.reject(new TypeError("offline")); }, "/api/v1/applications": () => { gets += 1; return json([({ id: "existing", target: { title: "Alpha", canonical_discovered_job_id: "job-alpha" }, created_at: "2026-01-01T00:00:00Z" })]); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: /^Prepare application/ })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByText(/cannot confirm from this response whether a preparation was created/)).toBeInTheDocument();
    expect(within(form).getAllByText(/shown as history only/).length).toBeGreaterThan(0);
    expect(within(form).getByRole("link", { name: /Alpha ·/ })).toHaveAttribute("href", "/applications/existing");
    expect(posts).toBe(1); expect(gets).toBe(1);
  });

  it("reports a failed transport-history reconciliation without retrying creation", async () => {
    let posts = 0; let gets = 0;
    const fetch = fakeFetch({ "/api/v1/profile": () => json({ display_name: "Current Person" }), "POST /api/v1/applications/prepare": () => { posts += 1; return Promise.reject(new TypeError("offline")); }, "/api/v1/applications": () => { gets += 1; return Promise.reject(new TypeError("offline")); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: /^Prepare application/ })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("cannot confirm from this response whether a preparation was created");
    expect(within(form).getByRole("alert")).toHaveTextContent("history could not be confirmed as refreshed");
    expect(posts).toBe(1); expect(gets).toBe(1);
  });

  it("redirects unauthenticated /jobs through the existing sign-in route", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<MemoryRouter initialEntries={["/jobs"]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Jobs" })).not.toBeInTheDocument();
  });

  it("blocks evaluation before candidate context is ready and links to Profile", async () => {
    renderJobs(fakeFetch({ "/api/v1/onboarding/status": () => json({ ...ready, candidate_context_ready: false }) }));
    expect(await screen.findByRole("heading", { name: "Complete your Profile first" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review Profile" })).toHaveAttribute("href", "/profile");
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    expect(screen.queryByRole("heading", { name: "Evaluate selected actionable jobs" })).not.toBeInTheDocument();
  });

  it("keeps Jobs usable for an unconfirmed newer CV and incomplete optional Adviser", async () => {
    renderJobs(fakeFetch({ "/api/v1/onboarding/status": () => json({ ...ready, latest_cv_draft: { ...ready.latest_cv_draft!, state: "review_ready" }, adviser: { ...ready.adviser, intake_exists: true, assessment_status: "stale" } }) }));
    expect(await screen.findByText(/A newer CV update is awaiting review/)).toBeInTheDocument();
    expect(screen.getByText(/Career Adviser completion is optional/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    expect(screen.getByRole("heading", { name: "Evaluate selected actionable jobs" })).toBeInTheDocument();
  });

  it("replaces the entire top-N opportunity window when Show more is used", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": (url) => json(page(url.searchParams.get("limit") === "40" ? [op("new-top", "New top window")] : [op("old-top", "Old window")], true)) });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); expect(await screen.findByText("Old window")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more current opportunities" }));
    expect(await screen.findByText("New top window")).toBeInTheDocument();
    expect(screen.queryByText("Old window")).not.toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities?limit=40");
  });

  it("caps opportunities at 100 and explains a truncated maximum window", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([op("at-limit")], true)) });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); await screen.findByText("at-limit");
    await growToLimit(fetch, "/api/v1/jobs/opportunities", "Show more current opportunities");
    expect(screen.queryByRole("button", { name: "Show more current opportunities" })).not.toBeInTheDocument();
    expect(screen.getByText("Showing the first 100 current opportunities available through this view.")).toBeInTheDocument();
    const limits = requestPaths(fetch).filter((path) => path.startsWith("/api/v1/jobs/opportunities?")).map((path) => Number(new URL(path, window.location.origin).searchParams.get("limit")));
    expect(limits).toContain(100); expect(limits.every((limit) => limit <= 100)).toBe(true); expect(limits).not.toContain(120);
  });

  it("caps discovery runs at 100 and explains a truncated maximum window", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs": () => json(page([run()], true)) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" });
    await growToLimit(fetch, "/api/v1/jobs/search-history", "Show more search history");
    expect(screen.queryByRole("button", { name: "Show more search history" })).not.toBeInTheDocument();
    expect(screen.getByText("Showing the first 100 Search History items available through this view.")).toBeInTheDocument();
    const limits = requestPaths(fetch).filter((path) => path.startsWith("/api/v1/jobs/search-history?")).map((path) => Number(new URL(path, window.location.origin).searchParams.get("limit")));
    expect(limits).toContain(100); expect(limits.every((limit) => limit <= 100)).toBe(true); expect(limits).not.toContain(120);
  });

  it("caps the recent inbox at 100 and explains a truncated maximum window", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/inbox": () => json(page([inboxItem("at-limit")], true)) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    await growToLimit(fetch, "/api/v1/jobs/inbox", "Show more recent vacancies");
    expect(screen.queryByRole("button", { name: "Show more recent vacancies" })).not.toBeInTheDocument();
    expect(screen.getByText("Showing the first 100 recent vacancies available through this view.")).toBeInTheDocument();
    const limits = requestPaths(fetch).filter((path) => path.startsWith("/api/v1/jobs/inbox?")).map((path) => Number(new URL(path, window.location.origin).searchParams.get("limit")));
    expect(limits).toContain(100); expect(limits.every((limit) => limit <= 100)).toBe(true); expect(limits).not.toContain(120);
  });

  it("loads current detail lazily, renders backend explanations safely, and never shows evidence IDs", async () => {
    const fetch = fakeFetch(); renderJobs(fetch); await loaded();
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/opportunities/eval-alpha");
    fireEvent.click(screen.getAllByRole("button", { name: /View detail for/ })[0]);
    expect(await screen.findByRole("article", { name: "Current opportunity detail" })).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities/eval-alpha");
    expect(screen.getByText(/Recommendation reasoning/)).toBeInTheDocument();
    expect(screen.getAllByText(/Canonical requirement/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Requirement reference unavailable.").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Posting recency signal/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/private-evidence-id/)).not.toBeInTheDocument();
    const link = screen.getAllByRole("link", { name: /Open vacancy for/ })[0];
    expect(link).toHaveAttribute("target", "_blank"); expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("removes a no-longer-current opportunity and refreshes the shortlist neutrally", async () => {
    let detailCalls = 0;
    const fetch = fakeFetch({ "GET /api/v1/jobs/opportunities/eval-alpha": () => { detailCalls += 1; return json(undefined, 404); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: /View detail for/ })[0]);
    expect(await screen.findByText(/no longer current/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Current opportunity detail" })).not.toBeInTheDocument();
    expect(detailCalls).toBe(1);
    await waitFor(() => expect(requestPaths(fetch).filter((path) => path.startsWith("/api/v1/jobs/opportunities?")).length).toBeGreaterThan(1));
  });

  it("loads run summaries then lazy detail, labels in-progress rows safely, and retrieves historical detail", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs": () => json(page([run("run-1", "running")])), "/api/v1/jobs/discovery-runs/run-1": () => json(runDetail("running")) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" });
    expect(await screen.findByText("Evaluation in progress")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^View run/ }));
    expect(await screen.findByText("Newly evaluated")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.queryByText("Analysis failed")).not.toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-1");
  });

  it("renders every terminal run outcome and lazy historical job detail as historical", async () => {
    const fetch = fakeFetch(); renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" });
    fireEvent.click(await screen.findByRole("button", { name: /^View run/ }));
    expect(screen.queryByRole("button", { name: /^Prepare application/ })).not.toBeInTheDocument();
    for (const label of ["Newly evaluated", "Reused evaluation", "Not actionable", "Presemantic filtered", "Outside semantic budget", "Semantic rejected", "Outside deep-analysis budget", "Analysis failed"]) expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getAllByText("Geography incompatible")).toHaveLength(2);
    expect(screen.getAllByText("Geography unknown")).toHaveLength(2);
    const historicalActions = screen.getAllByRole("button", { name: /Historical detail for historical result/ });
    expect(historicalActions).toHaveLength(10);
    expect(new Set(historicalActions.map((button) => button.getAttribute("aria-label"))).size).toBe(10);
    expect(historicalActions.every((button) => !button.getAttribute("aria-label")?.includes("job-"))).toBe(true);
    fireEvent.click(screen.getAllByRole("button", { name: /Historical detail for/ })[0]);
    expect(await screen.findByRole("article", { name: "Historical evaluation detail" })).toBeInTheDocument();
    expect(screen.getByText(/does not describe the vacancy or recommendation as current/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "AI runtime used" })).toBeInTheDocument();
    expect(screen.getByText("old-job-relevance · Provider default reasoning")).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-1/jobs/job-0");
    expect(requestPaths(fetch).some((path) => path.startsWith("/api/v1/ai/settings") || path.startsWith("/api/v1/ai/models"))).toBe(false);
  });

  it("shows only the factual historical outcome when the historical snapshot is null", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs/run-1/jobs/job-0": () => json({ ...historyDetail, opportunity: null, outcome: "semantic_rejected" }) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" });
    fireEvent.click(await screen.findByRole("button", { name: /^View run/ }));
    await screen.findByText("Reused evaluation");
    fireEvent.click(screen.getAllByRole("button", { name: /Historical detail for/ })[0]);
    expect(await screen.findByText(/No evaluation snapshot exists for this row/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Historical evaluation detail" })).not.toBeInTheDocument();
  });

  it("describes the shared recent inbox neutrally and keeps non-actionable rows visible but disabled", async () => {
    renderJobs(); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    expect(screen.getByText(/recent shared persisted public vacancies/)).toBeInTheDocument();
    const blocked = screen.getByRole("checkbox", { name: /^Select Inbox blocked/ });
    expect(blocked).toBeDisabled(); expect(screen.getByText(/Not actionable/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /^Prepare application/ })).not.toBeInTheDocument();
  });

  it("replaces the recent inbox window and only submits selected actionable persisted IDs with structured controls", async () => {
    let inboxFetch = 0; let postBody: unknown;
    const fetch = fakeFetch({ "/api/v1/jobs/inbox": (url) => { inboxFetch += 1; return json(page(url.searchParams.get("limit") === "40" ? [inboxItem("new-inbox")] : [inboxItem("actionable"), inboxItem("blocked", false)], true)); }, "POST /api/v1/jobs/discovery-runs": (_url, init) => { postBody = JSON.parse(String(init?.body)); return json({ ...run("new-run"), jobs: [] }); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("button", { name: "Show more recent vacancies" }));
    expect(await screen.findByText("Inbox new-inbox")).toBeInTheDocument(); expect(screen.queryByText("Inbox actionable")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more recent vacancies" }));
    await waitFor(() => expect(inboxFetch).toBeGreaterThan(1));
    expect(await screen.findByText("Inbox actionable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox blocked/ }));
    await editIntentOnFind("AI Engineer\nApplied AI Engineer", "London, United Kingdom", "exclude_remote");
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await waitFor(() => expect(postBody).toBeDefined());
    expect(postBody).toMatchObject({ discovered_job_ids: ["actionable"], query: { keywords: ["AI Engineer", "Applied AI Engineer"], locations: ["London, United Kingdom"], remote_ok: false, companies: [], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 50 }, max_semantic_candidates: 10, max_full_analyses: 5 });
  });

  it("removes a disappearing selected inbox job from selection and submission", async () => {
    let inboxCalls = 0; let postBody: { discovered_job_ids: string[] } | undefined;
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => json(page(inboxCalls++ < 2 ? [inboxItem("selected-A")] : [inboxItem("replacement-B")], true)),
      "POST /api/v1/jobs/discovery-runs": (_url, init) => { postBody = JSON.parse(String(init?.body)); return json({ ...run("run-new"), jobs: [] }); },
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    await editIntentOnFind("AI");
    fireEvent.click(await screen.findByRole("checkbox", { name: /^Select Inbox selected-A/ }));
    expect(screen.getByRole("button", { name: "Evaluate 1 jobs" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Inbox replacement-B")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /^Select Inbox selected-A/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evaluate selected jobs" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox replacement-B/ }));
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await waitFor(() => expect(postBody).toBeDefined());
    expect(postBody?.discovered_job_ids).toEqual(["replacement-B"]);
    expect(postBody?.discovered_job_ids).not.toContain("selected-A");
  });

  it("drops a selected inbox job when the backend changes it to non-actionable", async () => {
    let inboxCalls = 0; let postBody: { discovered_job_ids: string[] } | undefined;
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => json(page(inboxCalls++ === 0 ? [inboxItem("selected-A")] : [inboxItem("selected-A", false), inboxItem("replacement-B")], true)),
      "POST /api/v1/jobs/discovery-runs": (_url, init) => { postBody = JSON.parse(String(init?.body)); return json({ ...run("run-new"), jobs: [] }); },
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    const selected = await screen.findByRole("checkbox", { name: /^Select Inbox selected-A/ }); fireEvent.click(selected);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    const nowBlocked = await screen.findByRole("checkbox", { name: /^Select Inbox selected-A/ });
    expect(nowBlocked).toBeDisabled(); expect(nowBlocked).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Evaluate selected jobs" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox replacement-B/ }));
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await waitFor(() => expect(postBody).toBeDefined());
    expect(postBody?.discovered_job_ids).toEqual(["replacement-B"]);
    expect(postBody?.discovered_job_ids).not.toContain("selected-A");
  });

  it("keeps a long evaluation visibly pending and prevents a duplicate submission", async () => {
    const pending = deferred<Response>(); let postCount = 0;
    const fetch = fakeFetch({ "POST /api/v1/jobs/discovery-runs": () => { postCount += 1; return pending.promise; } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByText(/may take several minutes/)).toBeInTheDocument();
    expect(screen.getByText(/Selected jobs: Inbox actionable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evaluating…" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("heading", { name: "Evaluate selected actionable jobs" }).closest("form")!);
    expect(postCount).toBe(1);
    pending.resolve(json({ ...run("run-new"), jobs: [] }));
    await screen.findByText(/Evaluation completed/);
  });

  it("refreshes successful runs and replaces opportunities from the initial top-N window", async () => {
    let opportunity20 = 0; let runGet = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/opportunities": (url) => {
        if (url.searchParams.get("limit") === "40") return json(page([op("large-window", "Larger window result")], true));
        opportunity20 += 1; return json(page([op(`fresh-${opportunity20}`, opportunity20 === 1 ? "Initial page-one result" : "Fresh page-one result")], opportunity20 === 1));
      },
      "/api/v1/jobs/discovery-runs": () => { runGet += 1; return json(page([run(`run-${runGet}`)])); },
    });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); expect(await screen.findByText("Initial page-one result")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more current opportunities" }));
    expect(await screen.findByText("Larger window result")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" });
    expect(await screen.findByText("Fresh page-one result")).toBeInTheDocument();
    expect(screen.queryByText("Larger window result")).not.toBeInTheDocument();
    expect(runGet).toBeGreaterThan(1);
    expect(requestPaths(fetch).filter((path) => path === "/api/v1/jobs/opportunities?limit=20").length).toBeGreaterThanOrEqual(2);
  });

  it("keeps an interrupted Inbox request uncertain and offers an idempotent retry when no run is visible", async () => {
    let runRequests = 0;
    let postRequests = 0;
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs": () => { runRequests += 1; return json(page([run(`run-${runRequests}`)])); }, "POST /api/v1/jobs/discovery-runs": () => { postRequests += 1; return Promise.reject(new TypeError("offline")); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })); await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByRole("region", { name: "Unconfirmed Inbox evaluation" })).toBeInTheDocument();
    expect(await screen.findByText(/No saved run matches this request ID yet/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Submitted evaluation" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evaluation outcome unconfirmed" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retry this same evaluation safely" })).toBeEnabled();
    await waitFor(() => expect(runRequests).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: "Reconcile run history" }));
    await waitFor(() => expect(runRequests).toBeGreaterThanOrEqual(2));
    expect(postRequests).toBe(1);
    expect(sessionStorage.getItem("career-trans.inbox-evaluation-uncertain")).not.toBeNull();
  });

  it("does not claim run history refreshed when the post-transport-failure GET also fails", async () => {
    let runRequests = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => { runRequests += 1; return json(undefined, 503); },
      "POST /api/v1/jobs/discovery-runs": () => Promise.reject(new TypeError("offline")),
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByRole("region", { name: "Unconfirmed Inbox evaluation" })).toBeInTheDocument();
    expect(await screen.findByText(/saved Search History could not be refreshed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evaluation outcome unconfirmed" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Retry this same evaluation safely" })).not.toBeInTheDocument();
    expect(runRequests).toBe(1);
  });

  it("reconciles an interrupted Inbox request to its exact saved run without reposting", async () => {
    let payload: CreateDiscoveryRun | undefined;
    let posts = 0;
    const runId = "run-reconciled";
    const fetch = fakeFetch({
      "POST /api/v1/jobs/discovery-runs": (_url, init) => {
        posts += 1;
        payload = JSON.parse(String(init?.body)) as CreateDiscoveryRun;
        return Promise.reject(new TypeError("offline"));
      },
      "/api/v1/jobs/search-history": () => {
        const request = payload as CreateDiscoveryRun;
        const summary = {
          ...run(runId), started_at: new Date(Date.now() + 1000).toISOString(),
          run_input: { client_request_id: request.client_request_id, query: request.query, discovered_job_ids: request.discovered_job_ids, max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 },
        };
        summary.run_input.query = { ...request.query, keywords: request.query.keywords.map((value) => value.toLowerCase()), locations: request.query.locations.map((value) => value.toLowerCase()) };
        return json(page([{ type: "discovery_run", id: runId, started_at: summary.started_at, run: summary }]));
      },
      [`/api/v1/jobs/discovery-runs/${runId}`]: () => {
        const request = payload as CreateDiscoveryRun;
        return json({
        ...run(runId), started_at: new Date(Date.now() + 1000).toISOString(),
        run_input: { client_request_id: request.client_request_id, query: { ...request.query, keywords: request.query.keywords.map((value) => value.toLowerCase()), locations: request.query.locations.map((value) => value.toLowerCase()) }, discovered_job_ids: request.discovered_job_ids, max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 },
        jobs: [{ discovered_job_id: "actionable", evaluation_id: null, outcome: "analysis_failed", failure_stage: "career_analysis", failure_kind: "provider_unavailable", opportunity: null }],
      });
      },
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })); await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await waitFor(() => expect(requestPaths(fetch)).toContain(`/api/v1/jobs/discovery-runs/${runId}`));
    expect(await screen.findByRole("region", { name: "Reconciled Inbox evaluation" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review saved evaluation" })).toHaveAttribute("href", `/jobs/history?run=${runId}`);
    expect(screen.queryByRole("region", { name: "Unconfirmed Inbox evaluation" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem("career-trans.inbox-evaluation-uncertain")).toBeNull();
    expect(screen.getByRole("button", { name: "Evaluate selected jobs" })).toBeDisabled();
    expect(posts).toBe(1);
  });

  it("reconciles non-English search terms using backend-compatible Unicode case folding", async () => {
    let payload: CreateDiscoveryRun | undefined;
    const savedInput = () => ({
      client_request_id: payload?.client_request_id,
      query: { keywords: ["strasse"], locations: ["london"], remote_ok: null, companies: [], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 50 },
      discovered_job_ids: ["actionable"], max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5,
    });
    const savedRun = () => ({ ...run("run-unicode"), run_input: savedInput() });
    const fetch = fakeFetch({
      "POST /api/v1/jobs/discovery-runs": (_url, init) => { payload = JSON.parse(String(init?.body)) as CreateDiscoveryRun; return Promise.reject(new TypeError("offline")); },
      "/api/v1/jobs/search-history": () => json(page(payload ? [{ type: "discovery_run", id: "run-unicode", started_at: new Date().toISOString(), run: savedRun() }] : [])),
      "/api/v1/jobs/discovery-runs/run-unicode": () => json({ ...savedRun(), jobs: [{ discovered_job_id: "actionable", evaluation_id: null, outcome: "analysis_failed", failure_stage: "career_analysis", failure_kind: "provider_unavailable", opportunity: null }] }),
    });
    renderJobs(fetch); await loaded();
    await editIntentOnFind("Straße", "London");
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByRole("region", { name: "Reconciled Inbox evaluation" })).toBeInTheDocument();
    expect(payload?.query.keywords).toEqual(["Straße"]);
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-unicode");
  });

  it("treats only a request-validation response as proof that no run was created", async () => {
    let runRequests = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => { runRequests += 1; return json(page([run(`run-${runRequests}`)])); },
      "POST /api/v1/jobs/discovery-runs": () => json({ detail: "Invalid request" }, 422),
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByText(/Career-trans rejected the evaluation before a run could be created\. No evaluation was submitted/)).toBeInTheDocument();
    expect(screen.getByText(/Recent run history has been refreshed/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Submitted evaluation" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evaluate 1 jobs" })).toBeEnabled();
    expect(screen.queryByRole("region", { name: "Unconfirmed Inbox evaluation" })).not.toBeInTheDocument();
    expect(runRequests).toBe(1);
  });

  it("reconciles a run persisted before an HTTP 500 using its request ID", async () => {
    let payload: CreateDiscoveryRun | undefined;
    let postCount = 0;
    const persistedRun = () => ({ ...run("persisted-before-error"), run_input: {
      client_request_id: payload?.client_request_id,
      query: { keywords: ["ai"], locations: ["london"], remote_ok: null, companies: [], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 50 },
      discovered_job_ids: payload?.discovered_job_ids,
      max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5,
    } });
    const fetch = fakeFetch({
      "POST /api/v1/jobs/discovery-runs": (_url, init) => { postCount += 1; payload = JSON.parse(String(init?.body)); return json({ detail: "Evaluation failed after persistence" }, 500); },
      "/api/v1/jobs/search-history": () => json(page([{ type: "discovery_run", id: "persisted-before-error", started_at: new Date().toISOString(), run: persistedRun() }])),
      "/api/v1/jobs/discovery-runs/persisted-before-error": () => json({ ...persistedRun(), jobs: [{ discovered_job_id: "actionable", evaluation_id: null, outcome: "analysis_failed", failure_stage: "career_analysis", failure_kind: "provider_unavailable", opportunity: null }] }),
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    await editIntentOnFind("AI", "London");
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByRole("region", { name: "Reconciled Inbox evaluation" })).toBeInTheDocument();
    expect(screen.queryByText(/may have created a run/)).not.toBeInTheDocument();
    expect(payload?.client_request_id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(postCount).toBe(1);
    expect(screen.queryByRole("button", { name: "Retry this same evaluation safely" })).not.toBeInTheDocument();
  });

  it("does not reconcile a nearby run with normalized content but a different request ID", async () => {
    let payload: CreateDiscoveryRun | undefined;
    const fetch = fakeFetch({
      "POST /api/v1/jobs/discovery-runs": (_url, init) => { payload = JSON.parse(String(init?.body)); return Promise.reject(new TypeError("offline")); },
      "/api/v1/jobs/search-history": () => json(page([{ type: "discovery_run", id: "unmatched-run", started_at: new Date().toISOString(), run: {
        ...run("unmatched-run"), run_input: {
          client_request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          query: { keywords: ["ai"], locations: [], remote_ok: null, companies: [], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 50 },
          discovered_job_ids: ["actionable"],
          max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5,
        },
      } }])) ,
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    await editIntentOnFind("AI"); fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByText(/No saved run matches this request ID yet/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Unconfirmed Inbox evaluation" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })).toBeChecked();
    expect(screen.getByRole("button", { name: "Retry this same evaluation safely" })).toBeEnabled();
    expect(payload?.client_request_id).not.toBe("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    expect(screen.queryByRole("region", { name: "Reconciled Inbox evaluation" })).not.toBeInTheDocument();
  });

  it("restores selected jobs and SearchIntent after reload and retries the same request ID", async () => {
    const requestId = "c05213c1-a74c-4cd9-8c2c-123456789abc";
    const payload: CreateDiscoveryRun = {
      client_request_id: requestId,
      query: { keywords: ["AI"], locations: ["London"], remote_ok: null, companies: [], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 50 },
      discovered_job_ids: ["actionable"], max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5,
    };
    sessionStorage.setItem("career-trans.inbox-evaluation-uncertain", JSON.stringify({ [user.id]: { user_id: user.id, started_at: new Date().toISOString(), payload, titles: ["Inbox actionable"], retry_allowed: true } }));
    let posts = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/search-history": () => json(page([])),
      "POST /api/v1/jobs/discovery-runs": (_url, init) => {
        posts += 1;
        const request = JSON.parse(String(init?.body)) as CreateDiscoveryRun;
        expect(request.client_request_id).toBe(requestId);
        return json({ ...run("recovered-run"), run_input: { client_request_id: requestId, query: { ...request.query, keywords: ["ai"], locations: ["london"] }, discovered_job_ids: request.discovered_job_ids, max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 }, jobs: [] });
      },
    });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByRole("heading", { name: "Inbox" });
    expect(await screen.findByText(/No saved run matches this request ID yet/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })).toBeChecked();
    expect(screen.getByText(/SearchIntent:/).parentElement).toHaveTextContent(/SearchIntent: AI/);
    expect(screen.getByRole("button", { name: "Retry this same evaluation safely" })).toBeEnabled();
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" }));
    expect(await screen.findByLabelText("Prioritisation themes (one per line)")).toHaveValue("AI");
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("button", { name: "Retry this same evaluation safely" }));
    expect(await screen.findByRole("region", { name: "Analysis just completed" })).toBeInTheDocument();
    expect(posts).toBe(1);
    expect(sessionStorage.getItem("career-trans.inbox-evaluation-uncertain")).toBeNull();
  });

  it("discards the pre-evaluation shortlist while the authoritative post-evaluation refresh is pending", async () => {
    let opportunityRequests = 0;
    const refreshedOpportunities = deferred<Response>();
    const fetch = fakeFetch({
      "/api/v1/jobs/opportunities": () => {
        opportunityRequests += 1;
        return opportunityRequests === 1 ? json(page([op("old", "Pre-evaluation shortlist")])) : refreshedOpportunities.promise;
      },
      "POST /api/v1/jobs/discovery-runs": () => json({ ...run("run-new"), jobs: [] }),
    });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); await screen.findByText("Pre-evaluation shortlist");
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));

    await waitFor(() => expect(opportunityRequests).toBe(2));
    expect(await screen.findByText(/Evaluation completed\. Refreshing recent runs and current opportunities/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" });
    expect(screen.queryByText("Pre-evaluation shortlist")).not.toBeInTheDocument();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities?limit=20");

    refreshedOpportunities.resolve(json(page([op("new", "Post-evaluation shortlist")])));
    expect(await screen.findByText("Post-evaluation shortlist")).toBeInTheDocument();
    expect(screen.queryByText("Pre-evaluation shortlist")).not.toBeInTheDocument();
    expect(await screen.findByText(/Evaluation completed\. Recent runs were refreshed; current opportunities were refreshed\./)).toBeInTheDocument();
  });

  it("uses a route-local empty-opportunity message without consulting history", async () => {
    const noItems = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([])), "/api/v1/jobs/discovery-runs": () => json(page([])) });
    renderJobs(noItems); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); expect(await screen.findByText("No current evaluated opportunities.")).toBeInTheDocument(); expect(screen.getByRole("link", { name: "View Search history" })).toHaveAttribute("href", "/jobs/history"); expect(requestPaths(noItems)).not.toContain("/api/v1/jobs/discovery-runs?limit=20"); cleanup(); sessionStorage.clear();
    const oldRuns = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([])), "/api/v1/jobs/discovery-runs": () => json(page([run()])) });
    renderJobs(oldRuns); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" }); await screen.findByText("Completed"); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); expect(await screen.findByText("No current evaluated opportunities.")).toBeInTheDocument(); expect(screen.queryByText(/Historical runs are available below/)).not.toBeInTheDocument();
  });

  it("does not claim historical runs exist when run history is unavailable", async () => {
    renderJobs(fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([])), "/api/v1/jobs/discovery-runs": () => json(undefined, 503) }));
    await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); await screen.findByRole("heading", { name: "Recommended / Current analyses" });
    await waitFor(() => expect(screen.queryByText(/Historical runs are available below/)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" });
    expect(await screen.findByText(/Discovery run history is unavailable/)).toBeInTheDocument();
  });

  it("keeps successfully loaded sections when another section is retried", async () => {
    let opportunities = 0;
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => { opportunities += 1; return opportunities === 1 ? json(undefined, 503) : json(page([op("recovered")])); } });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); expect(await screen.findByText(/Current opportunities are unavailable/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry this section" }));
    expect(await screen.findByText("recovered")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" }); expect(screen.getByText("Completed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" }); expect(screen.getByText("Inbox actionable")).toBeInTheDocument();
  });

  it("uses the existing authenticated 401 session-clear behavior", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => json(undefined, 401) });
    renderJobs(fetch, "/jobs/opportunities/recommended"); expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it("cannot restore an older delayed response after an authenticated request clears the session", async () => {
    const oldOpportunity = deferred<Response>();
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => oldOpportunity.promise, "/api/v1/onboarding/status": () => json(undefined, 401) });
    renderJobs(fetch, "/jobs/opportunities/recommended");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    oldOpportunity.resolve(json(page([op("stale-user", "Stale prior-user opportunity")])));
    await waitFor(() => expect(screen.queryByText("Stale prior-user opportunity")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("ignores a delayed older selected-detail response when a newer card is opened", async () => {
    const first = deferred<Response>(); const second = deferred<Response>();
    const fetch = fakeFetch({ "GET /api/v1/jobs/opportunities/eval-alpha": () => first.promise, "GET /api/v1/jobs/opportunities/eval-beta": () => second.promise });
    renderJobs(fetch); await loaded();
    fireEvent.click(screen.getAllByRole("button", { name: /View detail for/ })[0]);
    fireEvent.click(screen.getByRole("button", { name: /View detail for/ }));
    second.resolve(json(ranked("Newest selected detail"))); expect(await screen.findByText("Newest selected detail")).toBeInTheDocument();
    first.resolve(json(ranked("Stale older detail")));
    await waitFor(() => expect(screen.queryByText("Stale older detail")).not.toBeInTheDocument());
  });

  it("ignores a delayed run detail when the user selects a newer run", async () => {
    const first = deferred<Response>(); const second = deferred<Response>();
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => json(page([run("run-1"), run("run-2")])),
      "GET /api/v1/jobs/discovery-runs/run-1": () => first.promise,
      "GET /api/v1/jobs/discovery-runs/run-2": () => second.promise,
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" });
    fireEvent.click(screen.getAllByRole("button", { name: /^View run/ })[0]);
    fireEvent.click(screen.getByRole("button", { name: /^View run/ }));
    second.resolve(json({ ...runDetail(), jobs: [{ discovered_job_id: "new", evaluation_id: null, outcome: "reused_evaluation", failure_stage: null, failure_kind: null, opportunity: null }] }));
    expect(await screen.findByText("Reused evaluation")).toBeInTheDocument();
    first.resolve(json({ ...runDetail(), jobs: [{ discovered_job_id: "old", evaluation_id: null, outcome: "newly_evaluated", failure_stage: null, failure_kind: null, opportunity: null }] }));
    await waitFor(() => expect(screen.queryByText("Newly evaluated")).not.toBeInTheDocument());
  });

  it("keeps the workspace accessible with semantic headings and labelled controls", async () => {
    renderJobs(); await loaded();
    expect(screen.getByRole("main", { name: "" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    expect(screen.queryByLabelText("Prioritisation themes (one per line)")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit SearchIntent in Find jobs" })).toHaveAttribute("href", "/jobs/find");
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toBeInTheDocument();
    expect(screen.getByText(/SearchIntent is not automatically transferred to local Codex/)).toBeInTheDocument();
  });
});

describe("Issue #234 Find jobs saved-schedule execution", () => {
  const scheduleList = (item: DiscoveryScheduleRead | null = savedSchedule()) => json(item ? [item] : []);
  const scheduleFetch = (item: DiscoveryScheduleRead, overrides: Record<string, Handler> = {}) => fakeFetch({
    "/api/v1/jobs/discovery-schedules": () => scheduleList(item),
    "/api/v1/jobs/discovery-schedules/s-1": () => json(item),
    ...overrides,
  });

  it("fresh-reads Find jobs saved configuration before POST and permits paused manual runs", async () => {
    let posts = 0;
    const fetch = scheduleFetch(savedSchedule(), { "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => { posts += 1; return json(savedExecution()); } });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Completed");
    expect(posts).toBe(1);
    const paths = requestPaths(fetch);
    expect(paths.indexOf("/api/v1/jobs/discovery-schedules/s-1")).toBeLessThan(paths.indexOf("/api/v1/jobs/discovery-schedules/s-1/run-now"));
  });

  it("refreshes the SearchIntent after a changed query and requires a second explicit run", async () => {
    const fresh = savedSchedule({ query: { ...savedSchedule().query, keywords: ["Platform"] } }); let posts = 0;
    const fetch = scheduleFetch(savedSchedule(), { "/api/v1/jobs/discovery-schedules/s-1": () => json(fresh), "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => { posts += 1; return json(savedExecution()); } });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByText(/SearchIntent and persisted channels were refreshed/)).toBeInTheDocument();
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("Platform"); expect(posts).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Run now" })); await waitFor(() => expect(posts).toBe(1));
  });

  it("blocks the first POST and displays changed acquisition channels", async () => {
    const fresh = savedSchedule({ acquisition: { ...savedSchedule().acquisition, structured_ats: { ...savedSchedule().acquisition.structured_ats, enabled: false }, agentic_web: { ...savedSchedule().acquisition.agentic_web, enabled: true } } }); let posts = 0;
    const fetch = scheduleFetch(savedSchedule(), { "/api/v1/jobs/discovery-schedules/s-1": () => json(fresh), "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => { posts += 1; return json(savedExecution()); } });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByText("Profile-driven bounded server-side web discovery")).toBeInTheDocument(); expect(posts).toBe(0);
  });

  it("blocks the first POST and displays changed evaluation configuration", async () => {
    const fresh = savedSchedule({ evaluation: { ...savedSchedule().evaluation, max_full_analyses: 2 } }); let posts = 0;
    const fetch = scheduleFetch(savedSchedule(), { "/api/v1/jobs/discovery-schedules/s-1": () => json(fresh), "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => { posts += 1; return json(savedExecution()); } });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByText(/2 full analyses/)).toBeInTheDocument(); expect(posts).toBe(0);
  });

  it("clears a deleted saved schedule without submitting POST", async () => {
    let lists = 0; let posts = 0;
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-schedules": () => { lists += 1; return scheduleList(lists === 1 ? savedSchedule() : null); }, "/api/v1/jobs/discovery-schedules/s-1": () => json(undefined, 404), "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => { posts += 1; return json(savedExecution()); } });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    await expectStatusContaining("The saved configuration is no longer available. Run now was not submitted."); expect(screen.getByLabelText("Saved search configuration")).toHaveValue(""); expect(posts).toBe(0);
  });

  it("does not call execution history when preflight GET is unavailable", async () => {
    let posts = 0;
    const fetch = scheduleFetch(savedSchedule(), { "/api/v1/jobs/discovery-schedules/s-1": () => Promise.reject(new TypeError("offline")), "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => { posts += 1; return json(savedExecution()); } });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    await expectStatusContaining("Could not confirm the current saved configuration. Run now was not submitted."); expect(posts).toBe(0);
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/discovery-schedules/s-1/executions"); expect(screen.queryByText(/Uncertain execution/)).not.toBeInTheDocument();
  });

  it("does not submit or reconcile when preflight HTTP verification fails", async () => {
    let posts = 0;
    const fetch = scheduleFetch(savedSchedule(), { "/api/v1/jobs/discovery-schedules/s-1": () => json(undefined, 503), "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => { posts += 1; return json(savedExecution()); } });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    await expectStatusContaining("Could not verify the current saved configuration (HTTP 503). Run now was not submitted."); expect(posts).toBe(0); expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/discovery-schedules/s-1/executions");
  });

  it("reports POST HTTP rejection separately from preflight failure", async () => {
    const fetch = scheduleFetch(savedSchedule(), { "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => json(undefined, 503) });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    await expectStatusContaining("The server rejected Run now with HTTP 503. No execution success was confirmed.");
  });

  it("reconciles Find jobs 409 against saved-schedule execution history", async () => {
    let globalRuns = 0;
    const fetch = scheduleFetch(savedSchedule(), { "/api/v1/jobs/discovery-runs": () => { globalRuns += 1; return json(page([run()])); }, "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => json(undefined, 409), "/api/v1/jobs/discovery-schedules/s-1/executions": () => json([]) });
    renderJobs(fetch); await selectSavedSchedule(); const before = globalRuns; fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Already running"); expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-schedules/s-1/executions"); expect(globalRuns).toBe(before); expect(await screen.findByText(/Execution history reconciliation: refreshed/)).toBeInTheDocument();
  });

  it("shows neutral uncertainty after POST interruption without claiming an execution identity", async () => {
    const fetch = scheduleFetch(savedSchedule(), { "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => Promise.reject(new TypeError("offline")), "/api/v1/jobs/discovery-schedules/s-1/executions": () => json([savedExecution("running")]) });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Uncertain"); expect(screen.getByText(/Execution history reconciliation: refreshed/)).toBeInTheDocument(); expect(screen.getByText(/cannot confirm from the interrupted response/)).toBeInTheDocument(); expect(screen.queryByText("execution-1")).not.toBeInTheDocument();
  });

  it("keeps uncertainty visible when saved-schedule history refresh fails", async () => {
    const fetch = scheduleFetch(savedSchedule(), { "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => Promise.reject(new TypeError("offline")), "/api/v1/jobs/discovery-schedules/s-1/executions": () => json(undefined, 503) });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Uncertain"); expect(screen.getByText(/Execution history reconciliation: could not be confirmed as refreshed/)).toBeInTheDocument();
  });

  it("lets stale saved-schedule history supersede generic 409 wording", async () => {
    let lists = 0;
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-schedules": () => { lists += 1; return scheduleList(lists === 1 ? savedSchedule() : null); }, "/api/v1/jobs/discovery-schedules/s-1": () => json(savedSchedule()), "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => json(undefined, 409), "/api/v1/jobs/discovery-schedules/s-1/executions": () => json(undefined, 404) });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    await expectStatusContaining("The saved configuration became unavailable while reconciling this run."); expect(screen.queryByText(/No execution was submitted/)).not.toBeInTheDocument(); expect(screen.getByText(/server had reported that an execution was already running/)).toBeInTheDocument(); expect(screen.getByLabelText("Saved search configuration")).toHaveValue("");
  });

  it("preserves uncertainty when POST interruption is followed by stale execution history", async () => {
    let lists = 0;
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-schedules": () => { lists += 1; return scheduleList(lists === 1 ? savedSchedule() : null); }, "/api/v1/jobs/discovery-schedules/s-1": () => json(savedSchedule()), "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => Promise.reject(new TypeError("offline")), "/api/v1/jobs/discovery-schedules/s-1/executions": () => json(undefined, 404) });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    await expectStatusContaining("The saved configuration became unavailable while reconciling this run."); expect(screen.queryByText(/No execution was submitted/)).not.toBeInTheDocument(); expect(screen.getByText(/cannot confirm whether a new execution was created/)).toBeInTheDocument(); expect(screen.getByLabelText("Saved search configuration")).toHaveValue("");
  });

  it.each([["completed", "Completed"], ["partial_failed", "Partial Failed"]] as const)("keeps %s visible on Find jobs and offers explicit Inbox review", async (status, label) => {
    const fetch = scheduleFetch(savedSchedule(), { "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => json(savedExecution(status)) });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("status")).toHaveTextContent(label); expect(screen.getByRole("heading", { name: "Find jobs" })).toBeInTheDocument(); expect(screen.getByRole("link", { name: "Review recent vacancies" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Review recent vacancies" })); await screen.findByRole("heading", { name: "Inbox" }); expect(await screen.findByRole("heading", { name: "Inbox" })).toBeInTheDocument();
  });

  it.each([["failed", "Failed"], ["skipped", "Skipped"]] as const)("shows %s without success-style Inbox review", async (status, label) => {
    const fetch = scheduleFetch(savedSchedule(), { "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => json(savedExecution(status)) });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("status")).toHaveTextContent(label); expect(screen.queryByRole("link", { name: "Review recent vacancies" })).not.toBeInTheDocument();
  });

  it("keeps execution status when Inbox refresh after completion fails", async () => {
    let inboxCalls = 0;
    const fetch = scheduleFetch(savedSchedule(), { "/api/v1/jobs/inbox": () => { inboxCalls += 1; return Promise.reject(new TypeError("offline")); }, "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => json(savedExecution()) });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Completed"); expect(screen.getByText(/Recent vacancies refresh: could not be confirmed as refreshed/)).toBeInTheDocument(); expect(screen.getByRole("link", { name: "Review recent vacancies" })).toBeInTheDocument();
  });
});

describe("Issue #261 transient one-off discovery", () => {
  it("shows the server policy and launches directly from a transient SearchIntent", async () => {
    let submitted: Record<string, unknown> | undefined;
    const fetch = fakeFetch({ "POST /api/v1/jobs/one-off-discovery/executions": (_url, init) => { submitted = JSON.parse(String(init?.body)); return json(oneOffExecution()); } });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    expect(await screen.findByText(/Tavily · Configured for launch/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "One-off discovery policy" })).toHaveTextContent("Maximum jobs: 20");
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "  AI roles  " } });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs now" }));
    expect(await screen.findByRole("heading", { name: "One-off discovery Completed" })).toBeInTheDocument();
    expect(submitted).toMatchObject({ expected_launch_fingerprint: "a".repeat(64), query: { keywords: ["AI roles"] } });
    expect(submitted?.client_request_id).toEqual(expect.any(String));
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/discovery-schedules/s-1/run-now");
  });

  it("keeps an unavailable provider actionable and does not submit", async () => {
    let posts = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/one-off-discovery/preflight": () => json(oneOffPreflight({ available: false, readiness: "not_configured", reason: "Tavily is not configured." })),
      "POST /api/v1/jobs/one-off-discovery/executions": () => { posts += 1; return json(oneOffExecution()); },
    });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    expect(await screen.findByText(/Tavily is not configured/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review Job Discovery Settings" })).toHaveAttribute("href", "/settings/discovery");
    expect(screen.queryByRole("button", { name: "Find jobs now" })).not.toBeInTheDocument();
    expect(posts).toBe(0);
  });

  it("keeps one-off discovery unavailable while a saved configuration is selected", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-schedules": () => json([savedSchedule()]) });
    renderJobs(fetch); await selectSavedSchedule();
    expect(screen.getByRole("button", { name: "Run now" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Find jobs now" })).not.toBeInTheDocument();
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/one-off-discovery/executions");
  });

  it("disables duplicate one-off launches while the exact request is pending", async () => {
    const pending = deferred<Response>();
    let posts = 0;
    const fetch = fakeFetch({ "POST /api/v1/jobs/one-off-discovery/executions": () => { posts += 1; return pending.promise; } });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    const launch = screen.getByRole("button", { name: "Find jobs now" });
    fireEvent.click(launch);
    expect(await screen.findByText(/One-off job discovery is in progress/)).toBeInTheDocument();
    fireEvent.click(launch);
    expect(posts).toBe(1);
    expect(launch).toBeDisabled();
    pending.resolve(json(oneOffExecution({ acquisition_summary: { canonical_jobs: 0 } })));
    expect(await screen.findByRole("heading", { name: "One-off discovery Completed" })).toBeInTheDocument();
  });

  it.each([["completed", "Completed"], ["partial_failed", "Partial Failed"], ["failed", "Failed"]] as const)("shows truthful %s execution outcomes", async (status, label) => {
    const fetch = fakeFetch({ "POST /api/v1/jobs/one-off-discovery/executions": () => json(oneOffExecution({ status, acquisition_summary: { canonical_jobs: status === "completed" ? 0 : 2, relevance_screened: 1, analysed: 0 } })) });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    fireEvent.click(await screen.findByRole("button", { name: "Find jobs now" }));
    expect(await screen.findByRole("heading", { name: `One-off discovery ${label}` })).toBeInTheDocument();
  });

  it("explains one-off acquisition and post-canonical geography losses in Find jobs", async () => {
    const fetch = fakeFetch({ "POST /api/v1/jobs/one-off-discovery/executions": () => json(oneOffExecution({
      status: "partial_failed",
      acquisition_summary: {
        search_strategies_generated: 6, search_queries_executed: 6, search_results_raw: 18,
        search_results_unique: 14, duplicate_search_results_removed: 4, deterministic_filtered_count: 2,
        pages_selected: 12, pages_opened: 10, page_fetch_failures: 2,
        extraction_successes: 5, extraction_failures: 5, canonical_jobs: 4,
        geography_eligible: 1, geography_incompatible: 1, geography_unknown: 2,
        remote_policy_filtered: 0, relevance_screened: 1, analysed: 1,
      },
      failure_summary: { agentic_web: 1 },
    })) });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    fireEvent.click(await screen.findByRole("button", { name: "Find jobs now" }));

    expect(await screen.findByText("Searches run")).toBeInTheDocument();
    expect(screen.getByText("Geography incompatible")).toBeInTheDocument();
    expect(screen.getByText("Geography unknown")).toBeInTheDocument();
    expect(screen.getByText(/Failures: Agentic Web 1/)).toBeInTheDocument();
  });

  it("refreshes stale preflight without automatically relaunching", async () => {
    let preflights = 0;
    let posts = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/one-off-discovery/preflight": () => { preflights += 1; return json(oneOffPreflight({ launch_fingerprint: preflights === 1 ? "a".repeat(64) : "b".repeat(64) })); },
      "POST /api/v1/jobs/one-off-discovery/executions": () => { posts += 1; return json({ detail: "Job Discovery settings changed; refresh preflight." }, 409); },
    });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs now" }));
    expect(await screen.findByText(/review the refreshed summary, then explicitly start again/i)).toBeInTheDocument();
    expect(preflights).toBe(2);
    expect(posts).toBe(1);
  });

  it("keeps an unresolved interruption behind explicit Reconcile search with the same request id", async () => {
    const submitted: Array<Record<string, unknown>> = [];
    let posts = 0;
    const fetch = fakeFetch({ "POST /api/v1/jobs/one-off-discovery/executions": (_url, init) => {
      submitted.push(JSON.parse(String(init?.body))); posts += 1;
      return posts < 3 ? Promise.reject(new TypeError("offline")) : json(oneOffExecution());
    } });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    await screen.findByRole("button", { name: "Find jobs now" });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs now" }));
    expect(await screen.findByRole("button", { name: "Reconcile search" })).toBeInTheDocument();
    expect(posts).toBe(2);
    expect(submitted[1]).toEqual(submitted[0]);
    fireEvent.click(screen.getByRole("button", { name: "Reconcile search" }));
    expect(await screen.findByRole("heading", { name: "One-off discovery Completed" })).toBeInTheDocument();
    expect(posts).toBe(3);
    expect(submitted[1]).toEqual(submitted[0]);
    expect(submitted[2]).toEqual(submitted[0]);
  });

  it("keeps the exact uncertain payload after a reconciliation HTTP failure", async () => {
    const submitted: Array<Record<string, unknown>> = [];
    const fetch = fakeFetch({ "POST /api/v1/jobs/one-off-discovery/executions": (_url, init) => {
      submitted.push(JSON.parse(String(init?.body)));
      if (submitted.length === 1) return Promise.reject(new TypeError("offline"));
      if (submitted.length === 2) return json({ detail: "Temporary service failure" }, 503);
      return json(oneOffExecution());
    } });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs now" }));
    expect(await screen.findByRole("button", { name: "Reconcile search" })).toBeInTheDocument();
    expect(screen.getByText(/result is uncertain/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reconcile search" }));
    expect(await screen.findByRole("heading", { name: "One-off discovery Completed" })).toBeInTheDocument();
    expect(submitted).toHaveLength(3);
    expect(submitted[1]).toEqual(submitted[0]);
    expect(submitted[2]).toEqual(submitted[0]);
  });

  it("clears obsolete uncertain authority when reconciliation reports stale preflight", async () => {
    const submitted: Array<Record<string, unknown>> = [];
    let preflights = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/one-off-discovery/preflight": () => json(oneOffPreflight({ launch_fingerprint: (++preflights === 1 ? "a" : "b").repeat(64) })),
      "POST /api/v1/jobs/one-off-discovery/executions": (_url, init) => {
        submitted.push(JSON.parse(String(init?.body)));
        return submitted.length === 1 ? Promise.reject(new TypeError("offline")) : json({ detail: "Job Discovery settings changed; refresh preflight." }, 409);
      },
    });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs now" }));
    expect(await screen.findByText(/review the refreshed summary, then explicitly start again/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Find jobs now" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reconcile search" })).not.toBeInTheDocument();
    expect(preflights).toBe(2);
    expect(submitted).toHaveLength(2);
    expect(submitted[1]).toEqual(submitted[0]);
  });

  it("does not retry an interrupted one-off POST after the user session changes", async () => {
    const first = deferred<Response>();
    let posts = 0;
    let meCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/users/me": () => json(meCalls++ === 0 ? user : { ...user, id: "user-b" }),
      "POST /api/v1/jobs/one-off-discovery/executions": () => { posts += 1; return first.promise; },
    });
    renderJobsWithAuthSwitcher(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    fireEvent.click(await screen.findByRole("button", { name: "Find jobs now" }));
    expect(posts).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Switch user" }));
    first.reject(new TypeError("interrupted"));
    expect(await screen.findByText(/session changed during this search/i)).toBeInTheDocument();
    expect(posts).toBe(1);
    expect(screen.queryByRole("button", { name: "Reconcile search" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /One-off discovery Completed/ })).not.toBeInTheDocument();
  });

  it("does not reconcile an uncertain request after session replacement during explicit reconciliation", async () => {
    const third = deferred<Response>();
    const submitted: Array<Record<string, unknown>> = [];
    let meCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/users/me": () => json(meCalls++ === 0 ? user : { ...user, id: "user-b" }),
      "POST /api/v1/jobs/one-off-discovery/executions": (_url, init) => {
        submitted.push(JSON.parse(String(init?.body)));
        return submitted.length < 3 ? Promise.reject(new TypeError("offline")) : third.promise;
      },
    });
    renderJobsWithAuthSwitcher(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    fireEvent.click(await screen.findByRole("button", { name: "Find jobs now" }));
    fireEvent.click(await screen.findByRole("button", { name: "Reconcile search" }));
    expect(submitted).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Switch user" }));
    third.reject(new TypeError("interrupted"));
    expect(await screen.findByText(/session changed during this search/i)).toBeInTheDocument();
    expect(submitted).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Reconcile search" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /One-off discovery Completed/ })).not.toBeInTheDocument();
  });

  it("retries an interrupted POST with the same request id to reconcile its execution", async () => {
    const submitted: Array<Record<string, unknown>> = [];
    let count = 0;
    const fetch = fakeFetch({ "POST /api/v1/jobs/one-off-discovery/executions": (_url, init) => {
      submitted.push(JSON.parse(String(init?.body)));
      count += 1;
      return count === 1 ? Promise.reject(new TypeError("offline")) : json(oneOffExecution());
    } });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI roles" } });
    fireEvent.click(screen.getByRole("button", { name: "Find jobs now" }));
    expect(await screen.findByRole("heading", { name: "One-off discovery Completed" })).toBeInTheDocument();
    expect(submitted).toHaveLength(2);
    expect(submitted[1]).toEqual(submitted[0]);
  });

  it("projects a linked evaluation as one Search History entry", async () => {
    const linked = oneOffExecution({ discovery_run_id: "run-1" });
    const fetch = fakeFetch({ "/api/v1/jobs/search-history": () => json(page([{ type: "one_off", id: linked.id, started_at: linked.started_at, execution: linked }])) });
    renderJobs(fetch, "/jobs/history");
    expect(await screen.findByRole("heading", { name: "Search history" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "One-off discovery · Completed" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: /Completed/ })).toHaveLength(1);
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/discovery-runs?limit=20");
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/one-off-discovery/executions");
    fireEvent.click(screen.getByRole("link", { name: "View evaluated results" }));
    expect(await screen.findByRole("heading", { name: "Per-job outcomes" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: /One-off discovery · Completed/ })).toHaveLength(1);
  });

  it("paginates the authoritative mixed history without duplicate linked-run rows", async () => {
    const linked = oneOffExecution({ discovery_run_id: "linked-run" });
    const firstItems = Array.from({ length: 20 }, (_, index) => index === 5
      ? { type: "one_off" as const, id: linked.id, started_at: linked.started_at, execution: linked }
      : { type: "discovery_run" as const, id: `ordinary-${index}`, started_at: `2026-10-01T00:${String(index).padStart(2, "0")}:00Z`, run: run(`ordinary-${index}`) });
    const allItems = [...firstItems, { type: "discovery_run" as const, id: "ordinary-next", started_at: "2026-09-30T23:00:00Z", run: run("ordinary-next") }];
    const fetch = fakeFetch({ "/api/v1/jobs/search-history": (url) => json({ items: allItems.slice(0, Number(url.searchParams.get("limit"))), limit: Number(url.searchParams.get("limit")), truncated: Number(url.searchParams.get("limit")) < allItems.length }) });
    renderJobs(fetch, "/jobs/history");
    await screen.findByRole("heading", { name: "One-off discovery · Completed" });
    expect(screen.getAllByRole("listitem")).toHaveLength(20);
    fireEvent.click(screen.getByRole("button", { name: "Show more search history" }));
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(21));
    expect(screen.getAllByRole("listitem")).toHaveLength(21);
    expect(requestPaths(fetch).filter((path) => path.startsWith("/api/v1/jobs/search-history?")).length).toBeGreaterThan(1);
    expect(requestPaths(fetch).some((path) => path.startsWith("/api/v1/jobs/discovery-runs?"))).toBe(false);
    expect(requestPaths(fetch).some((path) => path.startsWith("/api/v1/jobs/one-off-discovery/executions?"))).toBe(false);
  });
});

describe("Issue #230 route and shell foundation", () => {
  it("uses Find jobs as the canonical Jobs route", async () => {
    renderJobs(fakeFetch());
    expect(await screen.findByRole("heading", { name: "Find jobs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Find jobs" })).toHaveAttribute("aria-current", "page");
    cleanup(); sessionStorage.clear();
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fakeFetch());
    render(<MemoryRouter initialEntries={["/jobs"]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Find jobs" })).toBeInTheDocument();
  });

  it("hands a transient SearchIntent to saved searches without creating or running it", async () => {
    const { fetch } = renderJobs(fakeFetch());
    await screen.findByRole("heading", { name: "Find jobs" });
    const themes = screen.getByLabelText("Prioritisation themes (one per line)");
    fireEvent.change(themes, { target: { value: "Agentic" } }); expect(themes).toHaveValue("Agentic");
    fireEvent.change(themes, { target: { value: "Agentic " } }); expect(themes).toHaveValue("Agentic ");
    fireEvent.change(themes, { target: { value: "Agentic AI   " } }); expect(themes).toHaveValue("Agentic AI   ");
    fireEvent.change(themes, { target: { value: "Agentic AI   \n" } }); expect(themes).toHaveValue("Agentic AI   \n");
    fireEvent.change(themes, { target: { value: "Agentic AI   \n\nApplied AI\n" } }); expect(themes).toHaveValue("Agentic AI   \n\nApplied AI\n");
    fireEvent.click(screen.getByRole("button", { name: "Save or configure search" }));
    expect(await screen.findByRole("heading", { name: "New saved discovery" })).toBeInTheDocument();
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("Agentic AI\nApplied AI");
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/discovery-schedules/s-1/run-now");
    expect(requestPaths(fetch).filter((path) => path === "/api/v1/jobs/discovery-schedules").length).toBeGreaterThan(0);
  });

  it("resets raw textarea text when another saved configuration becomes authoritative", async () => {
    const first = savedSchedule({ name: "First", query: { ...savedSchedule().query, keywords: ["Agentic AI"] } });
    const second = savedSchedule({ id: "s-2", name: "Second", query: { ...savedSchedule().query, keywords: ["Applied AI"] } });
    renderJobs(fakeFetch({ "/api/v1/jobs/discovery-schedules": () => json([first, second]) }));
    await screen.findByRole("heading", { name: "Find jobs" });
    await screen.findByRole("option", { name: "First" });
    const select = screen.getByLabelText("Saved search configuration");
    fireEvent.change(select, { target: { value: "s-1" } });
    const themes = screen.getByLabelText("Prioritisation themes (one per line)");
    expect(themes).toHaveValue("Agentic AI");
    fireEvent.change(themes, { target: { value: "Unsaved previous text " } });
    expect(themes).toHaveValue("Unsaved previous text ");
    fireEvent.change(select, { target: { value: "s-2" } });
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("Applied AI");
  });

  it("protects Home and sends unauthenticated visitors to sign in", async () => {
    const fetch = fakeFetch();
    vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/home"]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(requestPaths(fetch)).not.toContain("/api/v1/onboarding/status");
  });

  it("renders Home links and uses only the provider-free readiness endpoint", async () => {
    const { fetch } = renderJobs(fakeFetch(), "/home");
    expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
    expect(await screen.findByText("Your confirmed profile context is ready for the workspace.")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(nav).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Home", "/home"], ["Profile", "/profile"], ["Job Search", "/jobs/find"], ["Applications", "/applications"], ["Tracking", "/tracking"], ["Settings", "/settings/ai"],
    ]);
    expect(within(nav).getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Career-trans" })).toHaveAttribute("href", "/home");
    expect(requestPaths(fetch).filter((path) => path === "/api/v1/onboarding/status")).toHaveLength(1);
    expect(requestPaths(fetch)).not.toEqual(expect.arrayContaining([
      "/api/v1/profile/snapshot", "/api/v1/applications", "/api/v1/jobs/opportunities?limit=20", "/api/v1/tracking",
    ]));
  });

  it("shows a recoverable readiness error on Home", async () => {
    let attempts = 0;
    const fetch = fakeFetch({ "/api/v1/onboarding/status": () => { attempts += 1; return attempts === 1 ? json(undefined, 503) : json(ready); } });
    renderJobs(fetch, "/home");
    expect(await screen.findByRole("alert")).toHaveTextContent("Readiness is unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry readiness" }));
    expect(await screen.findByText("Your confirmed profile context is ready for the workspace.")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("keeps StrictMode readiness owned by the replacement request and recovers after failure", async () => {
    const stale = deferred<Response>();
    let attempts = 0;
    const fetch = fakeFetch({ "/api/v1/onboarding/status": () => {
      attempts += 1;
      if (attempts === 1) return stale.promise;
      if (attempts === 2) return Promise.reject(new TypeError("offline"));
      return json(ready);
    } });
    renderJobs(fetch, "/jobs/find", false, true);
    expect(await screen.findByRole("alert")).toHaveTextContent("Candidate readiness is unavailable");
    stale.resolve(json(ready));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Candidate readiness is unavailable"));
    expect(screen.queryByText("Checking candidate readiness…")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry readiness" }));
    await waitFor(() => expect(screen.queryByText("Checking candidate readiness…")).not.toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "Complete your Profile first" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(attempts).toBe(3);
  });

  it("lets a StrictMode replacement request establish not-ready while ignoring its late predecessor", async () => {
    const stale = deferred<Response>();
    let attempts = 0;
    const fetch = fakeFetch({ "/api/v1/onboarding/status": () => ++attempts === 1 ? stale.promise : json({ ...ready, candidate_context_ready: false }) });
    renderJobs(fetch, "/jobs/find", false, true);
    expect(await screen.findByRole("heading", { name: "Complete your Profile first" })).toBeInTheDocument();
    stale.resolve(json(ready));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Complete your Profile first" })).toBeInTheDocument());
    expect(screen.queryByText("Checking candidate readiness…")).not.toBeInTheDocument();
    expect(attempts).toBeGreaterThanOrEqual(2);
  });

  it("does not let a prior-session readiness request overwrite the replacement session", async () => {
    const priorSession = deferred<Response>();
    let readinessCalls = 0;
    let userCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/users/me": () => json(userCalls++ === 0 ? user : { ...user, id: "user-2", email: "second@example.test" }),
      "/api/v1/onboarding/status": () => ++readinessCalls === 1 ? priorSession.promise : json({ ...ready, candidate_context_ready: false }),
    });
    sessionStorage.setItem(TOKEN, "test-token");
    vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/jobs/find"]}><AuthProvider><App /><AuthSwitcher /></AuthProvider></MemoryRouter>);
    await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.click(screen.getByRole("button", { name: "Switch user" }));
    expect(await screen.findByRole("heading", { name: "Complete your Profile first" })).toBeInTheDocument();
    priorSession.resolve(json(ready));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Complete your Profile first" })).toBeInTheDocument());
    expect(readinessCalls).toBeGreaterThanOrEqual(2);
  });

  it("keeps root and unknown paths as replace redirects to Profile while /jobs redirects to Find jobs", async () => {
    for (const path of ["/", "/unknown"]) {
      cleanup();
      sessionStorage.setItem(TOKEN, "test-token");
      vi.stubGlobal("fetch", fakeFetch());
      render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /><RouteLocation /></AuthProvider></MemoryRouter>);
      expect(await screen.findByRole("heading", { name: "Your career profile" })).toBeInTheDocument();
      expect(screen.getByLabelText("Route location")).toHaveTextContent("/profile:REPLACE");
    }
    cleanup(); sessionStorage.clear(); sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fakeFetch());
    render(<MemoryRouter initialEntries={["/jobs"]}><AuthProvider><App /><RouteLocation /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Find jobs" })).toBeInTheDocument();
    expect(screen.getByLabelText("Route location")).toHaveTextContent("/jobs/find:REPLACE");
  });

  it.each([
    ["/cv", "/profile/cv"],
    ["/adviser", "/profile/adviser"],
    ["/jobs/searches", "/jobs/find/saved"],
    ["/settings", "/settings/ai"],
  ])("keeps compatibility alias %s as a replace redirect to %s", async (legacyPath, canonicalPath) => {
    cleanup(); sessionStorage.clear(); sessionStorage.setItem(TOKEN, "test-token");
    const overrides: Record<string, Handler> = {};
    if (legacyPath === "/cv") overrides["/api/v1/onboarding/status"] = () => json({ ...ready, candidate_context_ready: false, latest_cv_draft: null });
    if (legacyPath === "/adviser") overrides["/api/v1/onboarding/status"] = () => json({ ...ready, candidate_context_ready: false });
    vi.stubGlobal("fetch", fakeFetch(overrides));
    render(<MemoryRouter initialEntries={[legacyPath]}><AuthProvider><App /><RouteLocation /></AuthProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Route location")).toHaveTextContent(`${canonicalPath}:REPLACE`));
  });

  it.each(["/profile/cv", "/cv"]) ("preserves the CV workflow at %s", async (path) => {
    renderJobs(fakeFetch({ "/api/v1/onboarding/status": () => json({ ...ready, candidate_context_ready: false, latest_cv_draft: null }) }), path);
    expect(await screen.findByRole("heading", { name: "Upload your CV" })).toBeInTheDocument();
  });

  it.each(["/profile/adviser", "/adviser"]) ("preserves the Adviser workflow at %s", async (path) => {
    renderJobs(fakeFetch({ "/api/v1/onboarding/status": () => json({ ...ready, candidate_context_ready: false }) }), path);
    expect(await screen.findByRole("heading", { name: "Set up Career Adviser" })).toBeInTheDocument();
  });
});

describe("Issue #236 Phase 5 Job Search route family", () => {
  it("keeps the Job Search shell mounted while canonical sections and SearchIntent state change", async () => {
    const { fetch } = renderJobs(fakeFetch());
    await screen.findByRole("heading", { name: "Find jobs" });
    const navigation = screen.getByRole("navigation", { name: "Job Search sections" });
    expect(within(navigation).getByRole("link", { name: "Find jobs" })).toHaveAttribute("href", "/jobs/find");
    expect(within(navigation).getByRole("link", { name: "Inbox" })).toHaveAttribute("href", "/jobs/inbox");
    expect(within(navigation).getByRole("link", { name: "My opportunities" })).toHaveAttribute("href", "/jobs/opportunities/recommended");
    expect(within(navigation).getByRole("link", { name: "Search history" })).toHaveAttribute("href", "/jobs/history");

    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Applied AI" } });
    fireEvent.click(within(navigation).getByRole("link", { name: "Inbox" }));
    await screen.findByRole("heading", { name: "Inbox" });
    expect(screen.getByText(/Applied AI/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Prioritisation themes (one per line)")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Edit SearchIntent in Find jobs" }));
    await screen.findByRole("heading", { name: "Find jobs" });
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("Applied AI");
    expect(screen.getByRole("link", { name: "Job Search" })).toHaveAttribute("aria-current", "page");
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/opportunities?limit=20");
  });

  it("loads the authoritative Shortlisted decision projection without reading opportunity state", async () => {
    const { fetch } = renderJobs(fakeFetch(), "/jobs/opportunities/shortlisted");
    expect(await screen.findByRole("heading", { name: "Shortlisted" })).toBeInTheDocument();
    expect(await screen.findByText(/No shortlisted jobs yet/)).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/decisions?decision=shortlisted&limit=20");
    expect(requestPaths(fetch).some((path) => path.startsWith("/api/v1/jobs/opportunities"))).toBe(false);
    expect(requestPaths(fetch).some((path) => path.startsWith("/api/v1/jobs/inbox"))).toBe(false);
    expect(requestPaths(fetch).some((path) => path.startsWith("/api/v1/jobs/discovery-runs"))).toBe(false);
  });

  it("renders a non-actionable shortlisted decision and refetches after removal", async () => {
    let listCalls = 0;
    const item = { discovered_job_id: "job-short", title: "Shortlisted role", company: "Public Co", location: "London", url: "https://public.example.test/short", posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", state: "inactive" as const, verification_status: "verified" as const, verification_reason: null, actionable: false, last_seen_at: "2026-02-02T00:00:00Z", decision: "shortlisted" as const, revision: 1, created_at: "2026-02-01T00:00:00Z", updated_at: "2026-02-02T00:00:00Z" };
    const fetch = fakeFetch({
      "/api/v1/jobs/decisions": (url) => { listCalls += 1; return json(url.searchParams.get("decision") === "shortlisted" && listCalls === 1 ? page([item]) : page([])); },
      "PUT /api/v1/jobs/decisions/job-short": () => json(decision("job-short")),
    });
    renderJobs(fetch, "/jobs/opportunities/shortlisted");
    expect(await screen.findByText("Shortlisted role")).toBeInTheDocument();
    expect(screen.getByText(/Not actionable/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Remove from shortlist/ }));
    await waitFor(() => expect(listCalls).toBeGreaterThan(1));
    expect(screen.queryByText("Shortlisted role")).not.toBeInTheDocument();
  });

  it("loads history deep links directly and preserves run/job query navigation", async () => {
    const { fetch } = renderJobs(fakeFetch(), "/jobs/history?run=run-1&job=job-0");
    expect(await screen.findByRole("heading", { name: "Search history" })).toBeInTheDocument();
    expect(await screen.findByRole("article", { name: "Historical evaluation detail" })).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-1");
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-1/jobs/job-0");
  });

  it("closes historical detail and removes only the job query", async () => {
    renderJobs(fakeFetch(), "/jobs/history", true);
    await screen.findByRole("heading", { name: "Search history" });
    await screen.findByText("Completed");
    fireEvent.click(screen.getByRole("button", { name: /^View run/ }));
    await screen.findByText("Per-job outcomes");
    fireEvent.click(screen.getAllByRole("button", { name: /Historical detail for/ })[0]);
    expect(await screen.findByRole("article", { name: "Historical evaluation detail" })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /Historical detail for/ })[0]);
    expect(screen.queryByRole("article", { name: "Historical evaluation detail" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Route location")).toHaveTextContent("/jobs/history?run=run-1:");
  });

  it("renders a direct history run and job when the bounded history list fails", async () => {
    const directRun = { ...runDetail(), id: "old-run", jobs: [{ ...runDetail().jobs[0], discovered_job_id: "old-job" }] };
    const directJob = { ...historyDetail, discovered_job_id: "old-job", opportunity: ranked("Old historical role") };
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => json(undefined, 503),
      "/api/v1/jobs/discovery-runs/old-run": () => json(directRun),
      "/api/v1/jobs/discovery-runs/old-run/jobs/old-job": () => json(directJob),
    });
    renderJobs(fetch, "/jobs/history?run=old-run&job=old-job", true);
    expect(await screen.findByRole("heading", { name: "Search history" })).toBeInTheDocument();
    expect(await screen.findByText(/Discovery run history is unavailable/)).toBeInTheDocument();
    expect(await screen.findByText("Old historical role")).toBeInTheDocument();
    expect(await screen.findByRole("article", { name: "Historical evaluation detail" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Close run/ }));
    expect(screen.getByLabelText("Route location")).toHaveTextContent("/jobs/history:REPLACE");
    expect(screen.queryByRole("region", { name: "Selected search history run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Historical evaluation detail" })).not.toBeInTheDocument();
  });

  it("closes a selected run that is outside the bounded history list", async () => {
    const directRun = { ...runDetail(), id: "old-run", jobs: [{ ...runDetail().jobs[0], discovered_job_id: "old-job" }] };
    const directJob = { ...historyDetail, discovered_job_id: "old-job", opportunity: ranked("Old historical role") };
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => json(page([run("run-1")])),
      "/api/v1/jobs/discovery-runs/old-run": () => json(directRun),
      "/api/v1/jobs/discovery-runs/old-run/jobs/old-job": () => json(directJob),
    });
    renderJobs(fetch, "/jobs/history?run=old-run&job=old-job", true);
    expect(await screen.findByText("Old historical role")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Selected search history run" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Close run/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Close run/ }));
    expect(screen.getByLabelText("Route location")).toHaveTextContent("/jobs/history:REPLACE");
    expect(screen.queryByRole("region", { name: "Selected search history run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Historical evaluation detail" })).not.toBeInTheDocument();
  });

  it("does not duplicate a selected run when a stale history refresh fails", async () => {
    let historyCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => { historyCalls += 1; return historyCalls === 1 ? json(page([run("run-1")])) : json(undefined, 503); },
      "/api/v1/jobs/discovery-runs/run-1": () => json(runDetail()),
    });
    renderJobs(fetch, "/jobs/history");
    await screen.findByText("Completed");
    fireEvent.click(screen.getByRole("button", { name: /^View run/ }));
    await screen.findByText("Per-job outcomes");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Refresh failed. Previously loaded information is still shown.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Close run/ })).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "Selected search history run" })).not.toBeInTheDocument();
  });

  it.each(["/jobs/history?run=run-1", "/jobs/history?run=missing-run"])("settles Search History after a StrictMode direct visit to %s", async (path) => {
    const fetch = fakeFetch({
      "GET /api/v1/jobs/discovery-runs/missing-run": () => json({ detail: "not found" }, 404),
    });
    renderJobs(fetch, path, false, true);
    await screen.findByRole("heading", { name: "Search history" });
    await waitFor(() => expect(screen.queryByText("Loading…")).not.toBeInTheDocument());
    expect(screen.queryByText("Loading discovery runs…")).not.toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/search-history?limit=20");
  });

  it("normalizes a job-only history URL and supports browser back and forward", async () => {
    sessionStorage.setItem(TOKEN, "test-token");
    const fetch = fakeFetch();
    vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/jobs/history?job=job-0"]}><AuthProvider><App /><RouteLocation /><HistoryControls /></AuthProvider></MemoryRouter>);
    await screen.findByRole("heading", { name: "Search history" });
    await waitFor(() => expect(screen.getByLabelText("Route location")).toHaveTextContent("/jobs/history:REPLACE"));
    fireEvent.click(screen.getByRole("button", { name: /^View run/ }));
    await screen.findByText("Per-job outcomes");
    fireEvent.click(screen.getAllByRole("button", { name: /Historical detail for/ })[0]);
    await screen.findByRole("article", { name: "Historical evaluation detail" });
    fireEvent.click(screen.getByRole("button", { name: "Back history" }));
    await waitFor(() => expect(screen.getByLabelText("Route location")).toHaveTextContent("/jobs/history?run=run-1:POP"));
    expect(screen.queryByRole("article", { name: "Historical evaluation detail" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back history" }));
    await waitFor(() => expect(screen.getByLabelText("Route location")).toHaveTextContent("/jobs/history:POP"));
    fireEvent.click(screen.getByRole("button", { name: "Forward history" }));
    await waitFor(() => expect(screen.getByLabelText("Route location")).toHaveTextContent("/jobs/history?run=run-1:POP"));
  });

  it.each([
    ["/jobs/opportunities/recommended", "Recommended / Current analyses"],
    ["/jobs/opportunities/shortlisted", "Shortlisted"],
  ])("marks only the active My opportunities child route for %s", async (path, heading) => {
    renderJobs(fakeFetch(), path);
    await screen.findByRole("heading", { name: heading });
    const navigation = screen.getByRole("navigation", { name: "My opportunities sections" });
    expect(within(navigation).getAllByRole("link", { current: "page" })).toHaveLength(1);
  });
});

describe("Issue #236 Phase 5 repair regressions", () => {
  it("preserves, re-resolves, and clears selected saved schedules across the Job Search family", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-schedules": () => json([savedSchedule()]) });
    renderJobs(fetch); await selectSavedSchedule();
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("AI");
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    expect(screen.getByLabelText("Saved search configuration")).toHaveValue("s-1");
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("AI");
    expect(screen.getByText(/5 full analyses/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    expect(screen.getByLabelText("Saved search configuration")).toHaveValue("s-1");
    expect(screen.getByText(/5 full analyses/)).toBeInTheDocument();
  });

  it("re-resolves the selected saved schedule when Find jobs refreshes it", async () => {
    let lists = 0;
    const refreshed = savedSchedule({ query: { ...savedSchedule().query, keywords: ["Refreshed"] }, evaluation: { ...savedSchedule().evaluation, max_full_analyses: 2 } });
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-schedules": () => { lists += 1; return json(lists === 1 ? [savedSchedule()] : [refreshed]); } });
    renderJobs(fetch); await selectSavedSchedule();
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    expect(screen.getByLabelText("Saved search configuration")).toHaveValue("s-1");
    expect(screen.getByText(/2 full analyses/)).toBeInTheDocument();
    expect(lists).toBeGreaterThan(1);
  });

  it("clears a selected saved schedule when a route-family refresh shows it was deleted", async () => {
    let lists = 0; let posts = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-schedules": () => { lists += 1; return json(lists === 1 ? [savedSchedule()] : []); },
      "/api/v1/jobs/discovery-schedules/s-1": () => json(undefined, 404),
      "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => { posts += 1; return json(savedExecution()); },
    });
    renderJobs(fetch); await selectSavedSchedule();
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Preserved transient intent" } });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    expect(screen.getByLabelText("Saved search configuration")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Run now" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("Preserved transient intent");
    expect(posts).toBe(0);
  });

  it("keeps a deferred Run now result visible after navigating to Inbox and back", async () => {
    const pending = deferred<Response>(); let posts = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-schedules": () => json([savedSchedule()]),
      "/api/v1/jobs/discovery-schedules/s-1": () => json(savedSchedule()),
      "POST /api/v1/jobs/discovery-schedules/s-1/run-now": () => { posts += 1; return pending.promise; },
    });
    renderJobs(fetch); await selectSavedSchedule(); fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("button", { name: "Running…" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    pending.resolve(json(savedExecution()));
    await waitFor(() => expect(posts).toBe(1));
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    expect(await screen.findByRole("status")).toHaveTextContent("Completed");
  });

  it("keeps a deferred Inbox evaluation snapshot and blocks SearchIntent edits until completion", async () => {
    const pending = deferred<Response>(); let posts = 0;
    const fetch = fakeFetch({ "POST /api/v1/jobs/discovery-runs": () => { posts += 1; return pending.promise; } });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByRole("heading", { name: "Inbox" }); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Deferred AI" } });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByRole("button", { name: "Evaluating…" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("Deferred AI");
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toBeDisabled();
    pending.resolve(json({ ...run("run-new"), jobs: [] }));
    await waitFor(() => expect(posts).toBe(1));
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    expect(await screen.findByText(/Search themes: Deferred AI/)).toBeInTheDocument();
  });

  it("resets transient Job Search state after leaving the route family", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-schedules": () => json([savedSchedule()]) });
    renderJobs(fetch); await selectSavedSchedule();
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Transient state" } });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" }); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("link", { name: "Profile" })); await screen.findByRole("heading", { name: "Your career profile" });
    fireEvent.click(screen.getByRole("link", { name: "Job Search" })); await screen.findByRole("heading", { name: "Find jobs" });
    expect(screen.getByLabelText("Saved search configuration")).toHaveValue("");
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("");
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" }); await screen.findByText("Inbox actionable");
    expect(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })).not.toBeChecked();
  });

  it("keeps Job Search mounted while navigating the canonical job workspace sections", async () => {
    const workspace = { job: { id: "actionable", title: "Workspace role", company: "Public Co", location: "London", url: "https://public.example.test/actionable", description: "Public description", posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", detail_authority: "provider_detail", verification_status: "verified", verification_reason: null, state: "new", actionable: true, first_seen_at: "2026-02-01T00:00:00Z", last_seen_at: "2026-02-02T00:00:00Z", last_changed_at: "2026-02-01T00:00:00Z" }, provenance: { items: [], count: 0, limit: 20, truncated: false }, current_fit: { status: "none", reason: "no_current_evaluation", evaluation: null }, evaluations: { items: [], limit: 20, truncated: false } };
    const fetch = fakeFetch({ "/api/v1/jobs/workspaces/actionable": () => json(workspace) });
    renderJobs(fetch, "/jobs/actionable");
    await screen.findByRole("heading", { name: "Workspace role" });
    expect(screen.getByRole("navigation", { name: "Job Search sections" })).toBeInTheDocument();
    const workspaceNav = screen.getByRole("navigation", { name: "Job workspace sections" });
    expect(within(workspaceNav).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page")).toHaveLength(1);
    fireEvent.click(within(workspaceNav).getByRole("link", { name: "Fit" }));
    await screen.findByRole("heading", { name: "Current Fit" });
    expect(screen.getByText(/No analysis is current for the present job, candidate and evaluation configuration/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Job Search" })).toHaveClass("active");
    expect(fetch.mock.calls.filter(([input]) => String(input).includes("/api/v1/jobs/workspaces/actionable")).length).toBe(1);
  });

  it("retains the exact successful Inbox analysis result across workspace navigation", async () => {
    const workspace = { job: { id: "actionable", title: "Exact returned role", company: "Public Co", location: "London", url: "https://public.example.test/actionable", description: null, posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", detail_authority: "provider_detail", verification_status: "verified", verification_reason: null, state: "new", actionable: true, first_seen_at: "2026-02-01T00:00:00Z", last_seen_at: "2026-02-02T00:00:00Z", last_changed_at: "2026-02-01T00:00:00Z" }, provenance: { items: [], count: 0, limit: 20, truncated: false }, current_fit: { status: "none", reason: "no_current_evaluation", evaluation: null }, evaluations: { items: [], limit: 20, truncated: false } };
    const exactRun = { ...run("run-exact"), search_input_fingerprint: "search", candidate_evaluation_fingerprint: "candidate", evaluation_contract_fingerprint: "contract", jobs: [{ discovered_job_id: "actionable", evaluation_id: "eval-exact", outcome: "newly_evaluated", failure_stage: null, failure_kind: null, opportunity: ranked("Exact returned role") }] };
    const fetch = fakeFetch({ "POST /api/v1/jobs/discovery-runs": () => json(exactRun), "/api/v1/jobs/workspaces/actionable": () => json(workspace) });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByRole("heading", { name: "Inbox" }); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Exact analysis" } });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" }); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await screen.findByRole("heading", { name: "Analysis just completed" });
    fireEvent.click(screen.getAllByRole("link", { name: /Open workspace for/ })[0]);
    await screen.findByRole("heading", { name: "Exact returned role" });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" }));
    await screen.findByRole("heading", { name: "Inbox" });
    expect(screen.getByRole("heading", { name: "Analysis just completed" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /View Fit for/ }).some((link) => link.getAttribute("href") === "/jobs/actionable/fit")).toBe(true);
    expect(screen.getByText("Newly evaluated")).toBeInTheDocument();
    expect(screen.getByText("eval-exact")).toBeInTheDocument();
    expect(screen.getByText("CONSIDER")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open exact run result for/ })).toHaveAttribute("href", "/jobs/history?run=run-exact&job=actionable");
  });

  it("keeps exact factual outcomes and actions for rows without opportunities", async () => {
    const exactRun = { ...run("run-exact-no-opportunity"), search_input_fingerprint: "search", candidate_evaluation_fingerprint: "candidate", evaluation_contract_fingerprint: "contract", jobs: [{ discovered_job_id: "blocked", evaluation_id: null, outcome: "semantic_rejected" as const, failure_stage: "relevance", failure_kind: "not_relevant", opportunity: null }, { discovered_job_id: "blocked-2", evaluation_id: null, outcome: "outside_semantic_budget" as const, failure_stage: null, failure_kind: null, opportunity: null }] };
    const fetch = fakeFetch({ "POST /api/v1/jobs/discovery-runs": () => json(exactRun) });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Exact analysis" } });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })); fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await screen.findByRole("heading", { name: "Analysis just completed" });
    expect(screen.getByText("Semantic rejected")).toBeInTheDocument();
    const workspaceLinks = screen.getAllByRole("link", { name: /Open workspace for returned result/ });
    const exactResultLinks = screen.getAllByRole("link", { name: /Open exact run result for returned result/ });
    expect(workspaceLinks).toHaveLength(2);
    expect(exactResultLinks).toHaveLength(2);
    expect(new Set(workspaceLinks.map((link) => link.getAttribute("aria-label"))).size).toBe(2);
    expect(new Set(exactResultLinks.map((link) => link.getAttribute("aria-label"))).size).toBe(2);
    expect(workspaceLinks.every((link) => !link.getAttribute("aria-label")?.includes("blocked"))).toBe(true);
    expect(exactResultLinks.every((link) => !link.getAttribute("aria-label")?.includes("blocked"))).toBe(true);
    expect(workspaceLinks.map((link) => link.getAttribute("aria-label"))).toEqual(expect.arrayContaining(["Open workspace for returned result 1", "Open workspace for returned result 2"]));
    expect(exactResultLinks.map((link) => link.getAttribute("href"))).toEqual(expect.arrayContaining(["/jobs/history?run=run-exact-no-opportunity&job=blocked", "/jobs/history?run=run-exact-no-opportunity&job=blocked-2"]));
    expect(screen.queryByRole("link", { name: "Open Fit" })).not.toBeInTheDocument();
  });

  it("renders current, historical, and unknown applicability as distinct presentations", async () => {
    const baseJob = { id: "actionable", title: "Applicability role", company: "Public Co", location: "London", url: "https://public.example.test/actionable", description: null, posted_at: "2026-02-01T00:00:00Z", work_arrangement: "Hybrid", employment_type: "Full-time", detail_authority: "provider_detail", verification_status: "verified", verification_reason: null, state: "new", actionable: true, first_seen_at: "2026-02-01T00:00:00Z", last_seen_at: "2026-02-02T00:00:00Z", last_changed_at: "2026-02-01T00:00:00Z" };
    const evaluation = (id: string, applicability: "current" | "historical" | "unknown") => ({ id, created_at: "2026-02-03T00:00:00Z", applicability, opportunity: ranked("Applicability role"), runtime_attribution: null });
    const workspace = { job: baseJob, provenance: { items: [], count: 0, limit: 20, truncated: false }, current_fit: { status: "none", reason: "no_current_evaluation", evaluation: null }, evaluations: { items: [evaluation("eval-current", "current"), evaluation("eval-historical", "historical"), evaluation("eval-unknown", "unknown")], limit: 20, truncated: false } };
    const fetch = fakeFetch({ "/api/v1/jobs/workspaces/actionable": () => json(workspace) });
    renderJobs(fetch, "/jobs/actionable/fit"); await screen.findByRole("heading", { name: "Current Fit" });
    expect(screen.getByText("Current evaluation")).toBeInTheDocument();
    expect(screen.getByText("Historical evaluation snapshot")).toBeInTheDocument();
    expect(screen.getByText("Historical posting recency signal")).toBeInTheDocument();
    expect(screen.getByText("Applicability unavailable")).toBeInTheDocument();
    expect(screen.getByText("Persisted posting recency signal")).toBeInTheDocument();
    expect(screen.getByText(/Currentness could not be established/)).toBeInTheDocument();
    expect(screen.queryAllByText(/This is historical evaluation state/)).toHaveLength(1);
    const unknownDetail = screen.getByRole("article", { name: "Evaluation applicability unavailable" });
    expect(within(unknownDetail).queryByText("Posting recency signal", { exact: true })).not.toBeInTheDocument();
  });

  it("shows Current Fit timestamp, evaluation ID, and runtime attribution independently of history", async () => {
    const workspace = { job: { id: "actionable", title: "Current role", company: "Public Co", location: "London", url: "https://public.example.test/actionable", description: null, posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", detail_authority: "provider_detail", verification_status: "verified", verification_reason: null, state: "new", actionable: true, first_seen_at: "2026-02-01T00:00:00Z", last_seen_at: "2026-02-02T00:00:00Z", last_changed_at: "2026-02-01T00:00:00Z" }, provenance: { items: [], count: 0, limit: 20, truncated: false }, current_fit: { status: "current", reason: null, evaluation: { id: "eval-current-only", created_at: "2026-02-03T00:00:00Z", applicability: "current", opportunity: ranked("Current role"), runtime_attribution: historyDetail.runtime_attribution } }, evaluations: { items: [], limit: 20, truncated: false } };
    const fetch = fakeFetch({ "/api/v1/jobs/workspaces/actionable": () => json(workspace) });
    renderJobs(fetch, "/jobs/actionable/fit"); await screen.findByRole("heading", { name: "Current Fit" });
    expect(screen.getByText("eval-current-only")).toBeInTheDocument();
    expect(screen.getByText(new Date("2026-02-03T00:00:00Z").toLocaleString())).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "AI runtime used" })).toBeInTheDocument();
    expect(screen.getByText("old-job-relevance · Provider default reasoning")).toBeInTheDocument();
  });

  it.each(["/jobs/actionable", "/jobs/actionable/fit", "/jobs/actionable/application", "/jobs/actionable/tracking"])("shows safe not-found state for missing canonical jobs at %s", async (path) => {
    const fetch = fakeFetch({ "/api/v1/jobs/workspaces/actionable": () => json(undefined, 404) });
    renderJobs(fetch, path);
    expect(await screen.findByRole("heading", { name: "Job Workspace not found" })).toBeInTheDocument();
  });

  it.each(["/jobs/opportunities", "/jobs/find/fit", "/jobs/find/application", "/jobs/inbox/fit", "/jobs/history/tracking", "/jobs/searches/fit", "/jobs/%"])("does not treat reserved or malformed route %s as a workspace ID", async (path) => {
    const fetch = fakeFetch();
    renderJobs(fetch, path);
    await waitFor(() => expect(requestPaths(fetch).length).toBeGreaterThan(0));
    expect(requestPaths(fetch).some((request) => request.startsWith("/api/v1/jobs/workspaces/"))).toBe(false);
  });
});

describe("Issue #238 final Phase 6 lifecycle regressions", () => {
  const workspace = { job: { id: "actionable", title: "Workspace role", company: "Public Co", location: "London", url: "https://public.example.test/actionable", description: null, posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", detail_authority: "provider_detail", verification_status: "verified", verification_reason: null, state: "new", actionable: true, first_seen_at: "2026-02-01T00:00:00Z", last_seen_at: "2026-02-02T00:00:00Z", last_changed_at: "2026-02-01T00:00:00Z" }, provenance: { items: [], count: 0, limit: 20, truncated: false }, current_fit: { status: "none", reason: "no_current_evaluation", evaluation: null }, evaluations: { items: [], limit: 20, truncated: false } };

  it("preserves SearchIntent, selected schedule, and selected Inbox job through Workspace", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-schedules": () => json([savedSchedule()]), "/api/v1/jobs/workspaces/actionable": () => json(workspace) });
    renderJobs(fetch); await selectSavedSchedule();
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Persistent workspace intent" } });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getAllByRole("link", { name: /Open workspace for/ }).find((link) => link.getAttribute("href") === "/jobs/actionable")!);
    await screen.findByRole("heading", { name: "Workspace role" });
    fireEvent.click(screen.getByRole("link", { name: "Job Search" })); await screen.findByRole("heading", { name: "Find jobs" });
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("Persistent workspace intent");
    expect(screen.getByLabelText("Saved search configuration")).toHaveValue("s-1");
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    expect(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })).toBeChecked();
  });

  it("keeps the exact result visible when history and opportunities refreshes fail", async () => {
    const exactRun = { ...run("run-refresh-failure"), search_input_fingerprint: "search", candidate_evaluation_fingerprint: "candidate", evaluation_contract_fingerprint: "contract", jobs: [{ discovered_job_id: "actionable", evaluation_id: "eval-refresh-failure", outcome: "reused_evaluation" as const, failure_stage: null, failure_kind: null, opportunity: ranked("Exact refresh failure role") }] };
    const fetch = fakeFetch({ "POST /api/v1/jobs/discovery-runs": () => json(exactRun), "/api/v1/jobs/discovery-runs": () => json(undefined, 503), "/api/v1/jobs/opportunities": () => json(undefined, 503) });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Refresh failure" } });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })); fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await screen.findByRole("heading", { name: "Analysis just completed" });
    expect(screen.getByText(/Exact refresh failure role/)).toBeInTheDocument();
    expect(screen.getByText("eval-refresh-failure")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open exact run result for/ })).toHaveAttribute("href", "/jobs/history?run=run-refresh-failure&job=actionable");
  });

  it("supersedes result A immediately when uncertain request B starts", async () => {
    let posts = 0;
    const resultA = { ...run("run-a"), search_input_fingerprint: "a", candidate_evaluation_fingerprint: "a", evaluation_contract_fingerprint: "a", jobs: [{ discovered_job_id: "actionable", evaluation_id: "eval-a", outcome: "newly_evaluated" as const, failure_stage: null, failure_kind: null, opportunity: ranked("Result A") }] };
    const fetch = fakeFetch({ "POST /api/v1/jobs/discovery-runs": () => { posts += 1; return posts === 1 ? json(resultA) : Promise.reject(new Error("transport interrupted")); } });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Two submissions" } });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" }); fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })); fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await screen.findByText(/Result A/);
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })); fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await screen.findByText(/evaluation request was interrupted/);
    expect(screen.queryByText(/Result A/)).not.toBeInTheDocument();
    expect(screen.queryByText("run-a")).not.toBeInTheDocument();
  });

  it("clears the exact result when leaving and returning to the Job Search family", async () => {
    const exactRun = { ...run("run-leave"), search_input_fingerprint: "search", candidate_evaluation_fingerprint: "candidate", evaluation_contract_fingerprint: "contract", jobs: [{ discovered_job_id: "actionable", evaluation_id: "eval-leave", outcome: "newly_evaluated" as const, failure_stage: null, failure_kind: null, opportunity: ranked("Leave result") }] };
    const fetch = fakeFetch({ "POST /api/v1/jobs/discovery-runs": () => json(exactRun) });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByRole("heading", { name: "Inbox" }); fireEvent.click(screen.getByRole("link", { name: "Find jobs" })); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Leave family" } }); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" }); fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ })); fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await screen.findByText(/Leave result/); fireEvent.click(screen.getByRole("link", { name: "Profile" })); await screen.findByRole("heading", { name: "Your career profile" }); fireEvent.click(screen.getByRole("link", { name: "Job Search" })); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    expect(screen.queryByRole("heading", { name: "Analysis just completed" })).not.toBeInTheDocument();
  });

  it.each([
    [{ status: "none", reason: "job_not_actionable", evaluation: null }, /not currently actionable/],
    [{ status: "none", reason: "no_current_evaluation", evaluation: null }, /No analysis is current for the present job, candidate and evaluation configuration/],
    [{ status: "unavailable", reason: "candidate_not_ready", evaluation: null }, /confirmed candidate context is not ready/],
    [{ status: "unavailable", reason: "candidate_evidence_incomplete", evaluation: null }, /candidate evidence is not fully materialised/],
    [{ status: "unavailable", reason: "runtime_configuration_unavailable", evaluation: null }, /current evaluation configuration could not be confirmed/],
  ] as const)("presents Current Fit reason %s distinctly", async (current_fit, copy) => {
    const fitWorkspace = { ...workspace, current_fit };
    const fetch = fakeFetch({ "/api/v1/jobs/workspaces/actionable": () => json(fitWorkspace) });
    renderJobs(fetch, "/jobs/actionable/fit"); await screen.findByRole("heading", { name: "Current Fit" });
    expect(screen.getByText(copy)).toBeInTheDocument();
  });
});

describe("Issue #240 Phase 7 decision authority regressions", () => {
  it("uses a synchronous per-job lock while allowing independent jobs", async () => {
    let puts = 0;
    const fetch = fakeFetch({
      "PUT /api/v1/jobs/decisions/job-a": () => { puts += 1; return json({ ...undecidedDecision("job-a"), decision: "shortlisted", revision: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }); },
      "PUT /api/v1/jobs/decisions/job-b": () => { puts += 1; return json({ ...undecidedDecision("job-b"), decision: "shortlisted", revision: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }); },
    });
    vi.stubGlobal("fetch", fetch); sessionStorage.setItem(TOKEN, "test-token");
    render(<MemoryRouter><AuthProvider><DecisionProbe /></AuthProvider></MemoryRouter>);
    expect(await screen.findByText(user.id)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mutate A twice" }));
    fireEvent.click(screen.getByRole("button", { name: "Mutate B" }));
    await waitFor(() => expect(puts).toBe(2));
    expect(fetch.mock.calls.filter(([input, init]) => String(input).includes("/api/v1/jobs/decisions/job-a") && init?.method === "PUT")).toHaveLength(1);
  });

  it("does not reconcile under a replacement session after an interrupted mutation", async () => {
    const request = deferred<Response>();
    let exactGets = 0;
    const fetch = fakeFetch({
      "PUT /api/v1/jobs/decisions/job-a": () => request.promise,
      "GET /api/v1/jobs/decisions/job-a": () => { exactGets += 1; return json({ ...undecidedDecision("job-a"), decision: "dismissed", revision: 2 }); },
    });
    vi.stubGlobal("fetch", fetch); sessionStorage.setItem(TOKEN, "test-token");
    render(<MemoryRouter><AuthProvider><DecisionProbe /></AuthProvider></MemoryRouter>);
    expect(await screen.findByText(user.id)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mutate A twice" }));
    fireEvent.click(screen.getByRole("button", { name: "Replace session" }));
    request.reject(new TypeError("interrupted"));
    await waitFor(() => expect(exactGets).toBe(0));
    expect(screen.queryByText(/could not be confirmed/)).not.toBeInTheDocument();
  });

  it("offers immediate Inbox Undo using the confirmed dismissed revision and refetches", async () => {
    let inboxCalls = 0;
    const putBodies: unknown[] = [];
    const dismissed = { ...inboxItem("actionable"), decision: { ...decision("actionable", "undecided"), revision: null } };
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => { inboxCalls += 1; return json(inboxCalls === 1 ? page([inboxItem("actionable")]) : inboxCalls === 2 ? page([]) : page([inboxItem("actionable")])); },
      "PUT /api/v1/jobs/decisions/actionable": (_url, init) => { putBodies.push(JSON.parse(String(init?.body))); return json({ ...dismissed.decision, decision: "dismissed", revision: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:01Z" }); },
    });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByRole("heading", { name: "Inbox" }); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getAllByRole("button", { name: /^Dismiss/ })[0]);
    await waitFor(() => expect(screen.queryByText("Inbox actionable")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.getByText("Inbox actionable")).toBeInTheDocument());
    expect(putBodies).toHaveLength(2);
    expect(putBodies[1]).toMatchObject({ decision: "undecided", expected_revision: 1 });
    expect(inboxCalls).toBeGreaterThanOrEqual(3);
  });

  it("does not present reconciled stale Dismiss as a successful dismissal", async () => {
    let inboxCalls = 0;
    const current = { ...inboxItem("actionable"), decision: { ...decision("actionable"), revision: 1 } };
    const reconciled = { ...current, decision: { ...current.decision, decision: "shortlisted" as const, revision: 2 } };
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => json(page([inboxCalls++ === 0 ? current : reconciled])),
      "PUT /api/v1/jobs/decisions/actionable": () => json({ detail: "Decision changed elsewhere." }, 409),
      "GET /api/v1/jobs/decisions/actionable": () => json(reconciled.decision),
    });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getAllByRole("button", { name: /^Dismiss/ })[0]);
    await waitFor(() => expect(screen.getByLabelText("Shortlisted")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    expect(screen.getByText(/decision changed elsewhere/)).toBeInTheDocument();
    expect(fetch.mock.calls.filter(([input, init]) => String(input).includes("/api/v1/jobs/decisions/actionable") && init?.method === "PUT")).toHaveLength(1);
  });

  it("retains a transport-reconciled Dismissed state with current-state wording and revision", async () => {
    let puts = 0; let inboxCalls = 0;
    const current = { ...inboxItem("actionable"), decision: { ...decision("actionable"), revision: 1 } };
    const dismissed = { ...current.decision, decision: "dismissed" as const, revision: 2 };
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => json(page(inboxCalls++ === 0 ? [current] : [])),
      "PUT /api/v1/jobs/decisions/actionable": (_url, init) => { puts += 1; return puts === 1 ? Promise.reject(new TypeError("offline")) : json({ ...dismissed, decision: "undecided", revision: 3 }); },
      "GET /api/v1/jobs/decisions/actionable": () => json(dismissed),
    });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getAllByRole("button", { name: /^Dismiss/ })[0]);
    expect(await screen.findByText("Current decision is Dismissed.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
    expect(screen.queryByText("Job dismissed.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(puts).toBe(2));
    const undoRequest = fetch.mock.calls.find(([input, init]) => String(input).includes("/api/v1/jobs/decisions/actionable") && init?.method === "PUT" && JSON.parse(String(init?.body)).decision === "undecided");
    expect(JSON.parse(String(undoRequest?.[1]?.body))).toMatchObject({ expected_revision: 2 });
  });

  it("clears a stale Undo banner when reconciliation reports a changed shortlisted state", async () => {
    let puts = 0;
    let inboxCalls = 0;
    const current = { ...inboxItem("actionable"), decision: decision("actionable") };
    const dismissed = { ...current.decision, decision: "dismissed" as const, revision: 3 };
    const shortlisted = { ...current.decision, decision: "shortlisted" as const, revision: 4 };
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => json(inboxCalls++ === 0 ? page([current]) : inboxCalls === 1 ? page([]) : page([{ ...current, decision: shortlisted }])),
      "PUT /api/v1/jobs/decisions/actionable": () => { puts += 1; return puts === 1 ? json(dismissed) : json({ detail: "Decision changed elsewhere." }, 409); },
      "GET /api/v1/jobs/decisions/actionable": () => json(shortlisted),
    });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getAllByRole("button", { name: /^Dismiss/ })[0]);
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.getByLabelText("Shortlisted")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    expect(screen.getByText(/decision changed elsewhere/)).toBeInTheDocument();
  });

  it("shows uncertainty without inventing state when both mutation and reconciliation fail", async () => {
    let inboxCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => { inboxCalls += 1; return json(page([inboxItem("actionable")])); },
      "PUT /api/v1/jobs/decisions/actionable": () => Promise.reject(new TypeError("offline")),
      "GET /api/v1/jobs/decisions/actionable": () => Promise.reject(new TypeError("offline")),
    });
    renderJobs(fetch, "/jobs/inbox"); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getAllByRole("button", { name: /^Dismiss/ })[0]);
    expect(await screen.findByText(/could not be confirmed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    expect(inboxCalls).toBe(1);
  });

  it("ignores a stale Show more Shortlisted response after authoritative removal", async () => {
    const oldResponse = deferred<Response>();
    const item = { discovered_job_id: "job-short", title: "Stale shortlisted role", company: "Public Co", location: "London", url: "https://public.example.test/short", posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", state: "inactive" as const, verification_status: "verified" as const, verification_reason: null, actionable: false, last_seen_at: "2026-02-02T00:00:00Z", decision: "shortlisted" as const, revision: 1, created_at: "2026-02-01T00:00:00Z", updated_at: "2026-02-02T00:00:00Z" };
    let calls = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/decisions": (url) => { if (url.searchParams.get("decision") !== "shortlisted") return json(page([])); calls += 1; if (url.searchParams.get("limit") === "40") return oldResponse.promise; return json(calls === 1 ? page([item], true) : page([])); },
      "PUT /api/v1/jobs/decisions/job-short": () => json({ ...item, decision: "undecided", revision: 2 }),
    });
    renderJobs(fetch, "/jobs/opportunities/shortlisted"); await screen.findByText(item.title);
    fireEvent.click(screen.getByRole("button", { name: "Show more shortlisted jobs" }));
    await waitFor(() => expect(requestPaths(fetch)).toContain("/api/v1/jobs/decisions?decision=shortlisted&limit=40"));
    fireEvent.click(screen.getByRole("button", { name: /^Remove from shortlist/ }));
    await waitFor(() => expect(screen.queryByText(item.title)).not.toBeInTheDocument());
    oldResponse.resolve(json(page([item], true)));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText(item.title)).not.toBeInTheDocument();
  });

  it("loads Dismissed management lazily and ignores stale Show more after Undo", async () => {
    const oldResponse = deferred<Response>();
    const shortlisted = { discovered_job_id: "job-short", title: "Shortlisted anchor", company: "Public Co", location: "London", url: "https://public.example.test/short", posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", state: "new" as const, verification_status: "verified" as const, verification_reason: null, actionable: true, last_seen_at: "2026-02-02T00:00:00Z", decision: "shortlisted" as const, revision: 1, created_at: "2026-02-01T00:00:00Z", updated_at: "2026-02-02T00:00:00Z" };
    const item = { ...shortlisted, discovered_job_id: "job-dismissed", title: "Stale dismissed role", decision: "dismissed" as const };
    const listCalls = { shortlisted: 0, dismissed: 0 };
    const fetch = fakeFetch({
      "/api/v1/jobs/decisions": (url) => { const kind = url.searchParams.get("decision") as "shortlisted" | "dismissed"; listCalls[kind] += 1; if (kind === "shortlisted") return json(page([shortlisted])); if (url.searchParams.get("limit") === "40") return oldResponse.promise; return json(listCalls.dismissed === 1 ? page([item], true) : page([])); },
      "PUT /api/v1/jobs/decisions/job-dismissed": () => json({ ...item, decision: "undecided", revision: 2 }),
    });
    renderJobs(fetch, "/jobs/opportunities/shortlisted"); await screen.findByText(shortlisted.title);
    fireEvent.click(screen.getByRole("button", { name: "Manage dismissed jobs" }));
    await screen.findByText(item.title);
    fireEvent.click(screen.getByRole("button", { name: "Show more dismissed jobs" }));
    await waitFor(() => expect(requestPaths(fetch)).toContain("/api/v1/jobs/decisions?decision=dismissed&limit=40"));
    fireEvent.click(screen.getByRole("button", { name: /^Undo dismissal/ }));
    await waitFor(() => expect(screen.queryByText(item.title)).not.toBeInTheDocument());
    oldResponse.resolve(json(page([item], true)));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText(item.title)).not.toBeInTheDocument();
  });

  it("keeps Recommended refreshes on the active expanded window after dismissal", async () => {
    let dismissed = false;
    const fetch = fakeFetch({
      "/api/v1/jobs/opportunities": (url) => { const limit = url.searchParams.get("limit"); if (limit === "20") return json(page([op("initial", "Initial")], true)); if (limit === "40") return json(page([op(dismissed ? "backfill" : "expanded", dismissed ? "Backfill" : "Expanded")], true)); if (limit === "60") return json(page([op("sixty", "Sixty")], true)); return json(page([])); },
      "PUT /api/v1/jobs/decisions/job-expanded": () => { dismissed = true; return json({ ...undecidedDecision("job-expanded"), decision: "dismissed", revision: 1 }); },
    });
    renderJobs(fetch); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); await screen.findByText("Initial");
    fireEvent.click(screen.getByRole("button", { name: "Show more current opportunities" }));
    expect(await screen.findByText("Expanded")).toBeInTheDocument();
    const card = screen.getByText("Expanded").closest("li")!;
    fireEvent.click(within(card).getByRole("button", { name: /^Dismiss/ }));
    await waitFor(() => expect(screen.getByText("Backfill")).toBeInTheDocument());
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities?limit=40");
    fireEvent.click(screen.getByRole("button", { name: "Show more current opportunities" }));
    await waitFor(() => expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities?limit=60"));
  });

  it("prunes a dismissed selected Inbox job before the authoritative refresh resolves", async () => {
    const inboxRefresh = deferred<Response>();
    let inboxCalls = 0;
    let submitted: unknown;
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => { inboxCalls += 1; return inboxCalls === 1 ? json(page([inboxItem("actionable")])) : inboxRefresh.promise; },
      "PUT /api/v1/jobs/decisions/actionable": () => json({ ...decision("actionable", "dismissed"), revision: 1 }),
      "POST /api/v1/jobs/discovery-runs": (_url, init) => { submitted = JSON.parse(String(init?.body)); return json({ ...run("submitted"), jobs: [] }); },
    });
    renderJobs(fetch, "/jobs/find"); await screen.findByRole("heading", { name: "Find jobs" });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getByRole("checkbox", { name: /^Select Inbox actionable/ }));
    fireEvent.click(screen.getAllByRole("button", { name: /^Dismiss/ })[0]);
    await waitFor(() => expect(screen.queryByText("Inbox actionable")).not.toBeInTheDocument());
    const form = screen.getByRole("heading", { name: "Evaluate selected actionable jobs" }).closest("form")!;
    expect(within(form).getByRole("button", { name: "Evaluate selected jobs" })).toBeDisabled();
    fireEvent.submit(form);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(submitted).toBeUndefined();
    inboxRefresh.resolve(json(page([])));
    await waitFor(() => expect(inboxCalls).toBeGreaterThan(1));
  });

  it("resets Phase 7 list windows and disclosure state across users", async () => {
    const userB: User = { ...user, id: "user-2", email: "second@example.test" };
    const shortlistA = { discovered_job_id: "job-a", title: "User A shortlist", company: "A", location: "London", url: "https://a.test", posted_at: null, work_arrangement: "Hybrid", employment_type: "Full-time", state: "new" as const, verification_status: "verified" as const, verification_reason: null, actionable: true, last_seen_at: "2026-02-02T00:00:00Z", decision: "shortlisted" as const, revision: 1, created_at: "2026-02-01T00:00:00Z", updated_at: "2026-02-02T00:00:00Z" };
    const shortlistB = { ...shortlistA, discovered_job_id: "job-b", title: "User B shortlist" };
    const dismissedB = { ...shortlistB, decision: "dismissed" as const, title: "User B dismissed" };
    const fetch = fakeFetch({
      "/api/v1/users/me": (_url, init) => new Headers(init?.headers).get("Authorization")?.includes("user-b-token") ? json(userB) : json(user),
      "/api/v1/jobs/decisions": (url, init) => { const token = new Headers(init?.headers).get("Authorization"); const kind = url.searchParams.get("decision"); if (token?.includes("user-b-token")) return kind === "shortlisted" ? json(page([shortlistB])) : json(page([dismissedB])); return kind === "shortlisted" ? json(page([shortlistA], true)) : json(page([])); },
    });
    vi.stubGlobal("fetch", fetch); sessionStorage.setItem(TOKEN, "test-token");
    render(<MemoryRouter initialEntries={["/jobs/opportunities/shortlisted"]}><AuthProvider><App /><AuthSwitcher /></AuthProvider></MemoryRouter>);
    await screen.findByText(shortlistA.title);
    fireEvent.click(screen.getByRole("button", { name: "Show more shortlisted jobs" }));
    await waitFor(() => expect(requestPaths(fetch)).toContain("/api/v1/jobs/decisions?decision=shortlisted&limit=40"));
    fireEvent.click(screen.getByRole("button", { name: "Manage dismissed jobs" }));
    fireEvent.click(screen.getByRole("button", { name: "Switch user" }));
    await screen.findByText(shortlistB.title);
    expect(screen.queryByText(shortlistA.title)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hide dismissed jobs" })).not.toBeInTheDocument();
    const userBShortlistCall = fetch.mock.calls.find(([input, init]) => String(input).includes("/api/v1/jobs/decisions?decision=shortlisted&limit=20") && new Headers(init?.headers).get("Authorization")?.includes("user-b-token"));
    expect(userBShortlistCall).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Manage dismissed jobs" }));
    expect(await screen.findByText(dismissedB.title)).toBeInTheDocument();
  });
});

describe("Issue #240 Phase 7 frontend acceptance matrix", () => {
  const workspaceDecisionCases = [
    ["never-decided", "undecided", null, ["Shortlist", "Dismiss"], "Shortlist", "shortlisted"],
    ["persisted-undecided", "undecided", 4, ["Shortlist", "Dismiss"], "Dismiss", "dismissed"],
    ["shortlisted", "shortlisted", 5, ["Shortlisted", "Remove from shortlist", "Dismiss"], "Dismiss", "dismissed"],
    ["dismissed", "dismissed", 6, ["Dismissed", "Undo dismissal", "Shortlist"], "Shortlist", "shortlisted"],
  ] as const;

  it.each(workspaceDecisionCases)("renders %s Workspace controls and preserves exact decision authority", async (_label, value, revision, controls, mutation, returned) => {
    let body: Record<string, unknown> | undefined;
    const fetch = fakeFetch({
      "/api/v1/jobs/workspaces/actionable": () => json(workspacePayload({ ...decision("actionable", value), revision })),
      "PUT /api/v1/jobs/decisions/actionable": (_url, init) => { body = JSON.parse(String(init?.body)); return json({ ...decision("actionable", returned), revision: (revision ?? 0) + 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-02T00:00:00Z" }); },
    });
    renderJobs(fetch, "/jobs/actionable");
    await screen.findByRole("heading", { name: "Workspace role" });
    expect(screen.getByLabelText("Your decision")).toBeInTheDocument();
    for (const control of controls) expect(screen.getByRole(control === "Shortlisted" || control === "Dismissed" ? "generic" : "button", { name: control })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: mutation }));
    await waitFor(() => expect(body).toEqual({ decision: returned, expected_revision: revision }));
  });

  it.each([
    "/jobs/actionable",
    "/jobs/actionable/fit",
    "/jobs/actionable/application",
    "/jobs/actionable/tracking",
  ])("keeps shell-level decision controls on %s", async (path) => {
    const fetch = fakeFetch({ "/api/v1/jobs/workspaces/actionable": () => json(workspacePayload()) });
    renderJobs(fetch, path);
    await screen.findByRole("heading", { name: "Workspace role" });
    expect(screen.getByLabelText("Your decision")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Shortlist/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^Dismiss/ })).toBeEnabled();
  });

  it.each([
    ["Fit unavailable", workspacePayload(decision("actionable"), { current_fit: { status: "unavailable", reason: "candidate_evidence_incomplete", evaluation: null } }), /Current Fit is temporarily unavailable/],
    ["non-actionable", workspacePayload(decision("actionable"), { job: workspaceJob({ actionable: false }), current_fit: { status: "none", reason: "job_not_actionable", evaluation: null } }), /not currently actionable/],
  ] as const)("keeps decision controls usable when the workspace is %s", async (_label, payload, expectedText) => {
    const fetch = fakeFetch({ "/api/v1/jobs/workspaces/actionable": () => json(payload) });
    renderJobs(fetch, "/jobs/actionable/fit");
    await screen.findByRole("heading", { name: "Current Fit" });
    expect(screen.getByText(expectedText)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Shortlist/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^Dismiss/ })).toBeEnabled();
  });

  it("moves a confirmed Shortlisted item to Dismissed through authoritative refetches", async () => {
    const shortlisted = listedDecision("job-transition", "shortlisted", 7, "Transition shortlist");
    const dismissed = { ...shortlisted, title: "Transition dismissed", decision: "dismissed" as const, revision: 8 };
    let shortlistedCalls = 0; let dismissedCalls = 0; let body: Record<string, unknown> | undefined;
    const fetch = fakeFetch({
      "/api/v1/jobs/decisions": (url) => {
        const kind = url.searchParams.get("decision");
        if (kind === "shortlisted") return json(shortlistedCalls++ === 0 ? page([shortlisted]) : page([]));
        return json(dismissedCalls++ === 0 ? page([]) : page([dismissed]));
      },
      "PUT /api/v1/jobs/decisions/job-transition": (_url, init) => { body = JSON.parse(String(init?.body)); return json(dismissed); },
    });
    renderJobs(fetch, "/jobs/opportunities/shortlisted");
    await screen.findByText(shortlisted.title);
    fireEvent.click(screen.getByRole("button", { name: "Manage dismissed jobs" }));
    await screen.findByText("No dismissed jobs.");
    fireEvent.click(screen.getByRole("button", { name: /^Dismiss/ }));
    await waitFor(() => expect(screen.queryByText(shortlisted.title)).not.toBeInTheDocument());
    expect(body).toEqual({ decision: "dismissed", expected_revision: 7 });
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/decisions?decision=shortlisted&limit=20");
    await waitFor(() => expect(dismissedCalls).toBeGreaterThan(1));
    expect(await screen.findByText(dismissed.title)).toBeInTheDocument();
  });

  it("moves a confirmed Dismissed item to Shortlisted through authoritative refetches", async () => {
    const anchor = listedDecision("job-anchor", "shortlisted", 2, "Existing shortlist");
    const dismissed = listedDecision("job-recover", "dismissed", 9, "Recoverable dismissal");
    const promoted = { ...dismissed, title: "Recovered shortlist", decision: "shortlisted" as const, revision: 10 };
    let shortlistedCalls = 0; let dismissedCalls = 0; let body: Record<string, unknown> | undefined;
    const fetch = fakeFetch({
      "/api/v1/jobs/decisions": (url) => {
        const kind = url.searchParams.get("decision");
        if (kind === "shortlisted") return json(shortlistedCalls++ === 0 ? page([anchor]) : page([anchor, promoted]));
        return json(dismissedCalls++ === 0 ? page([dismissed]) : page([]));
      },
      "PUT /api/v1/jobs/decisions/job-recover": (_url, init) => { body = JSON.parse(String(init?.body)); return json(promoted); },
    });
    renderJobs(fetch, "/jobs/opportunities/shortlisted");
    await screen.findByText(anchor.title);
    fireEvent.click(screen.getByRole("button", { name: "Manage dismissed jobs" }));
    await screen.findByText(dismissed.title);
    fireEvent.click(screen.getByRole("button", { name: /^Shortlist/ }));
    await waitFor(() => expect(screen.queryByText(dismissed.title)).not.toBeInTheDocument());
    expect(body).toEqual({ decision: "shortlisted", expected_revision: 9 });
    await waitFor(() => expect(shortlistedCalls).toBeGreaterThan(1));
    expect(await screen.findByText(promoted.title)).toBeInTheDocument();
    expect(dismissedCalls).toBeGreaterThan(1);
  });

  it("grows Shortlisted deterministically from 20 to 100 and states max-window truncation truthfully", async () => {
    const fetch = fakeFetch({
      "/api/v1/jobs/decisions": (url) => url.searchParams.get("decision") === "shortlisted" ? json(page([listedDecision(`short-${url.searchParams.get("limit")}`, "shortlisted", 1, `Shortlisted ${url.searchParams.get("limit")}`)], true)) : json(page([])),
    });
    renderJobs(fetch, "/jobs/opportunities/shortlisted");
    await screen.findByText("Shortlisted 20");
    for (const limit of [40, 60, 80, 100]) {
      fireEvent.click(screen.getByRole("button", { name: "Show more shortlisted jobs" }));
      await waitFor(() => expect(requestPaths(fetch)).toContain(`/api/v1/jobs/decisions?decision=shortlisted&limit=${limit}`));
    }
    expect(screen.getByText("Showing the first 100 shortlisted jobs; more matching decisions exist.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more shortlisted jobs" })).not.toBeInTheDocument();
  });

  it("grows Dismissed independently from 20 to 100 and states max-window truncation truthfully", async () => {
    const anchor = listedDecision("job-anchor", "shortlisted", 2, "Shortlisted anchor");
    const fetch = fakeFetch({
      "/api/v1/jobs/decisions": (url) => url.searchParams.get("decision") === "shortlisted" ? json(page([anchor])) : json(page([listedDecision(`dismissed-${url.searchParams.get("limit")}`, "dismissed", 1, `Dismissed ${url.searchParams.get("limit")}`)], true)),
    });
    renderJobs(fetch, "/jobs/opportunities/shortlisted");
    await screen.findByText(anchor.title);
    fireEvent.click(screen.getByRole("button", { name: "Manage dismissed jobs" }));
    await screen.findByText("Dismissed 20");
    for (const limit of [40, 60, 80, 100]) {
      fireEvent.click(screen.getByRole("button", { name: "Show more dismissed jobs" }));
      await waitFor(() => expect(requestPaths(fetch)).toContain(`/api/v1/jobs/decisions?decision=dismissed&limit=${limit}`));
    }
    expect(screen.getByText("Showing the first 100 dismissed jobs; more matching decisions exist.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more dismissed jobs" })).not.toBeInTheDocument();
  });

  it("keeps an Inbox row visible when Shortlist is confirmed", async () => {
    let body: Record<string, unknown> | undefined;
    const fetch = fakeFetch({
      "PUT /api/v1/jobs/decisions/actionable": (_url, init) => { body = JSON.parse(String(init?.body)); return json({ ...decision("actionable", "shortlisted"), revision: 4 }); },
    });
    renderJobs(fetch, "/jobs/inbox");
    await screen.findByText("Inbox actionable");
    fireEvent.click(screen.getAllByRole("button", { name: /^Shortlist/ })[0]);
    await waitFor(() => expect(screen.getByLabelText("Shortlisted")).toBeInTheDocument());
    expect(body).toEqual({ decision: "shortlisted", expected_revision: null });
    expect(screen.getByText("Inbox actionable")).toBeInTheDocument();
  });

  it("keeps Recommended ordering and analysis fields unchanged when Shortlist is confirmed", async () => {
    let opportunityCalls = 0; let body: Record<string, unknown> | undefined;
    const fetch = fakeFetch({
      "/api/v1/jobs/opportunities": () => { opportunityCalls += 1; return json(page([op("alpha", "Alpha", 1), op("beta", "Beta", 2)])); },
      "PUT /api/v1/jobs/decisions/job-alpha": (_url, init) => { body = JSON.parse(String(init?.body)); return json({ ...decision("job-alpha", "shortlisted"), revision: 3 }); },
    });
    renderJobs(fetch, "/jobs/opportunities/recommended");
    await screen.findByText("Alpha");
    const alpha = screen.getByText("Alpha").closest("li")!;
    fireEvent.click(within(alpha).getByRole("button", { name: /^Shortlist/ }));
    await waitFor(() => expect(within(alpha).getByLabelText("Shortlisted")).toBeInTheDocument());
    expect(body).toEqual({ decision: "shortlisted", expected_revision: null });
    expect(screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(["Alpha", "Beta"]);
    expect(within(alpha).getByText("72")).toBeInTheDocument();
    expect(within(alpha).getByText(/84/)).toBeInTheDocument();
  });

  it("does not remove Recommended on an unconfirmed Dismiss, then removes only after confirmed authority", async () => {
    let opportunityCalls = 0; let puts = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/opportunities": () => { opportunityCalls += 1; return opportunityCalls === 1 ? json(page([op("alpha", "Alpha", 1)])) : json(page([])); },
      "PUT /api/v1/jobs/decisions/job-alpha": () => { puts += 1; return puts === 1 ? json({ detail: "offline" }, 503) : json({ ...decision("job-alpha", "dismissed"), revision: 1 }); },
      "GET /api/v1/jobs/decisions/job-alpha": () => json(decision("job-alpha")),
    });
    renderJobs(fetch, "/jobs/opportunities/recommended");
    const alpha = await screen.findByText("Alpha");
    fireEvent.click(within(alpha.closest("li")!).getByRole("button", { name: /^Dismiss/ }));
    await screen.findByText(/update was interrupted/);
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(opportunityCalls).toBe(1);
    fireEvent.click(within(screen.getByText("Alpha").closest("li")!).getByRole("button", { name: /^Dismiss/ }));
    await waitFor(() => expect(screen.queryByText("Alpha")).not.toBeInTheDocument());
    expect(puts).toBe(2);
    expect(opportunityCalls).toBeGreaterThan(1);
  });

  it("uses reconciled authority on Recommended without replaying the original mutation", async () => {
    let puts = 0; let opportunityCalls = 0;
    const reconciledOpportunity = { ...op("alpha", "Alpha", 1), decision: { ...decision("job-alpha", "shortlisted"), revision: 2 } };
    const fetch = fakeFetch({
      "/api/v1/jobs/opportunities": () => { opportunityCalls += 1; return json(page([opportunityCalls === 1 ? op("alpha", "Alpha", 1) : reconciledOpportunity])); },
      "PUT /api/v1/jobs/decisions/job-alpha": () => { puts += 1; return json({ detail: "changed elsewhere" }, 409); },
      "GET /api/v1/jobs/decisions/job-alpha": () => json({ ...decision("job-alpha", "shortlisted"), revision: 2 }),
    });
    renderJobs(fetch, "/jobs/opportunities/recommended");
    const alpha = await screen.findByText("Alpha");
    fireEvent.click(within(alpha.closest("li")!).getByRole("button", { name: /^Dismiss/ }));
    await waitFor(() => expect(within(screen.getByText("Alpha").closest("li")!).getByLabelText("Shortlisted")).toBeInTheDocument());
    expect(puts).toBe(1);
    expect(opportunityCalls).toBe(1);
    expect(screen.getByText(/decision changed elsewhere/)).toBeInTheDocument();
  });

  it("reloads Dismissed management from durable authority on a direct Shortlisted route", async () => {
    const anchor = listedDecision("job-anchor", "shortlisted", 2, "Shortlisted anchor");
    const dismissed = listedDecision("job-reload", "dismissed", 5, "Reloaded dismissal");
    const fetch = fakeFetch({
      "/api/v1/jobs/decisions": (url) => url.searchParams.get("decision") === "shortlisted" ? json(page([anchor])) : json(page([dismissed])),
    });
    renderJobs(fetch, "/jobs/opportunities/shortlisted");
    await screen.findByText(anchor.title);
    fireEvent.click(screen.getByRole("button", { name: "Manage dismissed jobs" }));
    expect(await screen.findByText(dismissed.title)).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/decisions?decision=dismissed&limit=20");
  });

  it("renders Workspace preparation history and reuses the canonical preparation endpoint", async () => {
    const application = { preparation_id: "prep-1", created_at: "2026-03-01T12:00:00Z", target: { source_kind: "discovered_job", canonical_discovered_job_id: "actionable", title: "Saved workspace role", company: "Public Co", location: "London", public_url: "https://public.example.test/actionable", work_arrangement: "Hybrid", employment_type: "Full-time", job_content_hash: "hash" }, snapshot_status: "current_job_content", result_summary: { layout_status: "fit", target_pages: 2, actual_pdf_pages: 2, has_cover_letter: true, answer_count: 1 }, tracking: null };
    let posts = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/workspaces/actionable": (_url, init) => init?.method === "POST" ? json({ detail: "unexpected" }, 405) : json(workspacePayload(decision("actionable"), { applications: { items: [application], limit: 20, truncated: false } })),
      "POST /api/v1/applications/prepare": (_url, init) => { posts += 1; expect(JSON.parse(String(init?.body))).toMatchObject({ target: { discovered_job_id: "actionable" }, target_pages: 2, include_cover_letter: true }); return json({ id: "prep-created" }, 201); },
    });
    renderJobs(fetch, "/jobs/actionable/application");
    expect(await screen.findByText("Saved workspace role")).toBeInTheDocument();
    expect(screen.getByText("Current job content")).toBeInTheDocument();
    expect(screen.queryByText(/not part of this Job Workspace yet/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open preparation for/ })).toHaveAttribute("href", "/applications/prep-1");
    fireEvent.click(await screen.findByRole("button", { name: "Create preparation" }));
    await waitFor(() => expect(posts).toBe(1));
    await waitFor(() => expect(screen.getAllByRole("link", { name: /Open preparation/ }).some((link) => link.getAttribute("href") === "/applications/prep-created")).toBe(true));
  });

  it("uses shared profile-missing authority on direct Workspace Application load", async () => {
    const fetch = fakeFetch({
      "/api/v1/onboarding/status": () => json({ ...ready, candidate_context_ready: true }),
      "/api/v1/profile": () => json(undefined, 404),
      "/api/v1/jobs/workspaces/actionable": () => json(workspacePayload(decision("actionable"), { applications: { items: [workspacePreparation()], limit: 20, truncated: false } })),
    });
    renderJobs(fetch, "/jobs/actionable/application");
    expect(await screen.findByText("Saved workspace preparation")).toBeInTheDocument();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("application display name is required");
    expect(within(alert).getByRole("link", { name: "Update your profile" })).toHaveAttribute("href", "/profile");
    expect(alert).not.toHaveTextContent("Preparation readiness is unavailable");
    expect(screen.queryByRole("button", { name: "Create preparation" })).not.toBeInTheDocument();
  });

  it("preserves candidate-not-ready precedence when the direct Workspace profile read fails", async () => {
    const fetch = fakeFetch({
      "/api/v1/onboarding/status": () => json({ ...ready, candidate_context_ready: false }),
      "/api/v1/profile": () => Promise.reject(new TypeError("offline")),
      "/api/v1/jobs/workspaces/actionable": () => json(workspacePayload(decision("actionable"), { applications: { items: [workspacePreparation()], limit: 20, truncated: false } })),
    });
    renderJobs(fetch, "/jobs/actionable/application");
    expect(await screen.findByText("Saved workspace preparation")).toBeInTheDocument();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("confirmed candidate CV is required");
    expect(within(alert).getByRole("link", { name: "Continue CV onboarding" })).toHaveAttribute("href", "/profile/cv");
    expect(alert).not.toHaveTextContent("Preparation readiness is unavailable");
    expect(screen.queryByRole("button", { name: "Create preparation" })).not.toBeInTheDocument();
  });

  it("shows truthful unavailable Workspace readiness with a retry action", async () => {
    const fetch = fakeFetch({
      "/api/v1/onboarding/status": () => Promise.reject(new TypeError("offline")),
      "/api/v1/jobs/workspaces/actionable": () => json(workspacePayload(decision("actionable"), { applications: { items: [workspacePreparation()], limit: 20, truncated: false } })),
    });
    renderJobs(fetch, "/jobs/actionable/application");
    expect(await screen.findByText("Saved workspace preparation")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Preparation readiness is unavailable");
    expect(screen.getByRole("button", { name: "Retry readiness" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create preparation" })).not.toBeInTheDocument();
  });

  it("transitions Workspace readiness from unavailable to ready without navigation", async () => {
    let statusCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/onboarding/status": () => ++statusCalls === 1 ? Promise.reject(new TypeError("offline")) : json(ready),
      "/api/v1/jobs/workspaces/actionable": () => json(workspacePayload(decision("actionable"), { applications: { items: [workspacePreparation()], limit: 20, truncated: false } })),
    });
    renderJobs(fetch, "/jobs/actionable/application");
    fireEvent.click(await screen.findByRole("button", { name: "Retry readiness" }));
    expect(await screen.findByRole("button", { name: "Create preparation" })).toBeInTheDocument();
    expect(statusCalls).toBe(2);
  });

  it("does not let a stale Workspace prerequisite read update a replacement user", async () => {
    const oldStatus = deferred<Response>();
    let statusCalls = 0; let meCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/users/me": () => json(meCalls++ === 0 ? user : { ...user, id: "user-b", email: "replacement@example.test" }),
      "/api/v1/onboarding/status": () => ++statusCalls === 1 ? oldStatus.promise : json(ready),
      "/api/v1/jobs/workspaces/actionable": () => json(workspacePayload(decision("actionable"), { applications: { items: [workspacePreparation()], limit: 20, truncated: false } })),
    });
    sessionStorage.setItem(TOKEN, "test-token");
    vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/jobs/actionable/application"]}><AuthProvider><AuthSwitcher /><App /></AuthProvider></MemoryRouter>);
    expect(await screen.findByText("Saved workspace preparation")).toBeInTheDocument();
    await waitFor(() => expect(statusCalls).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: "Switch user" }));
    expect(await screen.findByRole("button", { name: "Create preparation" })).toBeInTheDocument();
    oldStatus.resolve(json({ ...ready, candidate_context_ready: false }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Create preparation" })).toBeInTheDocument());
    expect(meCalls).toBeGreaterThan(1);
  });

  it("uses the same profile-missing presentation for initial reads and 409 reconciliation", async () => {
    let mode: "initial" | "conflict" = "initial"; let profileCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/onboarding/status": () => json(ready),
      "/api/v1/profile": () => mode === "initial" ? json(undefined, 404) : profileCalls++ === 0 ? json({ display_name: "Current Person" }) : json(undefined, 404),
      "/api/v1/jobs/workspaces/actionable": () => json(workspacePayload(decision("actionable"), { applications: { items: [workspacePreparation()], limit: 20, truncated: false } })),
      "POST /api/v1/applications/prepare": () => json({ detail: "profile changed" }, 409),
    });
    renderJobs(fetch, "/jobs/actionable/application");
    const initialAlert = await screen.findByRole("alert");
    expect(initialAlert).toHaveTextContent("application display name is required");
    expect(within(initialAlert).getByRole("link", { name: "Update your profile" })).toHaveAttribute("href", "/profile");
    cleanup(); sessionStorage.clear(); mode = "conflict"; profileCalls = 0;
    renderJobs(fetch, "/jobs/actionable/application");
    fireEvent.click(await screen.findByRole("button", { name: "Create preparation" }));
    const reconciledAlert = await screen.findByRole("alert");
    expect(reconciledAlert).toHaveTextContent("application display name is required");
    expect(within(reconciledAlert).getByRole("link", { name: "Update your profile" })).toHaveAttribute("href", "/profile");
  });

  it("renders independent Workspace tracking states and synchronously locks duplicate starts", async () => {
    const base = (id: string, tracking: unknown) => ({ preparation_id: id, created_at: "2026-03-01T12:00:00Z", target: { source_kind: "discovered_job", canonical_discovered_job_id: "actionable", title: id, company: "Public Co", location: "London", public_url: null, work_arrangement: null, employment_type: null, job_content_hash: "hash" }, snapshot_status: "current_job_content", result_summary: null, tracking });
    let workspaceCalls = 0; let posts = 0; const pending = deferred<Response>();
    const fetch = fakeFetch({
      "/api/v1/jobs/workspaces/actionable": () => { workspaceCalls += 1; return json(workspacePayload(decision("actionable"), { applications: { items: [base("prep-a", null), base("prep-b", { id: "track-b", preparation_id: "prep-b", current_status: "interview", revision: 2, created_at: "2026-03-01T12:00:00Z", updated_at: "2026-03-02T12:00:00Z" })], limit: 20, truncated: false } })); },
      "POST /api/v1/application-tracking": () => { posts += 1; return pending.promise; },
    });
    renderJobs(fetch, "/jobs/actionable/tracking");
    expect(await screen.findByText("Not tracked")).toBeInTheDocument();
    expect(screen.getByText("interview", { exact: false })).toBeInTheDocument();
    const start = screen.getByRole("button", { name: /Start tracking for/ });
    expect(start).toHaveAccessibleName(/Start tracking for/);
    fireEvent.click(start); fireEvent.click(start);
    await waitFor(() => expect(posts).toBe(1));
    const pendingStart = screen.getByRole("button", { name: /Starting tracking… for/ });
    expect(pendingStart).toHaveTextContent("Starting tracking…");
    expect(pendingStart).toBeDisabled();
    pending.resolve(json({ id: "track-a", preparation_id: "prep-a" }, 201));
    await waitFor(() => expect(workspaceCalls).toBeGreaterThan(1));
    expect(screen.getByText(/workspace projection is refreshing/i)).toBeInTheDocument();
  });

  it("reports Workspace tracking conflict only after authoritative reconciliation succeeds", async () => {
    const base = (tracking: unknown) => ({ preparation_id: "prep-a", created_at: "2026-03-01T12:00:00Z", target: { source_kind: "discovered_job", canonical_discovered_job_id: "actionable", title: "prep-a", company: "Public Co", location: "London", public_url: null, work_arrangement: null, employment_type: null, job_content_hash: "hash" }, snapshot_status: "current_job_content", result_summary: null, tracking });
    let workspaceCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/workspaces/actionable": () => json(workspacePayload(decision("actionable"), { applications: { items: [base(workspaceCalls++ === 0 ? null : { id: "track-a", preparation_id: "prep-a", current_status: "interview", revision: 2, created_at: "2026-03-01T12:00:00Z", updated_at: "2026-03-02T12:00:00Z" })], limit: 20, truncated: false } })),
      "/api/v1/application-tracking/by-preparation/prep-a": () => json({ id: "track-a", preparation_id: "prep-a", current_status: "interview", revision: 2, created_at: "2026-03-01T12:00:00Z", updated_at: "2026-03-02T12:00:00Z" }),
      "POST /api/v1/application-tracking": () => json({ detail: "already exists" }, 409),
    });
    renderJobs(fetch, "/jobs/actionable/tracking");
    fireEvent.click(await screen.findByRole("button", { name: /Start tracking for/ }));
    expect(await screen.findByText(/Saved tracking was confirmed/)).toBeInTheDocument();
    expect(await screen.findByText(/interview/i)).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/application-tracking/by-preparation/prep-a");
    expect(workspaceCalls).toBeGreaterThan(1);
  });

  it("does not claim Workspace tracking state when exact reconciliation fails even if the bounded refresh succeeds", async () => {
    let workspaceCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/workspaces/actionable": () => workspaceCalls++ === 0 ? json(workspacePayload(decision("actionable"), { applications: { items: [{ preparation_id: "prep-a", created_at: "2026-03-01T12:00:00Z", target: { source_kind: "discovered_job", canonical_discovered_job_id: "actionable", title: "prep-a", company: "Public Co", location: "London", public_url: null, work_arrangement: null, employment_type: null, job_content_hash: "hash" }, snapshot_status: "current_job_content", result_summary: null, tracking: null }], limit: 20, truncated: false } })) : json(workspacePayload(decision("actionable"), { applications: { items: [], limit: 20, truncated: false } })),
      "/api/v1/application-tracking/by-preparation/prep-a": () => json({ detail: "not found" }, 404),
      "POST /api/v1/application-tracking": () => json({ detail: "already exists" }, 409),
    });
    renderJobs(fetch, "/jobs/actionable/tracking");
    fireEvent.click(await screen.findByRole("button", { name: /Start tracking for/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not confirm saved tracking for this preparation/);
    expect(screen.queryByText(/Saved tracking was confirmed/)).not.toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/application-tracking/by-preparation/prep-a");
  });

  it("keeps Tracking show-more on the shared bounded application window", async () => {
    const item = (id: string) => ({ preparation_id: id, created_at: "2026-03-01T12:00:00Z", target: { source_kind: "discovered_job", canonical_discovered_job_id: "actionable", title: id, company: "Public Co", location: "London", public_url: null, work_arrangement: null, employment_type: null, job_content_hash: "hash" }, snapshot_status: "current_job_content", result_summary: null, tracking: null });
    const fetch = fakeFetch({
      "/api/v1/jobs/workspaces/actionable": (url) => url.searchParams.get("application_limit") === "40"
        ? json(workspacePayload(decision("actionable"), { applications: { items: [item("prep-40")], limit: 40, truncated: false } }))
        : json(workspacePayload(decision("actionable"), { applications: { items: [item("prep-20")], limit: 20, truncated: true } })),
    });
    renderJobs(fetch, "/jobs/actionable/tracking");
    expect(await screen.findByText("prep-20")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByText("prep-40")).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/workspaces/actionable?application_limit=40");
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
  });

  it("refreshes full Workspace authority after preparation target unavailability", async () => {
    let workspaceCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/workspaces/actionable": () => {
        workspaceCalls += 1;
        return json(workspacePayload(decision("actionable"), { job: workspaceJob({ actionable: workspaceCalls === 1 }) }));
      },
      "POST /api/v1/applications/prepare": () => json({ detail: "gone" }, 404),
    });
    renderJobs(fetch, "/jobs/actionable/application");
    await screen.findByRole("button", { name: "Create preparation" });
    fireEvent.click(screen.getByRole("button", { name: "Create preparation" }));
    expect(await screen.findByText("This vacancy is no longer actionable. Saved preparations remain visible, but new preparation creation is disabled.")).toBeInTheDocument();
    expect(workspaceCalls).toBeGreaterThan(1);
    expect(screen.queryByRole("button", { name: "Create preparation" })).not.toBeInTheDocument();
  });

  it("keeps a newer 40-item application window and decision while stale target authority updates job and Fit", async () => {
    const staleRefresh = deferred<Response>();
    const application = (id: string) => ({ preparation_id: id, created_at: "2026-03-01T12:00:00Z", target: { source_kind: "discovered_job", canonical_discovered_job_id: "actionable", title: id, company: "Public Co", location: "London", public_url: null, work_arrangement: null, employment_type: null, job_content_hash: "hash" }, snapshot_status: "current_job_content", result_summary: null, tracking: null });
    const initialHistory = { id: "eval-history", created_at: "2026-02-01T00:00:00Z", applicability: "historical", opportunity: ranked("Historical workspace role"), runtime_attribution: null };
    const initialProvenance = { id: "prov-1", runtime: "codex", source_ref: "workspace-source", discovered_via: "external_import", imported_at: "2026-02-01T00:00:00Z" };
    let workspaceCalls = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/workspaces/actionable": (url) => {
        if (url.searchParams.get("application_limit") === "40") return json(workspacePayload(decision("actionable"), { applications: { items: [application("prep-40")], limit: 40, truncated: false } }));
        workspaceCalls += 1;
        if (workspaceCalls === 1) return json(workspacePayload(decision("actionable"), {
          current_fit: { status: "current", reason: null, evaluation: { id: "eval-current", created_at: "2026-03-01T00:00:00Z", applicability: "current", opportunity: ranked("Current workspace role"), runtime_attribution: null } },
          evaluations: { items: [initialHistory], limit: 20, truncated: false },
          provenance: { items: [initialProvenance], count: 1, limit: 20, truncated: false },
          applications: { items: [application("prep-20")], limit: 20, truncated: true },
        }));
        return staleRefresh.promise;
      },
      "POST /api/v1/applications/prepare": () => json({ detail: "gone" }, 404),
      "PUT /api/v1/jobs/decisions/actionable": () => json({ ...decision("actionable", "shortlisted"), revision: 1 }),
    });
    renderJobs(fetch, "/jobs/actionable/application");
    await screen.findByRole("button", { name: "Create preparation" });
    expect(await screen.findByText("prep-20")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create preparation" }));
    await waitFor(() => expect(workspaceCalls).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: /^Shortlist/ }));
    expect(await screen.findByLabelText("Shortlisted")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByText("prep-40")).toBeInTheDocument();
    staleRefresh.resolve(json(workspacePayload(decision("actionable", "dismissed"), {
      job: workspaceJob({ actionable: false, title: "No longer actionable role" }),
      current_fit: { status: "none", reason: "job_not_actionable", evaluation: null },
      applications: { items: [application("prep-20")], limit: 20, truncated: true },
    })));
    expect(await screen.findByText("This vacancy is no longer actionable. Saved preparations remain visible, but new preparation creation is disabled.")).toBeInTheDocument();
    expect(screen.getByText("prep-40")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Shortlisted")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Fit" }));
    expect(await screen.findByText("This vacancy is not currently actionable, so no current Fit is claimed.")).toBeInTheDocument();
    expect(screen.getByText("Historical workspace role")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Overview" }));
    expect(await screen.findByText("workspace-source")).toBeInTheDocument();
  });
});

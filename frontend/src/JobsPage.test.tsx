import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation, useNavigationType } from "react-router-dom";
import type { ApplicationPreparation, DiscoveryRunDetail, DiscoveryRunSummary, DiscoveryScheduleRead, HistoricalRunJobDetail, InboxSummary, OnboardingStatus, RankedJobOpportunity, ScheduledExecutionRead, User, UserOpportunitySummary } from "./api";
import { App } from "./App";
import { AuthProvider } from "./auth";

const TOKEN = "career-trans.access-token";
const user: User = { id: "user-1", email: "jobs@example.test", created_at: "2026-01-01T00:00:00Z" };
const ready: OnboardingStatus = { profile_exists: true, candidate_context_ready: true, latest_cv_draft: { id: "cv-1", state: "confirmed", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 0 } };
const op = (id: string, title = id, rank = 999): UserOpportunitySummary => ({ evaluation_id: `eval-${id}`, discovered_job_id: `job-${id}`, recommendation: "consider", title, company: "Example Co", location: "London", work_arrangement: "Hybrid", fit_score: 72, career_alignment_score: 84, career_alignment_confidence: "medium", relevance_score: 0.91, archetype: "ai_forward_deployed", url: `https://jobs.example.test/${id}`, posting_recency: { legitimacy: "unknown", reasoning: "The listing date is not independently verified." }, ...( { rank } as object) });
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
const run = (id = "run-1", status: DiscoveryRunSummary["status"] = "completed"): DiscoveryRunSummary => ({ id, status, run_input: { query: { keywords: ["AI Engineer"], locations: ["London"], remote_ok: false } }, funnel: { submitted: 3, reused: 1, relevance_screened: 2, full_analysis_attempts: 1, analysed: 1 }, failure_summary: { provider_failure: 1 }, started_at: "2026-02-01T12:00:00Z", completed_at: status === "running" ? null : "2026-02-01T12:05:00Z" });
const savedSchedule = (patch: Partial<DiscoveryScheduleRead> = {}): DiscoveryScheduleRead => ({
  id: "s-1", name: "AI roles", enabled: false,
  schedule: { cadence: "daily", timezone: "UTC", local_time: "09:00:00", weekdays: [] },
  query: { keywords: ["AI"], locations: ["London"], remote_ok: false, companies: ["Compatibility Co"], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 73 },
  acquisition: { structured_ats: { enabled: true, companies: [], providers: [], all_resolved_sources: true, max_sources: 20, max_results: 100 }, agentic_web: { enabled: false, country: "gb", max_search_queries: 6, max_search_results_per_query: 10, max_pages_to_open: 12, max_discovered_jobs: 20 } },
  evaluation: { max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 }, next_run_at: null, last_execution_at: null, ...patch,
});
const savedExecution = (status: ScheduledExecutionRead["status"] = "completed"): ScheduledExecutionRead => ({
  id: "execution-1", trigger_kind: "manual", scheduled_for: null, status,
  config_snapshot: { schedule: savedSchedule().schedule, query: savedSchedule().query, acquisition: savedSchedule().acquisition, evaluation: savedSchedule().evaluation },
  discovery_run_id: status === "running" ? null : "run-1", acquisition_summary: {}, failure_summary: {}, started_at: "2026-02-01T12:00:00Z", completed_at: status === "running" ? null : "2026-02-01T12:05:00Z",
});
const runDetail = (status: DiscoveryRunSummary["status"] = "completed"): DiscoveryRunDetail => ({ ...run("run-1", status), jobs: (["newly_evaluated", "reused_evaluation", "not_actionable", "presemantic_filtered", "outside_semantic_budget", "semantic_rejected", "outside_deep_analysis_budget", "analysis_failed"] as const).map((outcome, i) => ({ discovered_job_id: `job-${i}`, evaluation_id: null, outcome, failure_stage: outcome === "analysis_failed" ? "career_analysis" : null, failure_kind: null, opportunity: null })) });
const inboxItem = (id: string, actionable = true): InboxSummary => ({ discovered_job_id: id, title: `Inbox ${id}`, company: "Public Co", location: "London", work_arrangement: "Hybrid", employment_type: "Full-time", url: `https://public.example.test/${id}`, state: "new", verification_status: actionable ? "verified" : "unverified", verification_reason: actionable ? null : "provider_detail_unavailable", actionable, first_seen_at: "2026-02-01T00:00:00Z", last_seen_at: "2026-02-02T00:00:00Z", provenance: [{ runtime: "codex", source_ref: "not-rendered", discovered_via: "external_import", imported_at: "2026-02-02T00:00:00Z" }], provenance_count: 1 });
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
    if (path === "/api/v1/users/me") return Promise.resolve(json(user));
    if (path === "/api/v1/onboarding/status") return Promise.resolve(json(ready));
    if (path === "/api/v1/profile") return Promise.resolve(json({ id: "profile", user_id: user.id, display_name: "Current Person", created_at: "", updated_at: "" }));
    if (path === "/api/v1/profile/snapshot") return Promise.resolve(json(profileSnapshot()));
    if (path === "/api/v1/profile/revisions/active") return Promise.resolve(json(null));
    if (path === "/api/v1/jobs/opportunities") return Promise.resolve(json(page([op("alpha", "Alpha", 99), op("beta", "Beta", 1)])));
    if (path === "/api/v1/jobs/discovery-runs") return Promise.resolve(json(page([run()])));
    if (path === "/api/v1/jobs/inbox") return Promise.resolve(json(page([inboxItem("actionable"), inboxItem("blocked", false)])));
    if (path === "/api/v1/jobs/discovery-schedules") return Promise.resolve(json([]));
    if (path === "/api/v1/jobs/opportunities/eval-alpha") return Promise.resolve(json(ranked("Alpha detail")));
    if (path === "/api/v1/jobs/discovery-runs/run-1") return Promise.resolve(json(runDetail()));
    if (path === "/api/v1/jobs/discovery-runs/run-1/jobs/job-0") return Promise.resolve(json(historyDetail));
    if (path === "/api/v1/jobs/discovery-runs" && init?.method === "POST") return Promise.resolve(json({ ...run("run-new"), jobs: [] }));
    throw new Error(`Unexpected request ${init?.method ?? "GET"} ${path}${url.search}`);
  });
}
function renderJobs(fetch = fakeFetch(), path = "/jobs/find") {
  sessionStorage.setItem(TOKEN, "test-token");
  vi.stubGlobal("fetch", fetch);
  return { ...render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>), fetch };
}
function RouteLocation() { const location = useLocation(); const action = useNavigationType(); return <output aria-label="Route location">{location.pathname}:{action}</output>; }
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

  it("prepares from the canonical discovered job ID, cleans questions, and does not gate on SKIP", async () => {
    let body: unknown;
    const alpha = { ...op("alpha", "Alpha", 99), recommendation: "apply" as const };
    const beta = { ...op("beta", "Beta", 1), recommendation: "skip" as const };
    const created = { id: "prep-alpha" } as ApplicationPreparation;
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([alpha, beta, op("gamma", "Gamma", 2)])), "POST /api/v1/applications/prepare": (_url, init) => { body = JSON.parse(String(init?.body)); return json(created, 201); } });
    renderJobs(fetch); await loaded();
    const prepareButtons = screen.getAllByRole("button", { name: "Prepare application" });
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
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    expect(await within(form).findByRole("alert")).toHaveTextContent("application display name is required");
    expect(within(form).getByRole("link", { name: "Update your profile" })).toHaveAttribute("href", "/profile");
    expect(within(form).queryByRole("button", { name: "Create preparation" })).not.toBeInTheDocument();
    expect(posts).toBe(0);
  });

  it("reports an unavailable target truthfully when the shortlist refresh fails", async () => {
    let opportunityCalls = 0; let posts = 0;
    const fetch = fakeFetch({ "/api/v1/profile": () => json({ display_name: "Current Person" }), "/api/v1/jobs/opportunities": () => { opportunityCalls += 1; return opportunityCalls === 1 ? json(page([op("alpha", "Alpha")] )) : Promise.reject(new TypeError("offline")); }, "POST /api/v1/applications/prepare": () => { posts += 1; return json({ detail: "not found" }, 404); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Prepare application" }));
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("opportunity refresh could not be confirmed");
    expect(within(form).queryByText(/created/)).not.toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Create preparation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Close preparation options" })).toBeEnabled();
    fireEvent.submit(form);
    expect(posts).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Close preparation options" }));
    expect(screen.queryByRole("form", { name: "Prepare application for Alpha" })).not.toBeInTheDocument();
    expect(opportunityCalls).toBe(2);
  });

  it.each([[422, "did not contain sufficient usable information"], [503, "temporarily unavailable"]] as const)("handles HTTP %s without raw server detail", async (status, message) => {
    const fetch = fakeFetch({ "/api/v1/profile": () => json({ display_name: "Current Person" }), "POST /api/v1/applications/prepare": () => json({ detail: "private provider diagnostic" }, status) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[0]);
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
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("confirmed candidate CV is required");
    expect(within(form).getByRole("link", { name: "Continue CV onboarding" })).toHaveAttribute("href", "/profile/cv");
    expect(screen.getByRole("button", { name: "Close preparation options" })).toBeEnabled();
    expect(within(form).getByRole("button", { name: "Create preparation" })).toBeDisabled();
    fireEvent.submit(form);
    expect(posts).toBe(1);
  });

  it("routes a refreshed missing-display-name 409 to Profile", async () => {
    let profileCalls = 0; let posts = 0;
    const fetch = fakeFetch({ "/api/v1/profile": () => json(++profileCalls === 1 ? { display_name: "Current Person" } : { display_name: "   " }), "POST /api/v1/applications/prepare": () => { posts += 1; return json({ detail: "profile invalid" }, 409); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[0]);
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
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[0]);
    const form = await screen.findByRole("form", { name: "Prepare application for Alpha" });
    await within(form).findByRole("button", { name: "Create preparation" });
    fireEvent.click(within(form).getByRole("button", { name: "Create preparation" }));
    expect(await within(form).findByText("Career-trans could not confirm the current preparation prerequisites. Review your CV and profile, then try again.")).toBeInTheDocument();
    const create = within(form).queryByRole("button", { name: "Create preparation" });
    if (create) expect(create).toBeDisabled();
    expect(screen.getByRole("button", { name: "Close preparation options" })).toBeEnabled();
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
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[0]);
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
    const panels = screen.getAllByRole("button", { name: "Prepare application" });
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
    const buttons = screen.getAllByRole("button", { name: "Prepare application" });
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
    const buttons = screen.getAllByRole("button", { name: "Prepare application" });
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
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[0]);
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
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: "Prepare application" })[0]);
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
    await growToLimit(fetch, "/api/v1/jobs/discovery-runs", "Show more runs");
    expect(screen.queryByRole("button", { name: "Show more runs" })).not.toBeInTheDocument();
    expect(screen.getByText("Showing the first 100 discovery runs available through this view.")).toBeInTheDocument();
    const limits = requestPaths(fetch).filter((path) => path.startsWith("/api/v1/jobs/discovery-runs?")).map((path) => Number(new URL(path, window.location.origin).searchParams.get("limit")));
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
    fireEvent.click(screen.getAllByRole("button", { name: "View detail" })[0]);
    expect(await screen.findByRole("article", { name: "Current opportunity detail" })).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities/eval-alpha");
    expect(screen.getByText(/Recommendation reasoning/)).toBeInTheDocument();
    expect(screen.getAllByText(/Canonical requirement/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Requirement reference unavailable.").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Posting recency signal/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/private-evidence-id/)).not.toBeInTheDocument();
    const link = screen.getAllByRole("link", { name: "Open vacancy" })[0];
    expect(link).toHaveAttribute("target", "_blank"); expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("removes a no-longer-current opportunity and refreshes the shortlist neutrally", async () => {
    let detailCalls = 0;
    const fetch = fakeFetch({ "GET /api/v1/jobs/opportunities/eval-alpha": () => { detailCalls += 1; return json(undefined, 404); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getAllByRole("button", { name: "View detail" })[0]);
    expect(await screen.findByText(/no longer current/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Current opportunity detail" })).not.toBeInTheDocument();
    expect(detailCalls).toBe(1);
    await waitFor(() => expect(requestPaths(fetch).filter((path) => path.startsWith("/api/v1/jobs/opportunities?")).length).toBeGreaterThan(1));
  });

  it("loads run summaries then lazy detail, labels in-progress rows safely, and retrieves historical detail", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs": () => json(page([run("run-1", "running")])), "/api/v1/jobs/discovery-runs/run-1": () => json(runDetail("running")) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" });
    expect(await screen.findByText("Evaluation in progress")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View run" }));
    expect(await screen.findByText("Newly evaluated")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.queryByText("Analysis failed")).not.toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-1");
  });

  it("renders every terminal run outcome and lazy historical job detail as historical", async () => {
    const fetch = fakeFetch(); renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" });
    fireEvent.click(await screen.findByRole("button", { name: "View run" }));
    expect(screen.queryByRole("button", { name: "Prepare application" })).not.toBeInTheDocument();
    for (const label of ["Newly evaluated", "Reused evaluation", "Not actionable", "Presemantic filtered", "Outside semantic budget", "Semantic rejected", "Outside deep-analysis budget", "Analysis failed"]) expect(await screen.findByText(label)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Historical detail" })[0]);
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
    fireEvent.click(await screen.findByRole("button", { name: "View run" }));
    await screen.findByText("Reused evaluation");
    fireEvent.click(screen.getAllByRole("button", { name: "Historical detail" })[0]);
    expect(await screen.findByText(/No evaluation snapshot exists for this row/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Historical evaluation detail" })).not.toBeInTheDocument();
  });

  it("describes the shared recent inbox neutrally and keeps non-actionable rows visible but disabled", async () => {
    renderJobs(); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    expect(screen.getByText(/recent shared persisted public vacancies/)).toBeInTheDocument();
    const blocked = screen.getByRole("checkbox", { name: "Select Inbox blocked" });
    expect(blocked).toBeDisabled(); expect(screen.getByText(/Not actionable/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select Inbox actionable" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Prepare application" })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox blocked" }));
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
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Inbox selected-A" }));
    expect(screen.getByRole("button", { name: "Evaluate 1 jobs" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Inbox replacement-B")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Select Inbox selected-A" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evaluate selected jobs" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox replacement-B" }));
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
    const selected = await screen.findByRole("checkbox", { name: "Select Inbox selected-A" }); fireEvent.click(selected);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    const nowBlocked = await screen.findByRole("checkbox", { name: "Select Inbox selected-A" });
    expect(nowBlocked).toBeDisabled(); expect(nowBlocked).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Evaluate selected jobs" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox replacement-B" }));
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
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
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
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" });
    expect(await screen.findByText("Fresh page-one result")).toBeInTheDocument();
    expect(screen.queryByText("Larger window result")).not.toBeInTheDocument();
    expect(runGet).toBeGreaterThan(1);
    expect(requestPaths(fetch).filter((path) => path === "/api/v1/jobs/opportunities?limit=20").length).toBeGreaterThanOrEqual(2);
  });

  it("refreshes run history and reports neutral uncertainty after an interrupted POST", async () => {
    let runRequests = 0;
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs": () => { runRequests += 1; return json(page([run(`run-${runRequests}`)])); }, "POST /api/v1/jobs/discovery-runs": () => Promise.reject(new TypeError("offline")) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" })); await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByText(/cannot confirm from this response whether the run started/)).toBeInTheDocument();
    expect(runRequests).toBe(1);
  });

  it("does not claim run history refreshed when the post-transport-failure GET also fails", async () => {
    let runRequests = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => { runRequests += 1; return json(undefined, 503); },
      "POST /api/v1/jobs/discovery-runs": () => Promise.reject(new TypeError("offline")),
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByText(/cannot confirm from this response whether the run started/)).toBeInTheDocument();
    expect(screen.getByText(/Recent run history could not be confirmed as refreshed/)).toBeInTheDocument();
    expect(screen.queryByText(/Recent run history has been refreshed/)).not.toBeInTheDocument();
    expect(runRequests).toBe(1);
  });

  it("reports an authoritative HTTP evaluation error separately from transport uncertainty", async () => {
    let runRequests = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => { runRequests += 1; return json(page([run(`run-${runRequests}`)])); },
      "POST /api/v1/jobs/discovery-runs": () => json({ detail: "Conflict" }, 409),
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("link", { name: "Inbox" })); await screen.findByRole("heading", { name: "Inbox" });
    await editIntentOnFind("AI");
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByText(/Career-trans returned an error while creating the evaluation/)).toBeInTheDocument();
    expect(screen.getByText(/Recent run history has been refreshed/)).toBeInTheDocument();
    expect(screen.queryByText(/cannot confirm from this response whether the run started/)).not.toBeInTheDocument();
    expect(runRequests).toBe(1);
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
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
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

  it("uses different empty-opportunity messages for a new account and a historical-only account", async () => {
    const noItems = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([])), "/api/v1/jobs/discovery-runs": () => json(page([])) });
    renderJobs(noItems); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); expect(screen.queryByText("No jobs have been evaluated yet.")).not.toBeInTheDocument(); expect(screen.queryByText(/No current evaluated opportunities/)).not.toBeInTheDocument(); cleanup(); sessionStorage.clear();
    const oldRuns = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([])), "/api/v1/jobs/discovery-runs": () => json(page([run()])) });
    renderJobs(oldRuns); await screen.findByRole("heading", { name: "Find jobs" }); fireEvent.click(screen.getByRole("link", { name: "Search history" })); await screen.findByRole("heading", { name: "Search history" }); await screen.findByText("Completed"); fireEvent.click(screen.getByRole("link", { name: "My opportunities" })); await screen.findByRole("heading", { name: "Recommended / Current analyses" }); expect(await screen.findByText(/No current evaluated opportunities. Historical runs are available below/)).toBeInTheDocument();
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
    fireEvent.click(screen.getAllByRole("button", { name: "View detail" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "View detail" }));
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
    fireEvent.click(screen.getAllByRole("button", { name: "View run" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "View run" }));
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
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Applied AI" } });
    fireEvent.click(screen.getByRole("button", { name: "Save or configure search" }));
    expect(await screen.findByRole("heading", { name: "New saved discovery" })).toBeInTheDocument();
    expect(screen.getByLabelText("Prioritisation themes (one per line)")).toHaveValue("Applied AI");
    expect(requestPaths(fetch)).not.toContain("/api/v1/jobs/discovery-schedules/s-1/run-now");
    expect(requestPaths(fetch).filter((path) => path === "/api/v1/jobs/discovery-schedules").length).toBeGreaterThan(0);
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

  it.each(["/profile/cv", "/cv"]) ("preserves the CV workflow at %s", async (path) => {
    renderJobs(fakeFetch({ "/api/v1/onboarding/status": () => json({ ...ready, candidate_context_ready: false, latest_cv_draft: null }) }), path);
    expect(await screen.findByRole("heading", { name: "Upload your CV" })).toBeInTheDocument();
  });

  it.each(["/profile/adviser", "/adviser"]) ("preserves the Adviser workflow at %s", async (path) => {
    renderJobs(fakeFetch({ "/api/v1/onboarding/status": () => json({ ...ready, candidate_context_ready: false }) }), path);
    expect(await screen.findByRole("heading", { name: "Complete your CV first" })).toBeInTheDocument();
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

  it("renders the static Shortlisted placeholder without inferring data or reading opportunity state", async () => {
    const { fetch } = renderJobs(fakeFetch(), "/jobs/opportunities/shortlisted");
    expect(await screen.findByRole("heading", { name: "Shortlisted" })).toBeInTheDocument();
    expect(screen.getByText(/non-authoritative placeholder/)).toBeInTheDocument();
    expect(requestPaths(fetch).some((path) => path.startsWith("/api/v1/jobs/opportunities"))).toBe(false);
    expect(requestPaths(fetch).some((path) => path.startsWith("/api/v1/jobs/inbox"))).toBe(false);
    expect(requestPaths(fetch).some((path) => path.startsWith("/api/v1/jobs/discovery-runs"))).toBe(false);
  });

  it("loads history deep links directly and preserves run/job query navigation", async () => {
    const { fetch } = renderJobs(fakeFetch(), "/jobs/history?run=run-1&job=job-0");
    expect(await screen.findByRole("heading", { name: "Search history" })).toBeInTheDocument();
    expect(await screen.findByRole("article", { name: "Historical evaluation detail" })).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-1");
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-1/jobs/job-0");
  });
});

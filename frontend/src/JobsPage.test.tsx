import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { DiscoveryRunDetail, DiscoveryRunSummary, HistoricalRunJobDetail, InboxSummary, OnboardingStatus, RankedJobOpportunity, User, UserOpportunitySummary } from "./api";
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
const runDetail = (status: DiscoveryRunSummary["status"] = "completed"): DiscoveryRunDetail => ({ ...run("run-1", status), jobs: (["newly_evaluated", "reused_evaluation", "not_actionable", "presemantic_filtered", "outside_semantic_budget", "semantic_rejected", "outside_deep_analysis_budget", "analysis_failed"] as const).map((outcome, i) => ({ discovered_job_id: `job-${i}`, evaluation_id: null, outcome, failure_stage: outcome === "analysis_failed" ? "career_analysis" : null, failure_kind: null, opportunity: null })) });
const inboxItem = (id: string, actionable = true): InboxSummary => ({ discovered_job_id: id, title: `Inbox ${id}`, company: "Public Co", location: "London", work_arrangement: "Hybrid", employment_type: "Full-time", url: `https://public.example.test/${id}`, state: "new", verification_status: actionable ? "verified" : "unverified", verification_reason: actionable ? null : "provider_detail_unavailable", actionable, first_seen_at: "2026-02-01T00:00:00Z", last_seen_at: "2026-02-02T00:00:00Z", provenance: [{ runtime: "codex", source_ref: "not-rendered", discovered_via: "external_import", imported_at: "2026-02-02T00:00:00Z" }], provenance_count: 1 });
const historyDetail: HistoricalRunJobDetail = { discovered_job_id: "job-0", evaluation_id: "historical-eval", outcome: "newly_evaluated", failure_stage: null, failure_kind: null, opportunity: ranked("Historical role") };
const json = (body: unknown, status = 200) => new Response(body === undefined ? "" : JSON.stringify(body), { status });
type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>;
const page = (items: unknown[], truncated = false) => ({ items, limit: 20, truncated });
function fakeFetch(overrides: Record<string, Handler> = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const path = url.pathname;
    const override = overrides[`${init?.method ?? "GET"} ${path}`] ?? overrides[path];
    if (override) return Promise.resolve(override(url, init));
    if (path === "/api/v1/users/me") return Promise.resolve(json(user));
    if (path === "/api/v1/onboarding/status") return Promise.resolve(json(ready));
    if (path === "/api/v1/jobs/opportunities") return Promise.resolve(json(page([op("alpha", "Alpha", 99), op("beta", "Beta", 1)])));
    if (path === "/api/v1/jobs/discovery-runs") return Promise.resolve(json(page([run()])));
    if (path === "/api/v1/jobs/inbox") return Promise.resolve(json(page([inboxItem("actionable"), inboxItem("blocked", false)])));
    if (path === "/api/v1/jobs/opportunities/eval-alpha") return Promise.resolve(json(ranked("Alpha detail")));
    if (path === "/api/v1/jobs/discovery-runs/run-1") return Promise.resolve(json(runDetail()));
    if (path === "/api/v1/jobs/discovery-runs/run-1/jobs/job-0") return Promise.resolve(json(historyDetail));
    if (path === "/api/v1/jobs/discovery-runs" && init?.method === "POST") return Promise.resolve(json({ ...run("run-new"), jobs: [] }));
    throw new Error(`Unexpected request ${init?.method ?? "GET"} ${path}${url.search}`);
  });
}
function renderJobs(fetch = fakeFetch(), path = "/jobs") {
  sessionStorage.setItem(TOKEN, "test-token");
  vi.stubGlobal("fetch", fetch);
  return { ...render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>), fetch };
}
function requestPaths(fetch: ReturnType<typeof fakeFetch>) { return fetch.mock.calls.map(([input]) => { const url = new URL(String(input), window.location.origin); return `${url.pathname}${url.search}`; }); }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function loaded() { await screen.findByRole("heading", { name: "Current ranked opportunities" }); await screen.findByText("Alpha"); }
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
    expect(await screen.findByRole("link", { name: "Jobs" })).toHaveAttribute("href", "/jobs");
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities?limit=20");
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs?limit=20");
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/inbox?limit=20");
    const titles = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);
    expect(titles.slice(0, 2)).toEqual(["Alpha", "Beta"]);
    expect(screen.getByText("1")).toBeInTheDocument(); expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.queryByText("99")).not.toBeInTheDocument();
  });

  it("redirects unauthenticated /jobs through the existing sign-in route", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<MemoryRouter initialEntries={["/jobs"]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Jobs" })).not.toBeInTheDocument();
  });

  it("blocks evaluation before candidate context is ready and links to CV onboarding", async () => {
    renderJobs(fakeFetch({ "/api/v1/onboarding/status": () => json({ ...ready, candidate_context_ready: false }) }));
    expect(await screen.findByRole("heading", { name: "Complete your CV first" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to CV onboarding" })).toHaveAttribute("href", "/cv");
    fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    expect(screen.queryByRole("heading", { name: "Evaluate selected actionable jobs" })).not.toBeInTheDocument();
  });

  it("keeps Jobs usable for an unconfirmed newer CV and incomplete optional Adviser", async () => {
    renderJobs(fakeFetch({ "/api/v1/onboarding/status": () => json({ ...ready, latest_cv_draft: { ...ready.latest_cv_draft!, state: "review_ready" }, adviser: { ...ready.adviser, intake_exists: true, assessment_status: "stale" } }) }));
    expect(await screen.findByText(/A newer CV update is awaiting review/)).toBeInTheDocument();
    expect(screen.getByText(/Career Adviser completion is optional/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    expect(screen.getByRole("heading", { name: "Evaluate selected actionable jobs" })).toBeInTheDocument();
  });

  it("replaces the entire top-N opportunity window when Show more is used", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": (url) => json(page(url.searchParams.get("limit") === "40" ? [op("new-top", "New top window")] : [op("old-top", "Old window")], true)) });
    renderJobs(fetch); expect(await screen.findByText("Old window")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more current opportunities" }));
    expect(await screen.findByText("New top window")).toBeInTheDocument();
    expect(screen.queryByText("Old window")).not.toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities?limit=40");
  });

  it("caps opportunities at 100 and explains a truncated maximum window", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([op("at-limit")], true)) });
    renderJobs(fetch); await screen.findByText("at-limit");
    await growToLimit(fetch, "/api/v1/jobs/opportunities", "Show more current opportunities");
    expect(screen.queryByRole("button", { name: "Show more current opportunities" })).not.toBeInTheDocument();
    expect(screen.getByText("Showing the first 100 current opportunities available through this view.")).toBeInTheDocument();
    const limits = requestPaths(fetch).filter((path) => path.startsWith("/api/v1/jobs/opportunities?")).map((path) => Number(new URL(path, window.location.origin).searchParams.get("limit")));
    expect(limits).toContain(100); expect(limits.every((limit) => limit <= 100)).toBe(true); expect(limits).not.toContain(120);
  });

  it("caps discovery runs at 100 and explains a truncated maximum window", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs": () => json(page([run()], true)) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Discovery runs" }));
    await growToLimit(fetch, "/api/v1/jobs/discovery-runs", "Show more runs");
    expect(screen.queryByRole("button", { name: "Show more runs" })).not.toBeInTheDocument();
    expect(screen.getByText("Showing the first 100 discovery runs available through this view.")).toBeInTheDocument();
    const limits = requestPaths(fetch).filter((path) => path.startsWith("/api/v1/jobs/discovery-runs?")).map((path) => Number(new URL(path, window.location.origin).searchParams.get("limit")));
    expect(limits).toContain(100); expect(limits.every((limit) => limit <= 100)).toBe(true); expect(limits).not.toContain(120);
  });

  it("caps the recent inbox at 100 and explains a truncated maximum window", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/inbox": () => json(page([inboxItem("at-limit")], true)) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
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
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Discovery runs" }));
    expect(await screen.findByText("Evaluation in progress")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View run" }));
    expect(await screen.findByText("Newly evaluated")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.queryByText("Analysis failed")).not.toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-1");
  });

  it("renders every terminal run outcome and lazy historical job detail as historical", async () => {
    const fetch = fakeFetch(); renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Discovery runs" }));
    fireEvent.click(await screen.findByRole("button", { name: "View run" }));
    for (const label of ["Newly evaluated", "Reused evaluation", "Not actionable", "Presemantic filtered", "Outside semantic budget", "Semantic rejected", "Outside deep-analysis budget", "Analysis failed"]) expect(await screen.findByText(label)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Historical detail" })[0]);
    expect(await screen.findByRole("article", { name: "Historical evaluation detail" })).toBeInTheDocument();
    expect(screen.getByText(/does not describe the vacancy or recommendation as current/)).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/discovery-runs/run-1/jobs/job-0");
  });

  it("shows only the factual historical outcome when the historical snapshot is null", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs/run-1/jobs/job-0": () => json({ ...historyDetail, opportunity: null, outcome: "semantic_rejected" }) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Discovery runs" }));
    fireEvent.click(await screen.findByRole("button", { name: "View run" }));
    await screen.findByText("Reused evaluation");
    fireEvent.click(screen.getAllByRole("button", { name: "Historical detail" })[0]);
    expect(await screen.findByText(/No evaluation snapshot exists for this row/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Historical evaluation detail" })).not.toBeInTheDocument();
  });

  it("describes the shared recent inbox neutrally and keeps non-actionable rows visible but disabled", async () => {
    renderJobs(); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    expect(screen.getByText(/most recent imported public vacancies/)).toBeInTheDocument();
    const blocked = screen.getByRole("checkbox", { name: "Select Inbox blocked" });
    expect(blocked).toBeDisabled(); expect(screen.getByText(/Not actionable/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select Inbox actionable" })).toBeEnabled();
  });

  it("replaces the recent inbox window and only submits selected actionable persisted IDs with structured controls", async () => {
    let inboxFetch = 0; let postBody: unknown;
    const fetch = fakeFetch({ "/api/v1/jobs/inbox": (url) => { inboxFetch += 1; return json(page(url.searchParams.get("limit") === "40" ? [inboxItem("new-inbox")] : [inboxItem("actionable"), inboxItem("blocked", false)], true)); }, "POST /api/v1/jobs/discovery-runs": (_url, init) => { postBody = JSON.parse(String(init?.body)); return json({ ...run("new-run"), jobs: [] }); } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    fireEvent.click(screen.getByRole("button", { name: "Show more recent vacancies" }));
    expect(await screen.findByText("Inbox new-inbox")).toBeInTheDocument(); expect(screen.queryByText("Inbox actionable")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more recent vacancies" }));
    await waitFor(() => expect(inboxFetch).toBeGreaterThan(1));
    expect(await screen.findByText("Inbox actionable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox blocked" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI Engineer, Applied AI Engineer" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Location eligibility (one location per line)" }), { target: { value: "London, United Kingdom" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Remote policy" }), { target: { value: "exclude_remote" } });
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await waitFor(() => expect(postBody).toBeDefined());
    expect(postBody).toMatchObject({ discovered_job_ids: ["actionable"], query: { keywords: ["AI Engineer", "Applied AI Engineer"], locations: ["London, United Kingdom"], remote_ok: false, companies: [], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 50 }, max_semantic_candidates: 10, max_full_analyses: 5 });
  });

  it("removes a disappearing selected inbox job from selection and submission", async () => {
    let inboxCalls = 0; let postBody: { discovered_job_ids: string[] } | undefined;
    const fetch = fakeFetch({
      "/api/v1/jobs/inbox": () => json(page(inboxCalls++ === 0 ? [inboxItem("selected-A")] : [inboxItem("replacement-B")], true)),
      "POST /api/v1/jobs/discovery-runs": (_url, init) => { postBody = JSON.parse(String(init?.body)); return json({ ...run("run-new"), jobs: [] }); },
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI" } });
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Inbox selected-A" }));
    expect(screen.getByRole("button", { name: "Evaluate 1 jobs" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Inbox replacement-B")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Select Inbox selected-A" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evaluate selected jobs" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox replacement-B" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI" } });
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
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    const selected = await screen.findByRole("checkbox", { name: "Select Inbox selected-A" }); fireEvent.click(selected);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    const nowBlocked = await screen.findByRole("checkbox", { name: "Select Inbox selected-A" });
    expect(nowBlocked).toBeDisabled(); expect(nowBlocked).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Evaluate selected jobs" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox replacement-B" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    await waitFor(() => expect(postBody).toBeDefined());
    expect(postBody?.discovered_job_ids).toEqual(["replacement-B"]);
    expect(postBody?.discovered_job_ids).not.toContain("selected-A");
  });

  it("keeps a long evaluation visibly pending and prevents a duplicate submission", async () => {
    const pending = deferred<Response>(); let postCount = 0;
    const fetch = fakeFetch({ "POST /api/v1/jobs/discovery-runs": () => { postCount += 1; return pending.promise; } });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI" } });
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
    renderJobs(fetch); expect(await screen.findByText("Initial page-one result")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more current opportunities" }));
    expect(await screen.findByText("Larger window result")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    fireEvent.click(screen.getByRole("button", { name: "Opportunities" }));
    expect(await screen.findByText("Fresh page-one result")).toBeInTheDocument();
    expect(screen.queryByText("Larger window result")).not.toBeInTheDocument();
    expect(runGet).toBeGreaterThan(1);
    expect(requestPaths(fetch).filter((path) => path === "/api/v1/jobs/opportunities?limit=20").length).toBeGreaterThanOrEqual(2);
  });

  it("refreshes run history and reports neutral uncertainty after an interrupted POST", async () => {
    let runRequests = 0;
    const fetch = fakeFetch({ "/api/v1/jobs/discovery-runs": () => { runRequests += 1; return json(page([run(`run-${runRequests}`)])); }, "POST /api/v1/jobs/discovery-runs": () => Promise.reject(new TypeError("offline")) });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" })); fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByText(/cannot confirm from this response whether the run started/)).toBeInTheDocument();
    expect(runRequests).toBeGreaterThan(1);
  });

  it("does not claim run history refreshed when the post-transport-failure GET also fails", async () => {
    let runRequests = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => { runRequests += 1; return runRequests === 1 ? json(page([run("run-initial")])) : json(undefined, 503); },
      "POST /api/v1/jobs/discovery-runs": () => Promise.reject(new TypeError("offline")),
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByText(/cannot confirm from this response whether the run started/)).toBeInTheDocument();
    expect(screen.getByText(/Recent run history could not be confirmed as refreshed/)).toBeInTheDocument();
    expect(screen.queryByText(/Recent run history has been refreshed/)).not.toBeInTheDocument();
    expect(runRequests).toBe(2);
  });

  it("reports an authoritative HTTP evaluation error separately from transport uncertainty", async () => {
    let runRequests = 0;
    const fetch = fakeFetch({
      "/api/v1/jobs/discovery-runs": () => { runRequests += 1; return json(page([run(`run-${runRequests}`)])); },
      "POST /api/v1/jobs/discovery-runs": () => json({ detail: "Conflict" }, 409),
    });
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));
    expect(await screen.findByText(/Career-trans returned an error while creating the evaluation/)).toBeInTheDocument();
    expect(screen.getByText(/Recent run history has been refreshed/)).toBeInTheDocument();
    expect(screen.queryByText(/cannot confirm from this response whether the run started/)).not.toBeInTheDocument();
    expect(runRequests).toBeGreaterThan(1);
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
    renderJobs(fetch); await screen.findByText("Pre-evaluation shortlist");
    fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Search themes/ }), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Inbox actionable" }));
    fireEvent.click(screen.getByRole("button", { name: "Evaluate 1 jobs" }));

    await waitFor(() => expect(opportunityRequests).toBe(2));
    expect(await screen.findByText(/Evaluation completed\. Refreshing recent runs and the current shortlist/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Opportunities" }));
    expect(screen.queryByText("Pre-evaluation shortlist")).not.toBeInTheDocument();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(requestPaths(fetch)).toContain("/api/v1/jobs/opportunities?limit=20");

    refreshedOpportunities.resolve(json(page([op("new", "Post-evaluation shortlist")])));
    expect(await screen.findByText("Post-evaluation shortlist")).toBeInTheDocument();
    expect(screen.queryByText("Pre-evaluation shortlist")).not.toBeInTheDocument();
    expect(await screen.findByText(/Evaluation completed\. Recent runs were refreshed; the current shortlist was refreshed\./)).toBeInTheDocument();
  });

  it("uses different empty-opportunity messages for a new account and a historical-only account", async () => {
    const noItems = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([])), "/api/v1/jobs/discovery-runs": () => json(page([])) });
    renderJobs(noItems); expect(await screen.findByText("No jobs have been evaluated yet.")).toBeInTheDocument(); cleanup(); sessionStorage.clear();
    const oldRuns = fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([])), "/api/v1/jobs/discovery-runs": () => json(page([run()])) });
    renderJobs(oldRuns); expect(await screen.findByText(/No current evaluated opportunities. Historical runs are available below/)).toBeInTheDocument();
  });

  it("does not claim historical runs exist when run history is unavailable", async () => {
    renderJobs(fakeFetch({ "/api/v1/jobs/opportunities": () => json(page([])), "/api/v1/jobs/discovery-runs": () => json(undefined, 503) }));
    await screen.findByRole("heading", { name: "Current ranked opportunities" });
    await waitFor(() => expect(screen.queryByText(/Historical runs are available below/)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Discovery runs" }));
    expect(await screen.findByText(/Discovery run history is unavailable/)).toBeInTheDocument();
  });

  it("keeps successfully loaded sections when another section is retried", async () => {
    let opportunities = 0;
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => { opportunities += 1; return opportunities === 1 ? json(undefined, 503) : json(page([op("recovered")])); } });
    renderJobs(fetch); expect(await screen.findByText(/Current opportunities are unavailable/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry this section" }));
    expect(await screen.findByText("recovered")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Discovery runs" })); expect(screen.getByText("Completed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" })); expect(screen.getByText("Inbox actionable")).toBeInTheDocument();
  });

  it("uses the existing authenticated 401 session-clear behavior", async () => {
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => json(undefined, 401) });
    renderJobs(fetch); expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it("cannot restore an older delayed response after an authenticated request clears the session", async () => {
    const oldOpportunity = deferred<Response>();
    const fetch = fakeFetch({ "/api/v1/jobs/opportunities": () => oldOpportunity.promise, "/api/v1/jobs/discovery-runs": () => json(undefined, 401) });
    renderJobs(fetch);
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
    renderJobs(fetch); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Discovery runs" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Recent vacancies" }));
    expect(screen.getByLabelText("Search themes (soft prioritisation; not eligibility filters)")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Remote policy" })).toBeInTheDocument();
    expect(screen.getByText(/does not start internet discovery/)).toBeInTheDocument();
  });
});

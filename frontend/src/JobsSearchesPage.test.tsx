import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { DiscoveryScheduleRead, User } from "./api";
import { App } from "./App";
import { AuthProvider } from "./auth";

const TOKEN = "career-trans.access-token";
const user: User = { id: "u1", email: "schedules@example.test", created_at: "2026-01-01T00:00:00Z" };
const schedule = (patch: Partial<DiscoveryScheduleRead> = {}): DiscoveryScheduleRead => ({
  id: "s-1", name: "AI roles", enabled: true,
  schedule: { cadence: "weekly", timezone: "Europe/London", local_time: "09:30:15.125000", weekdays: [0, 2, 6] },
  query: { keywords: ["AI, ML platform", "FDE"], locations: ["London, United Kingdom"], remote_ok: true, companies: ["Hidden query company"], excluded_companies: ["Avoid Co"], excluded_title_terms: ["Intern, Junior"], employment_types: ["Full-time"], max_results: 73 },
  acquisition: {
    structured_ats: { enabled: true, companies: ["OpenAI"], providers: ["greenhouse", "legacy-provider"], all_resolved_sources: false, max_sources: 27, max_results: 42 },
    agentic_web: { enabled: false, country: "us", max_search_queries: 8, max_search_results_per_query: 14, max_pages_to_open: 19, max_discovered_jobs: 31 },
  },
  evaluation: { max_semantic_candidates: 17, max_full_analyses: 8, min_relevance_score: 0.63 }, next_run_at: "2026-10-01T08:30:00Z", last_execution_at: "2026-09-20T08:00:00Z", ...patch,
});
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>;
const key = (method: string, path: string) => `${method} ${path}`;
function fakeFetch(overrides: Record<string, Handler> = {}, initial: DiscoveryScheduleRead[] = [schedule()]) {
  const requests: Array<{ method: string; path: string; body?: unknown }> = [];
  let list = initial;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin); const method = init?.method ?? "GET"; const path = url.pathname;
    requests.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const handler = overrides[key(method, path)] ?? overrides[path];
    if (handler) return handler(url, init);
    if (path === "/api/v1/users/me") return response(user);
    if (path === "/api/v1/jobs/discovery-schedules" && method === "GET") return response(list);
    if (path === "/api/v1/jobs/discovery-schedules" && method === "POST") {
      const created = schedule({ id: "created", ...(JSON.parse(String(init?.body)) as object) }); list = [...list, created]; return response(created, 201);
    }
    const match = path.match(/^\/api\/v1\/jobs\/discovery-schedules\/([^/]+)$/);
    if (match && method === "GET") return response(list.find((item) => item.id === decodeURIComponent(match[1])) ?? schedule({ id: match[1] }), list.some((item) => item.id === match[1]) ? 200 : 404);
    if (match && method === "PATCH") {
      const current = list.find((item) => item.id === match[1]) ?? schedule({ id: match[1] });
      const patch = JSON.parse(String(init?.body)); const updated = { ...current, ...patch } as DiscoveryScheduleRead;
      list = list.map((item) => item.id === match[1] ? updated : item); return response(updated);
    }
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
  return { fetch, requests };
}
function renderPage(path = "/jobs/searches", setup: Record<string, Handler> = {}, initial?: DiscoveryScheduleRead[]) {
  sessionStorage.setItem(TOKEN, "test-token"); const mocked = fakeFetch(setup, initial);
  vi.stubGlobal("fetch", mocked.fetch);
  const view = render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
  return { ...view, ...mocked };
}
async function loaded() { await screen.findByRole("heading", { name: "Your saved discoveries" }); await waitFor(() => expect(screen.queryByText("Loading saved discoveries…")).not.toBeInTheDocument()); }
function getCalls(requests: ReturnType<typeof fakeFetch>["requests"], method: string, path: string) { return requests.filter((item) => item.method === method && item.path === path); }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });
afterEach(cleanup);

describe("Issue #175 saved discovery configurations", () => {
  it("protects the direct /jobs/searches route and exposes navigation from both authenticated surfaces", async () => {
    const { requests } = renderPage("/jobs");
    expect(await screen.findByRole("link", { name: "Manage saved discovery configurations" })).toHaveAttribute("href", "/jobs/searches");
    fireEvent.click(screen.getByRole("link", { name: "Manage saved discovery configurations" }));
    expect(await screen.findByRole("heading", { name: "Saved discovery configurations" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Saved searches" })).toHaveAttribute("href", "/jobs/searches");
    expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules").length).toBeGreaterThan(0);
  });

  it("redirects an unauthenticated direct route to sign in", async () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<MemoryRouter initialEntries={["/jobs/searches"]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Saved discovery configurations" })).not.toBeInTheDocument();
  });

  it("renders backend list order, clear paused/enabled facts, execution label, and no pagination claim", async () => {
    const first = schedule({ id: "first", name: "First", enabled: true });
    const second = schedule({ id: "second", name: "Second", enabled: false });
    renderPage("/jobs/searches", {}, [first, second]); await loaded();
    const headings = screen.getAllByRole("heading", { level: 3 }).map((item) => item.textContent);
    expect(headings).toEqual(["First", "Second"]);
    expect(screen.getByText(/Recurrence configured/)).toBeInTheDocument(); expect(screen.getByText(/^Paused ·/)).toBeInTheDocument();
    const cards = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.closest("li")!);
    expect(within(cards[0]).getByText(/Recurrence is configured for this saved discovery/)).toBeInTheDocument();
    expect(within(cards[0]).queryByText(/Automatic recurrence is paused/)).not.toBeInTheDocument();
    expect(within(cards[1]).getByText(/Automatic recurrence is paused for this saved discovery/)).toBeInTheDocument();
    expect(within(cards[1]).queryByText(/Recurrence is configured for this saved discovery/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/Last recorded execution completion/)).toHaveLength(2);
    expect(screen.queryByText(/Last successful run/)).not.toBeInTheDocument();
    expect(screen.getByText(/complete list; this view does not paginate it/)).toBeInTheDocument();
  });

  it("distinguishes an empty list from unavailable list data and preserves loaded items on refresh failure", async () => {
    renderPage("/jobs/searches", {}, []);
    expect(await screen.findByText("No saved discovery configurations yet.")).toBeInTheDocument();
    cleanup();
    let calls = 0;
    renderPage("/jobs/searches", { ["GET /api/v1/jobs/discovery-schedules"]: () => ++calls === 1 ? response([schedule()]) : Promise.reject(new TypeError("offline")) });
    await loaded(); fireEvent.click(screen.getByRole("button", { name: "Refresh list" }));
    expect(await screen.findByText(/Previously loaded saved configurations remain shown/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "AI roles" })).toBeInTheDocument();
  });

  it("keeps an unsaved editor draft intact when the independent schedule list refreshes", async () => {
    const { requests } = renderPage(); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Unsaved draft" } }); fireEvent.click(screen.getByRole("button", { name: "Refresh list" }));
    await waitFor(() => expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules")).toHaveLength(2));
    expect(screen.getByDisplayValue("Unsaved draft")).toBeInTheDocument(); expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(0);
  });

  it("keeps loading/error unknown state distinct from factual empty", async () => {
    renderPage("/jobs/searches", { ["GET /api/v1/jobs/discovery-schedules"]: () => Promise.reject(new TypeError("offline")) });
    expect(await screen.findByRole("alert")).toHaveTextContent("Saved discovery configurations are unavailable.");
    expect(screen.queryByText("No saved discovery configurations yet.")).not.toBeInTheDocument();
  });

  it("opens a safe paused new form without silently enabling channels or readiness checks", async () => {
    const { requests } = renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet.");
    fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    expect(screen.getByRole("checkbox", { name: "Recurrence enabled" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Structured ATS/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Profile-driven bounded server-side web discovery/ })).not.toBeChecked();
    expect(screen.getByLabelText("Configured local time")).toHaveValue("09:00:00");
    expect(screen.getByLabelText("IANA timezone")).toHaveValue(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    expect(screen.getByText(/Choose at least one channel explicitly/)).toBeInTheDocument();
    expect(getCalls(requests, "GET", "/api/v1/onboarding/status")).toHaveLength(0);
  });

  it("falls back to UTC for a new-form browser timezone default when Intl cannot resolve one", async () => {
    const original = Intl.DateTimeFormat;
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(((...args: ConstructorParameters<typeof Intl.DateTimeFormat>) => {
      if (args.length === 0) throw new RangeError("timezone unavailable");
      return new original(...args);
    }) as typeof Intl.DateTimeFormat);
    renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet."); fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    expect(screen.getByLabelText("IANA timezone")).toHaveValue("UTC");
  });

  it("exposes and enforces bounded acquisition and evaluation controls", async () => {
    const { requests } = renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet."); fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bounds" } }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } }); fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ })); fireEvent.click(screen.getByRole("checkbox", { name: /Profile-driven bounded server-side web discovery/ }));
    for (const label of ["Maximum resolved sources (1–100)", "Maximum ATS results (1–100)", "Maximum semantic candidates (1–100)"]) expect(screen.getByLabelText(label)).toHaveAttribute("min", "1");
    expect(screen.getByLabelText("Maximum resolved sources (1–100)")).toHaveAttribute("max", "100"); expect(screen.getByLabelText("Maximum ATS results (1–100)")).toHaveAttribute("max", "100");
    expect(screen.getByLabelText("Maximum semantic candidates (1–100)")).toHaveAttribute("max", "100"); expect(screen.getByLabelText("Maximum full analyses (1–30)")).toHaveAttribute("max", "30");
    expect(screen.getByLabelText("Maximum search queries (1–100)")).toHaveAttribute("max", "100"); expect(screen.getByLabelText("Maximum search results per query (1–50)")).toHaveAttribute("max", "50");
    expect(screen.getByLabelText("Maximum pages to open (1–100)")).toHaveAttribute("max", "100"); expect(screen.getByLabelText("Maximum discovered jobs (1–100)")).toHaveAttribute("max", "100");
    expect(screen.getByLabelText("Minimum relevance score (0–1)")).toHaveAttribute("min", "0"); expect(screen.getByLabelText("Minimum relevance score (0–1)")).toHaveAttribute("max", "1");
    fireEvent.change(screen.getByLabelText("Maximum resolved sources (1–100)"), { target: { value: "101" } }); fireEvent.submit(screen.getByRole("button", { name: "Save configuration" }).closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Structured ATS maximum sources must be between 1 and 100."); expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(0);
  });

  it("normalizes create line inputs, maps daily payload, uses query defaults and never executes discovery", async () => {
    const { requests } = renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet.");
    fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    expect(screen.getByText(/Saving is configuration-only and does not check readiness/)).toBeInTheDocument();
    expect(screen.getByText(/Running a saved discovery requires confirmed candidate context and the required server-side providers/)).toBeInTheDocument();
    expect(screen.queryByText(/Saving requires a confirmed candidate profile/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  AI hunt  " } });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "  AI, ML platform  \n\n FDE " } });
    fireEvent.change(screen.getByLabelText("Eligibility locations (one complete location per line)"), { target: { value: " London, United Kingdom \n\nOxford, United Kingdom" } });
    fireEvent.change(screen.getByLabelText("Excluded companies (one per line)"), { target: { value: " Avoid Co \n\n Other Co " } });
    fireEvent.change(screen.getByLabelText("Excluded title terms (one per line)"), { target: { value: " Intern, Junior " } });
    fireEvent.change(screen.getByLabelText("Employment types (one per line)"), { target: { value: " Full-time \n Contract " } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(1));
    const body = getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")[0].body as Record<string, any>;
    expect(body.name).toBe("AI hunt"); expect(body.enabled).toBe(false); expect(body.schedule).toMatchObject({ cadence: "daily", local_time: "09:00:00", weekdays: [] });
    expect(body.query).toMatchObject({ keywords: ["AI, ML platform", "FDE"], locations: ["London, United Kingdom", "Oxford, United Kingdom"], companies: [], max_results: 50, excluded_companies: ["Avoid Co", "Other Co"], excluded_title_terms: ["Intern, Junior"], employment_types: ["Full-time", "Contract"] });
    expect(body.acquisition.structured_ats).toMatchObject({ enabled: true, all_resolved_sources: true, companies: [], providers: [] });
    expect(body.evaluation).toEqual({ max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 });
    expect(requests.some((item) => item.path.includes("/run-now") || item.path.includes("/executions") || item.path === "/api/v1/jobs/discover")).toBe(false);
    await screen.findByText(/The saved-configuration list is current/);
  });

  it("explains soft themes, comma preservation, server-side profile-driven web, DST, and manual-run boundary", async () => {
    renderPage(); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    expect(screen.getByText(/Themes prioritise structured ATS candidates softly/)).toBeInTheDocument();
    expect(screen.getByText(/not exact web-search terms or eligibility filters/)).toBeInTheDocument();
    expect(screen.getByText(/This is not Codex/)).toBeInTheDocument();
    expect(screen.getByText(/search strategy comes from confirmed candidate context/i)).toBeInTheDocument();
    expect(screen.getByText(/Daylight-saving gaps are skipped; repeated local times use the first occurrence/)).toBeInTheDocument();
    expect(screen.getByText(/Manual Run now and execution history are not available here yet/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Agentic country/i)).not.toBeInTheDocument();
  });

  it("supports exact weekly weekday indexes", async () => {
    const { requests } = renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet."); fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Weekly" } }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ })); fireEvent.change(screen.getByLabelText("Cadence"), { target: { value: "weekly" } });
    for (const day of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]) fireEvent.click(screen.getByRole("checkbox", { name: day }));
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(1));
    const body = getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")[0].body as any;
    expect(body.schedule.weekdays).toEqual([0, 1, 2, 3, 4, 5, 6]); expect(body.schedule.cadence).toBe("weekly");
  });

  it("rejects whitespace-only names before POST", async () => {
    const { requests } = renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet."); fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "   " } }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } }); fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a configuration name.");
    expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(0);
  });

  it("rejects blank-only themes before POST when the name is valid", async () => {
    const { requests } = renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet."); fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "No themes" } }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "  \n\t\n" } }); fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Add at least one prioritisation theme.");
    expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(0);
  });

  it("uses backend/default Agentic country for new schedules and validates channel/evaluation bounds", async () => {
    const { requests } = renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet."); fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bounds" } }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } }); fireEvent.click(screen.getByRole("checkbox", { name: /Profile-driven bounded server-side web discovery/ }));
    expect(screen.queryByLabelText(/country/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Maximum discovered jobs (1–100)")).toHaveAttribute("max", "100");
    fireEvent.change(screen.getByLabelText("Maximum discovered jobs (1–100)"), { target: { value: "101" } });
    fireEvent.submit(screen.getByRole("button", { name: "Save configuration" }).closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Maximum discovered jobs must be between 1 and 100."); expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Maximum discovered jobs (1–100)"), { target: { value: "20" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(1));
    expect((getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")[0].body as any).acquisition.agentic_web.country).toBe("gb");
  });

  it("requires an explicit filtered ATS company/provider scope and shows supported provider options only", async () => {
    const { requests } = renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet."); fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Filtered" } }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ })); fireEvent.click(screen.getByRole("radio", { name: "Filtered resolved sources" }));
    expect(screen.getByText(/exact matches, not partial-text searches/)).toBeInTheDocument(); expect(screen.getByText(/Company values OR together; provider values OR together; when both dimensions are used, both must match/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "greenhouse" })).toBeInTheDocument(); expect(screen.getByRole("checkbox", { name: "recruitee" })).toBeInTheDocument(); expect(screen.queryByRole("textbox", { name: /provider/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Choose at least one company or provider filter/);
    fireEvent.click(screen.getByRole("checkbox", { name: "greenhouse" })); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(1));
    expect((getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")[0].body as any).acquisition.structured_ats).toMatchObject({ enabled: true, companies: [], providers: ["greenhouse"], all_resolved_sources: false });
  });

  it("switching to unfiltered ATS clears filters and carries exact max-source wording", async () => {
    renderPage("/jobs/searches", {}, []); await screen.findByText("No saved discovery configurations yet."); fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Unfiltered" } }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } }); fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Filtered resolved sources" })); fireEvent.change(screen.getByLabelText("Resolved company filters (one company name per line)"), { target: { value: "OpenAI" } }); fireEvent.click(screen.getByRole("radio", { name: /No company\/provider filter/ }));
    expect(screen.getByText("No company/provider filter — scan up to the configured maximum number of resolved sources.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Resolved company filters (one company name per line)")).not.toBeInTheDocument();
  });

  it("uses authoritative GET for edit baseline, keeps exact local-time precision, and name-only PATCH is top-level only", async () => {
    const { requests } = renderPage("/jobs/searches", { "GET /api/v1/jobs/discovery-schedules/s-1": () => response(schedule({ name: "Fresh server name", schedule: { cadence: "weekly", timezone: "Europe/London", local_time: "09:30:15.125000", weekdays: [0, 2] } })) }); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("Fresh server name");
    expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1);
    expect(screen.getByLabelText("Configured local time")).toHaveValue("09:30:15.125000");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Renamed" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1));
    const calls = getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1"); expect(calls[0].body).toEqual({ name: "Renamed" });
    expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1);
  });

  it("does a fresh exact GET immediately before nested PATCH and preserves hidden persisted fields", async () => {
    const fresh = schedule({ query: { ...schedule().query, companies: ["New hidden company"], max_results: 91 }, acquisition: { ...schedule().acquisition, agentic_web: { ...schedule().acquisition.agentic_web, country: "fr" } } });
    const { requests } = renderPage("/jobs/searches", { "GET /api/v1/jobs/discovery-schedules/s-1": () => response(fresh) }); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles");
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI systems" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1));
    const ordered = requests.filter((item) => item.path.endsWith("/s-1") && ["GET", "PATCH"].includes(item.method)).map((item) => item.method);
    expect(ordered).toEqual(["GET", "GET", "PATCH"]);
    const patch = getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[0].body as any;
    expect(patch).toEqual({ query: { ...fresh.query, keywords: ["AI systems"] } });
    expect(patch.query.companies).toEqual(["New hidden company"]); expect(patch.query.max_results).toBe(91);
    expect(patch.acquisition).toBeUndefined(); expect(patch.schedule).toBeUndefined();
  });

  it("aborts a nested save when the mandatory fresh GET fails", async () => {
    let gets = 0; const { requests } = renderPage("/jobs/searches", { "GET /api/v1/jobs/discovery-schedules/s-1": () => ++gets === 1 ? response(schedule()) : Promise.reject(new TypeError("offline")) }); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles"); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "Changed" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Changes could not be saved"); expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(0);
  });

  it("omits schedule when only evaluation changes and applies the authoritative PATCH response", async () => {
    const { requests } = renderPage("/jobs/searches", { "PATCH /api/v1/jobs/discovery-schedules/s-1": (_url, init) => response({ ...schedule(), ...(JSON.parse(String(init?.body)) as object), evaluation: { max_semantic_candidates: 22, max_full_analyses: 6, min_relevance_score: 0.7 } }) }); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles"); fireEvent.change(screen.getByLabelText("Maximum semantic candidates (1–100)"), { target: { value: "22" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1));
    expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[0].body).toEqual({ evaluation: { max_semantic_candidates: 22, max_full_analyses: 8, min_relevance_score: 0.63 } });
    expect(screen.getByLabelText("Maximum semantic candidates (1–100)")).toHaveValue(22);
  });

  it("discards edits by refetching the exact persisted record", async () => {
    const { requests } = renderPage(); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Unsaved" } }); fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await screen.findByDisplayValue("AI roles"); expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(2); expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(0);
  });

  it("preserves legacy remote true, query companies/max_results, hidden Agentic country and dormant disabled channel state on unrelated edits", async () => {
    const value = schedule({ acquisition: { ...schedule().acquisition, structured_ats: { ...schedule().acquisition.structured_ats, enabled: false, companies: ["Keep Co"], providers: ["old-provider"], all_resolved_sources: true }, agentic_web: { ...schedule().acquisition.agentic_web, enabled: true, country: "jp" } } });
    const { requests } = renderPage("/jobs/searches", {}, [value]); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles");
    expect(screen.getByLabelText("Remote policy")).toHaveValue("legacy_true"); expect(screen.getAllByText(/No remote restriction — legacy stored value preserved/).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText(/Agentic country/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Renamed" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1));
    expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[0].body).toEqual({ name: "Renamed" });
  });

  it("normalizes legacy remote true only after an explicit user change", async () => {
    const { requests } = renderPage(); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles");
    fireEvent.change(screen.getByLabelText("Remote policy"), { target: { value: "exclude_remote" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1));
    const patch = getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[0].body as any;
    expect(patch.query.remote_ok).toBe(false); expect(patch.query.companies).toEqual(["Hidden query company"]); expect(patch.query.max_results).toBe(73);
  });

  it("disabling and re-enabling Structured ATS preserves dormant filters, scope and bounds", async () => {
    const value = schedule({ acquisition: { ...schedule().acquisition, structured_ats: { ...schedule().acquisition.structured_ats, companies: ["OpenAI"], providers: ["legacy-provider", "greenhouse"], all_resolved_sources: false, max_sources: 27, max_results: 42 }, agentic_web: { ...schedule().acquisition.agentic_web, enabled: false, country: "jp" } } });
    const { requests } = renderPage("/jobs/searches", {}, [value]); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles");
    fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ })); fireEvent.click(screen.getByRole("checkbox", { name: /Profile-driven bounded server-side web discovery/ })); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1));
    const disabledPatch = getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[0].body as any;
    expect(disabledPatch.acquisition.structured_ats).toMatchObject({ enabled: false, companies: ["OpenAI"], providers: ["legacy-provider", "greenhouse"], all_resolved_sources: false, max_sources: 27, max_results: 42 });
    expect(disabledPatch.acquisition.agentic_web).toMatchObject({ enabled: true, country: "jp" });
    fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles"); fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ })); fireEvent.click(screen.getByRole("checkbox", { name: /Profile-driven bounded server-side web discovery/ })); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(2));
    const enabledPatch = getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[1].body as any;
    expect(enabledPatch.acquisition.structured_ats).toMatchObject({ enabled: true, companies: ["OpenAI"], providers: ["legacy-provider", "greenhouse"], all_resolved_sources: false, max_sources: 27, max_results: 42 });
    expect(enabledPatch.acquisition.agentic_web).toMatchObject({ enabled: false, country: "jp" });
  });

  it("preserves a mixed legacy ATS scope and unknown provider during an unrelated edit, then exposes explicit normalization", async () => {
    const mixed = schedule({ acquisition: { ...schedule().acquisition, structured_ats: { ...schedule().acquisition.structured_ats, all_resolved_sources: true, companies: ["OpenAI"], providers: ["retired-provider"] } } });
    const { requests } = renderPage("/jobs/searches", {}, [mixed]); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles");
    expect(screen.getByText("Legacy/mixed persisted source scope")).toBeInTheDocument(); expect(screen.getByText("Legacy persisted provider filters")).toBeInTheDocument(); expect(screen.getByText("retired-provider")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Mixed preserved" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1));
    expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[0].body).toEqual({ name: "Mixed preserved" });
    fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("Mixed preserved");
    fireEvent.click(screen.getByRole("button", { name: "Normalize to unfiltered resolved sources" }));
    expect(screen.queryByText("Legacy/mixed persisted source scope")).not.toBeInTheDocument();
  });

  it("preserves unknown provider values during unrelated channel edits and supports exact case-insensitive resolved-company copy", async () => {
    const { requests } = renderPage(); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles");
    expect(screen.getByText(/case\/punctuation normalization/)).toBeInTheDocument(); expect(screen.getByText(/not partial-text searches/)).toBeInTheDocument();
    expect(screen.getByText(/does not resolve a source/)).toBeInTheDocument(); expect(screen.getByText(/zero jobs without proving the employer has no open roles/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Maximum ATS results (1–100)"), { target: { value: "50" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1));
    expect((getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[0].body as any).acquisition.structured_ats).toMatchObject({ providers: ["greenhouse", "legacy-provider"], max_results: 50 });
  });

  it("preserves exact next-run and paused/run state semantics including missing next due time", async () => {
    const enabledNoDate = schedule({ id: "enabled-no-date", name: "Enabled no date", next_run_at: null });
    renderPage("/jobs/searches", {}, [enabledNoDate]); await screen.findByRole("heading", { name: "Enabled no date" });
    expect(screen.getByText(/Next due:.*Next due time is unavailable/)).toBeInTheDocument(); expect(screen.getByText(/Recurrence configured/)).toBeInTheDocument(); expect(screen.queryByText("Paused")).not.toBeInTheDocument();
  });

  it("falls back to labelled UTC when browser timezone formatting rejects a backend timezone", async () => {
    const original = Intl.DateTimeFormat;
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(((...args: ConstructorParameters<typeof Intl.DateTimeFormat>) => {
      const options = args[1]; if (options?.timeZone === "Unsupported/Browser") throw new RangeError("unsupported");
      return new original(...args);
    }) as typeof Intl.DateTimeFormat);
    renderPage("/jobs/searches", {}, [schedule({ schedule: { ...schedule().schedule, timezone: "Unsupported/Browser" } })]);
    expect(await screen.findByText(/UTC fallback; configured timezone: Unsupported\/Browser/)).toBeInTheDocument();
  });

  it("pauses and resumes with enabled-only PATCH and uses returned recurrence state", async () => {
    const { requests } = renderPage(); await loaded(); fireEvent.click(screen.getByRole("button", { name: "Pause recurrence" }));
    await screen.findByText(/Future recurrence paused/); expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[0].body).toEqual({ enabled: false });
    fireEvent.click(screen.getByRole("button", { name: "Resume recurrence" })); await screen.findByText(/Recurrence resumed/);
    expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")[1].body).toEqual({ enabled: true });
    expect(screen.getByText(/Next due:/)).toBeInTheDocument();
  });

  it("blocks Pause/Resume while dirty and duplicate mutation requests while pending", async () => {
    const pending = deferred<Response>(); const { requests } = renderPage("/jobs/searches", { "PATCH /api/v1/jobs/discovery-schedules/s-1": () => pending.promise }); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles"); fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Dirty" } });
    expect(screen.getByRole("button", { name: "Pause recurrence" })).toBeDisabled(); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled(); fireEvent.submit(screen.getByLabelText("Name").closest("form")!);
    expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1);
    pending.resolve(response(schedule({ name: "Dirty" }))); await screen.findByText("Saved discovery Dirty updated.");
  });

  it("blocks duplicate pause requests while one same-schedule mutation is pending", async () => {
    const pending = deferred<Response>(); const { requests } = renderPage("/jobs/searches", { "PATCH /api/v1/jobs/discovery-schedules/s-1": () => pending.promise }); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Pause recurrence" })); await screen.findByRole("button", { name: "Updating…" }); fireEvent.click(screen.getByRole("button", { name: "Updating…" }));
    expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1); pending.resolve(response(schedule({ enabled: false, next_run_at: null })));
    await screen.findByText(/Future recurrence paused/);
  });

  it("blocks duplicate create submissions and refreshes the authoritative backend list after POST", async () => {
    const pending = deferred<Response>(); let createdConfirmed = false; const existing = schedule({ id: "earlier", name: "Earlier backend item" }); const created = schedule({ id: "created", name: "Created", enabled: false });
    const { requests } = renderPage("/jobs/searches", { "GET /api/v1/jobs/discovery-schedules": () => response(createdConfirmed ? [existing, created] : []), "POST /api/v1/jobs/discovery-schedules": () => pending.promise }, []); await screen.findByText("No saved discovery configurations yet.");
    fireEvent.click(screen.getByRole("button", { name: "New saved discovery" })); fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Created" } }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } }); fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" })); expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled(); fireEvent.submit(screen.getByLabelText("Name").closest("form")!);
    expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(1);
    createdConfirmed = true; pending.resolve(response(created, 201)); await screen.findByText("Saved discovery created. The saved-configuration list is current.");
    expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules").length).toBeGreaterThan(1);
    expect(screen.getAllByRole("heading").filter((heading) => ["Earlier backend item", "Created"].includes(heading.textContent ?? "")).map((heading) => heading.textContent)).toEqual(["Earlier backend item", "Created"]);
  });

  it("keeps a newer editor when an earlier schedule save resolves late", async () => {
    const pendingPatch = deferred<Response>();
    const first = schedule({ id: "first", name: "Schedule A" });
    const second = schedule({ id: "second", name: "Schedule B" });
    const { requests } = renderPage("/jobs/searches", {
      "PATCH /api/v1/jobs/discovery-schedules/first": () => pendingPatch.promise,
    }, [first, second]);
    await loaded();

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await screen.findByDisplayValue("Schedule A");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Schedule A saved" } });
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/first")).toHaveLength(1));

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    await screen.findByDisplayValue("Schedule B");
    pendingPatch.resolve(response(schedule({ id: "first", name: "Schedule A saved" })));

    expect(await screen.findByDisplayValue("Schedule B")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Edit saved discovery" })).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Schedule A saved")).not.toBeInTheDocument();
  });

  it("does not let a delayed create close a newer editor", async () => {
    const pendingPost = deferred<Response>();
    const { requests } = renderPage("/jobs/searches", {
      "POST /api/v1/jobs/discovery-schedules": () => pendingPost.promise,
    });
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Pending create" } });
    fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await screen.findByDisplayValue("AI roles");
    pendingPost.resolve(response(schedule({ id: "created", name: "Pending create" }), 201));

    expect(await screen.findByDisplayValue("AI roles")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Edit saved discovery" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "New saved discovery" })).not.toBeInTheDocument();
  });

  it("reports creation success separately when authoritative list refresh fails and preserves prior list", async () => {
    let listCalls = 0; const { requests } = renderPage("/jobs/searches", {
      "GET /api/v1/jobs/discovery-schedules": () => ++listCalls === 1 ? response([schedule()]) : Promise.reject(new TypeError("offline")),
    }); await loaded(); fireEvent.click(screen.getByRole("button", { name: "New saved discovery" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New" } }); fireEvent.change(screen.getByLabelText("Prioritisation themes (one per line)"), { target: { value: "AI" } }); fireEvent.click(screen.getByRole("checkbox", { name: /Structured ATS/ })); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByText(/was created, but the list refresh could not be confirmed/)).toBeInTheDocument(); expect(screen.getByRole("heading", { name: "AI roles" })).toBeInTheDocument();
    expect(getCalls(requests, "POST", "/api/v1/jobs/discovery-schedules")).toHaveLength(1); expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules")).toHaveLength(2);
  });

  it("clears a stale editor and refreshes the list after exact GET 404", async () => {
    const { requests } = renderPage("/jobs/searches", { "GET /api/v1/jobs/discovery-schedules/s-1": () => response({ detail: "Not found" }, 404) }); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Edit" })); expect(await screen.findByText(/no longer available/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Edit saved discovery" })).not.toBeInTheDocument(); expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules").length).toBeGreaterThan(1);
  });

  it("clears a stale editor and refreshes the list after PATCH 404", async () => {
    const { requests } = renderPage("/jobs/searches", { "PATCH /api/v1/jobs/discovery-schedules/s-1": () => response({ detail: "Not found" }, 404) }); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Edit" })); await screen.findByDisplayValue("AI roles"); fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Renamed" } }); fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByText(/no longer available/)).toBeInTheDocument(); expect(screen.queryByRole("heading", { name: "Edit saved discovery" })).not.toBeInTheDocument(); expect(getCalls(requests, "PATCH", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1);
  });

  it("clears stale pause selection and refreshes after pause/resume 404", async () => {
    const { requests } = renderPage("/jobs/searches", { "PATCH /api/v1/jobs/discovery-schedules/s-1": () => response({ detail: "Not found" }, 404) }); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Pause recurrence" })); expect(await screen.findByText(/no longer available/)).toBeInTheDocument(); expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules").length).toBeGreaterThan(1);
  });

  it("clears a stale editor when resuming a deleted schedule returns 404", async () => {
    const { requests } = renderPage("/jobs/searches", { "PATCH /api/v1/jobs/discovery-schedules/s-1": () => response({ detail: "Not found" }, 404) }, [schedule({ enabled: false, next_run_at: null })]); await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Resume recurrence" })); expect(await screen.findByText(/no longer available/)).toBeInTheDocument();
    expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules").length).toBeGreaterThan(1);
  });

  it("ignores older delayed list responses after a newer refresh", async () => {
    const old = deferred<Response>(); let calls = 0;
    const { requests } = renderPage("/jobs/searches", { "GET /api/v1/jobs/discovery-schedules": () => ++calls === 1 ? old.promise : response([schedule({ id: "new", name: "Newest" })]) }, []);
    fireEvent.click(await screen.findByRole("button", { name: "Refresh list" })); expect(await screen.findByRole("heading", { name: "Newest" })).toBeInTheDocument(); old.resolve(response([schedule({ id: "old", name: "Stale result" })]));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Stale result" })).not.toBeInTheDocument()); expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules")).toHaveLength(2);
  });

  it("ignores an older delayed exact edit response after a newer schedule selection", async () => {
    const old = deferred<Response>(); const second = schedule({ id: "s-2", name: "Second" });
    const { requests } = renderPage("/jobs/searches", { "GET /api/v1/jobs/discovery-schedules/s-1": () => old.promise, "GET /api/v1/jobs/discovery-schedules/s-2": () => response(second) }, [schedule(), second]); await loaded();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]); fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[1]); await screen.findByDisplayValue("Second"); old.resolve(response(schedule({ name: "Stale editor" })));
    await waitFor(() => expect(screen.queryByDisplayValue("Stale editor")).not.toBeInTheDocument()); expect(screen.getByDisplayValue("Second")).toBeInTheDocument(); expect(getCalls(requests, "GET", "/api/v1/jobs/discovery-schedules/s-1")).toHaveLength(1);
  });

  it("uses the shared SessionApi 401 session-clear behavior", async () => {
    renderPage("/jobs/searches", { "GET /api/v1/jobs/discovery-schedules": () => response({ detail: "Unauthorized" }, 401) });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument(); expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it("requires no readiness fetch and exposes no run-now, execution-history, or deletion controls", async () => {
    const { requests } = renderPage(); await loaded();
    expect(requests.some((item) => item.path.includes("onboarding") || item.path.endsWith("/executions") || item.path.endsWith("/run-now"))).toBe(false);
    expect(screen.queryByRole("button", { name: /Run now|Execution history|Delete|Archive/i })).not.toBeInTheDocument();
  });
});

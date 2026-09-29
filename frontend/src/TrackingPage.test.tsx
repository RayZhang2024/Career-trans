import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Link, MemoryRouter } from "react-router-dom";
import type { ApplicationPreparation, ApplicationPreparationReview, ApplicationTracking, ApplicationTrackingListItem, User } from "./api";
import { App } from "./App";
import { AuthProvider, useAuth } from "./auth";

const TOKEN = "career-trans.access-token";
const user: User = { id: "owner", email: "owner@example.test", created_at: "2026-01-01T00:00:00Z" };
const statuses = ["prepared", "applied", "interview", "rejected", "offer", "withdrawn"] as const;
const preparation = (id = "prep-1"): ApplicationPreparation => ({
  id, target: { source_kind: "discovered_job", public_url: "https://example.test/old", title: "Historical Engineer", company: "Snapshot Co", location: "London", work_arrangement: null, employment_type: null, job_content_hash: "hash", job_profile: { requirements: [], responsibilities: [], technical_skills: [], domain_knowledge: [], security_requirements: [], work_authorization_requirements: [] }, requirement_matches: [] },
  identity: { display_name: "Synthetic", email: "synthetic@example.test" }, preparation_input_fingerprint: "a", preparation_contract_fingerprint: "b",
  result: { cv: { professional_summary: "Saved summary", summary_source_refs: [], key_skills: [], roles: [], selected_projects: [], education: [], credentials: [] }, cover_letter: null, answers: [], layout_status: "fit", target_pages: 2, actual_pdf_pages: 2 }, runtime_attribution: { status: "legacy_unavailable", provider: null, operations: {} }, created_at: "2026-01-01T00:00:00Z",
});
const event = (revision: number, to_status: ApplicationTracking["current_status"], from_status: ApplicationTracking["current_status"] | null) => ({ revision, from_status, to_status, recorded_at: `2026-01-0${revision}T12:00:00Z` });
const tracking = (id = "track-1", current_status: ApplicationTracking["current_status"] = "prepared", revision = 1): ApplicationTracking => ({
  id, preparation_id: "prep-1", target: { preparation_id: "prep-1", preparation_created_at: "2026-01-01T00:00:00Z", source_kind: "discovered_job", title: "Historical Engineer", company: "Snapshot Co", location: "London", public_url: "https://example.test/old" },
  current_status, revision, created_at: "2026-01-02T12:00:00Z", updated_at: `2026-01-0${revision + 1}T12:00:00Z`,
  events: Array.from({ length: revision }, (_, index) => event(index + 1, index === revision - 1 ? current_status : statuses[index % statuses.length], index === 0 ? null : statuses[(index - 1) % statuses.length])),
});
const item = (id: string, title: string, status: ApplicationTracking["current_status"], updated_at: string): ApplicationTrackingListItem => ({ ...tracking(id, status), target: { ...tracking(id, status).target, title }, updated_at });
const review: ApplicationPreparationReview = { preparation_id: "prep-1", evidence_snapshot_status: "available", evidence_sources: [] };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>;
function fetcher(overrides: Record<string, Handler> = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const method = init?.method ?? "GET";
    const handler = overrides[`${method} ${url.pathname}`] ?? overrides[url.pathname];
    if (handler) return Promise.resolve(handler(url, init));
    if (url.pathname === "/api/v1/users/me") return Promise.resolve(json(user));
    if (url.pathname === "/api/v1/applications" && method === "GET") return Promise.resolve(json([preparation()]));
    if (/^\/api\/v1\/applications\/[^/]+\/review$/.test(url.pathname)) return Promise.resolve(json(review));
    if (/^\/api\/v1\/applications\/[^/]+$/.test(url.pathname) && method === "GET") return Promise.resolve(json(preparation()));
    if (url.pathname === "/api/v1/application-tracking" && method === "GET") return Promise.resolve(json([]));
    if (url.pathname.startsWith("/api/v1/application-tracking/by-preparation/")) return Promise.resolve(json({ detail: "not found" }, 404));
    if (url.pathname.startsWith("/api/v1/application-tracking/") && method === "GET") return Promise.resolve(json(tracking(decodeURIComponent(url.pathname.split("/").at(-1)!))));
    if (url.pathname.endsWith("status-events") && method === "POST") return Promise.resolve(json({ detail: "not found" }, 404));
    if (url.pathname === "/api/v1/expire" ) return Promise.resolve(json({ detail: "unauthorized" }, 401));
    throw new Error(`Unexpected ${method} ${url.pathname}`);
  });
}
function renderApp(fetch = fetcher(), path = "/tracking") {
  sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
  return { ...render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>), fetch };
}
function paths(fetch: ReturnType<typeof fetcher>) { return fetch.mock.calls.map(([input]) => new URL(String(input), window.location.origin).pathname); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
function Navigation() { const { api } = useAuth(); return <nav><Link to="/tracking/track-A">A</Link><Link to="/tracking/track-B">B</Link><Link to="/applications/prep-1">Preparation</Link><Link to="/applications/prep-A">Preparation A</Link><Link to="/applications/prep-B">Preparation B</Link><button onClick={() => void api.request("/api/v1/expire").catch(() => {})}>Expire session</button></nav>; }

beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });
afterEach(cleanup);

describe("Issue #184 application tracking workspace", () => {
  it("loads only when opened, preserves API ordering and does not fetch review evidence", async () => {
    const fetch = fetcher({ "/api/v1/application-tracking": () => json([item("t-new", "Newest role", "offer", "2026-02-02"), item("t-old", "Older role", "applied", "2026-01-01")]) });
    renderApp(fetch);
    expect(await screen.findByRole("heading", { name: "Newest role" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Older role" })).toBeInTheDocument();
    const historyLinks = screen.getAllByRole("link", { name: /Open tracking history for/ });
    expect(historyLinks).toHaveLength(2);
    expect(new Set(historyLinks.map((link) => link.getAttribute("aria-label"))).size).toBe(2);
    const rendered = document.body.textContent ?? "";
    expect(rendered.indexOf("Newest role")).toBeLessThan(rendered.indexOf("Older role"));
    expect(screen.getByRole("link", { name: "Tracking" })).toHaveAttribute("href", "/tracking");
    expect(paths(fetch).filter((path) => path.endsWith("/review"))).toHaveLength(0);
    expect(paths(fetch)).not.toContain("/api/v1/profile");
  });

  it("ignores an older list refresh that resolves after a newer refresh", async () => {
    const older = deferred<Response>(); let gets = 0;
    const fetch = fetcher({
      "/api/v1/application-tracking": () => {
        gets += 1;
        if (gets === 1) return older.promise;
        return json([item("fresh", "Fresh role", "offer", "2026-03-01")]);
      },
    });
    renderApp(fetch);
    await waitFor(() => expect(gets).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("heading", { name: "Fresh role" })).toBeInTheDocument();
    older.resolve(json([item("stale", "Stale role", "applied", "2026-01-01")]));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Stale role" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Fresh role" })).toBeInTheDocument();
  });

  it("shows the historical target and revision-ordered timeline with recorded-time wording", async () => {
    const value = { ...tracking("track-1", "interview", 2), events: [event(2, "interview", "applied"), event(1, "applied", null)] };
    const fetch = fetcher({ "/api/v1/application-tracking/track-1": () => json(value) });
    renderApp(fetch, "/tracking/track-1");
    expect(await screen.findByRole("heading", { name: "Historical Engineer" })).toBeInTheDocument();
    expect(screen.getByText("Snapshot Co · London")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Interview");
    const timeline = screen.getByRole("heading", { name: "Status history" }).parentElement!;
    const entries = within(timeline).getAllByRole("listitem");
    expect(entries[0]).toHaveTextContent("Applied"); expect(entries[1]).toHaveTextContent("Interview");
    expect(within(timeline).getByText(/Recorded time is when this status was saved/)).toBeInTheDocument();
    expect(paths(fetch)).not.toContain("/api/v1/applications/prep-1/review");
  });

  it("loads one global tracking projection for Applications history", async () => {
    const fetch = fetcher(); renderApp(fetch, "/applications");
    expect(await screen.findByRole("heading", { name: "Applications" })).toBeInTheDocument();
    await waitFor(() => expect(paths(fetch)).toContain("/api/v1/applications"));
    expect(paths(fetch).filter((path) => path === "/api/v1/application-tracking")).toHaveLength(1);
  });

  it("waits for preparation ownership before looking up tracking and leaves V2C2 content visible on tracking failure", async () => {
    const oldDetail = deferred<Response>();
    const fetch = fetcher({
      "/api/v1/applications/prep-1": () => oldDetail.promise,
      "/api/v1/application-tracking/by-preparation/prep-1": () => json({ detail: "temporarily unavailable" }, 503),
    });
    renderApp(fetch, "/applications/prep-1");
    expect(paths(fetch)).not.toContain("/api/v1/application-tracking/by-preparation/prep-1");
    oldDetail.resolve(json(preparation()));
    expect(await screen.findByText("Saved summary")).toBeInTheDocument();
    expect(await screen.findByText(/Tracking information is temporarily unavailable/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Preparation review summary" })).toBeInTheDocument();
    expect(paths(fetch)).toContain("/api/v1/application-tracking/by-preparation/prep-1");
  });

  it("offers all six initial statuses, defaults to Prepared, and sends the explicit user choice", async () => {
    const body: Array<Record<string, unknown>> = [];
    const fetch = fetcher({ "POST /api/v1/application-tracking": (_url, init) => { body.push(JSON.parse(String(init?.body))); return json(tracking("created", "interview"), 201); } });
    renderApp(fetch, "/applications/prep-1");
    const select = await screen.findByLabelText("Initial recorded status");
    expect(select).toHaveValue("prepared");
    expect(Array.from((select as HTMLSelectElement).options).map((option) => option.value)).toEqual(statuses);
    expect(screen.getByText(/does not submit or update anything with the employer/)).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Start tracking" }));
    expect(await screen.findByText(/Current recorded status:/)).toHaveTextContent("Interview");
    expect(body).toEqual([{ preparation_id: "prep-1", status: "interview" }]);
  });

  it("shows an existing tracking record and does not assume an untracked result owns a preparation", async () => {
    const fetch = fetcher({ "/api/v1/application-tracking/by-preparation/prep-1": () => json(tracking("existing", "applied")) });
    renderApp(fetch, "/applications/prep-1");
    expect(await screen.findByText(/Current recorded status:/)).toHaveTextContent("Applied");
    expect(screen.getByRole("link", { name: "Open tracking history" })).toHaveAttribute("href", "/tracking/existing");
    expect(screen.queryByRole("button", { name: "Start tracking" })).not.toBeInTheDocument();
  });

  it("reconciles duplicate-create conflict without replay and makes no causal success claim", async () => {
    let posts = 0; let lookupCalls = 0;
    const fetch = fetcher({
      "POST /api/v1/application-tracking": () => { posts += 1; return json({ detail: "private conflict text" }, 409); },
      "/api/v1/application-tracking/by-preparation/prep-1": () => ++lookupCalls === 1 ? json({ detail: "untracked" }, 404) : json(tracking("existing", "applied")),
    });
    renderApp(fetch, "/applications/prep-1");
    fireEvent.click(await screen.findByRole("button", { name: "Start tracking" }));
    expect(await screen.findByText(/currently recorded state is shown if available/)).toBeInTheDocument();
    expect(await screen.findByText(/Current recorded status:/)).toHaveTextContent("Applied");
    expect(posts).toBe(1);
    expect(screen.queryByText(/private conflict text|successfully started/)).not.toBeInTheDocument();
  });

  it("reconciles interrupted creation without retry, preserves selection and permits explicit retry when absent", async () => {
    let posts = 0; let lookups = 0;
    const fetch = fetcher({
      "POST /api/v1/application-tracking": () => { posts += 1; return Promise.reject(new TypeError("offline")); },
      "/api/v1/application-tracking/by-preparation/prep-1": () => { lookups += 1; return lookups === 1 ? json({ detail: "not found" }, 404) : json({ detail: "temporarily unavailable" }, 503); },
    });
    renderApp(fetch, "/applications/prep-1");
    const select = await screen.findByLabelText("Initial recorded status");
    fireEvent.change(select, { target: { value: "offer" } });
    fireEvent.click(screen.getByRole("button", { name: "Start tracking" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/current saved tracking could not be confirmed/);
    expect(screen.getByLabelText("Initial recorded status")).toHaveValue("offer");
    expect(posts).toBe(1); expect(lookups).toBe(2); // initial authority load + one reconciliation GET
    fireEvent.click(screen.getByRole("button", { name: "Start tracking" }));
    await waitFor(() => expect(posts).toBe(2));
  });

  it("confirms interrupted creation from the exact preparation lookup without claiming causality", async () => {
    let posts = 0; let lookups = 0;
    const fetch = fetcher({
      "POST /api/v1/application-tracking": () => { posts += 1; return Promise.reject(new TypeError("offline")); },
      "/api/v1/application-tracking/by-preparation/prep-1": () => { lookups += 1; return lookups === 1 ? json({ detail: "not found" }, 404) : json(tracking("existing", "offer")); },
    });
    renderApp(fetch, "/applications/prep-1");
    fireEvent.click(await screen.findByRole("button", { name: "Start tracking" }));
    expect(await screen.findByText(/Current recorded status:/)).toHaveTextContent("Offer");
    expect(await screen.findByRole("status")).toHaveTextContent(/currently saved tracking state was confirmed/);
    expect(posts).toBe(1); expect(lookups).toBe(2);
    expect(screen.queryByText(/successfully started/)).not.toBeInTheDocument();
  });

  it("does not reconcile a tracking record returned for a different preparation", async () => {
    let lookups = 0;
    const fetch = fetcher({
      "POST /api/v1/application-tracking": () => Promise.reject(new TypeError("offline")),
      "/api/v1/application-tracking/by-preparation/prep-1": () => { lookups += 1; return lookups === 1 ? json({ detail: "not found" }, 404) : json({ ...tracking("wrong"), preparation_id: "other-preparation" }); },
    });
    renderApp(fetch, "/applications/prep-1");
    fireEvent.click(await screen.findByRole("button", { name: "Start tracking" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/current saved tracking could not be confirmed/);
    expect(screen.queryByText(/Current recorded status:/)).not.toBeInTheDocument();
    expect(lookups).toBe(2);
  });

  it("sends displayed expected revision and accepts successful full detail", async () => {
    let requestBody: unknown;
    const fetch = fetcher({
      "/api/v1/application-tracking/track-1": () => json(tracking("track-1", "applied", 2)),
      "POST /api/v1/application-tracking/track-1/status-events": (_url, init) => { requestBody = JSON.parse(String(init?.body)); return json(tracking("track-1", "interview", 3)); },
    });
    renderApp(fetch, "/tracking/track-1");
    const select = await screen.findByLabelText("Record another status");
    expect(Array.from((select as HTMLSelectElement).options).map((option) => option.value)).not.toContain("applied");
    fireEvent.change(select, { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Interview");
    expect(requestBody).toEqual({ status: "interview", expected_revision: 2 });
  });

  it("unifies any status-event 409 into one detail refresh, ignores its body, and never retries POST", async () => {
    let posts = 0; let gets = 0;
    const fetch = fetcher({
      "/api/v1/application-tracking/track-1": () => { gets += 1; return json(tracking("track-1", gets === 1 ? "applied" : "rejected", gets === 1 ? 1 : 2)); },
      "POST /api/v1/application-tracking/track-1/status-events": () => { posts += 1; return json({ detail: "same-status and stale-revision are secret variants" }, 409); },
    });
    renderApp(fetch, "/tracking/track-1");
    fireEvent.change(await screen.findByLabelText("Record another status"), { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    expect(await screen.findByText(/changed before your update could be applied/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Rejected");
    expect(posts).toBe(1); expect(gets).toBe(2);
    expect(screen.queryByText(/secret variants/)).not.toBeInTheDocument();
  });

  it("does not let an older conflict reconciliation overwrite a newer detail refresh", async () => {
    const olderReconciliation = deferred<Response>(); let gets = 0; let posts = 0;
    const fetch = fetcher({
      "/api/v1/application-tracking/track-1": () => {
        gets += 1;
        if (gets === 1) return json(tracking("track-1", "applied", 1));
        if (gets === 2) return olderReconciliation.promise;
        return json(tracking("track-1", "offer", 3));
      },
      "POST /api/v1/application-tracking/track-1/status-events": () => {
        posts += 1;
        return posts === 1 ? json({ detail: "conflict" }, 409) : json(tracking("track-1", "interview", 4));
      },
    });
    renderApp(fetch, "/tracking/track-1");
    fireEvent.change(await screen.findByLabelText("Record another status"), { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    await waitFor(() => expect(gets).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: "Refresh tracking" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Offer · Revision 3"));
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Interview · Revision 4"));
    olderReconciliation.resolve(json(tracking("track-1", "rejected", 2)));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Interview · Revision 4"));
    expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).not.toHaveTextContent("Rejected · Revision 2");
    expect(posts).toBe(2);
  });

  it("keeps the accepted detail when an update response carries a lower revision", async () => {
    const fetch = fetcher({
      "/api/v1/application-tracking/track-1": () => json(tracking("track-1", "offer", 3)),
      "POST /api/v1/application-tracking/track-1/status-events": () => json(tracking("track-1", "rejected", 2)),
    });
    renderApp(fetch, "/tracking/track-1");
    fireEvent.change(await screen.findByLabelText("Record another status"), { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    await waitFor(() => expect(paths(fetch).some((path) => path.endsWith("status-events"))).toBe(true));
    expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Offer · Revision 3");
    expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).not.toHaveTextContent("Rejected · Revision 2");
  });

  it("reconciles transport failure once without replay or falsely claiming desired status succeeded", async () => {
    let posts = 0; let gets = 0;
    const fetch = fetcher({
      "/api/v1/application-tracking/track-1": () => { gets += 1; return json(tracking("track-1", gets === 1 ? "applied" : "interview", gets === 1 ? 1 : 2)); },
      "POST /api/v1/application-tracking/track-1/status-events": () => { posts += 1; return Promise.reject(new TypeError("offline")); },
    });
    renderApp(fetch, "/tracking/track-1");
    fireEvent.change(await screen.findByLabelText("Record another status"), { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    expect(await screen.findByText(/cause cannot be confirmed/)).toBeInTheDocument();
    expect(posts).toBe(1); expect(gets).toBe(2);
    expect(screen.queryByText(/update succeeded|successfully recorded/)).not.toBeInTheDocument();
  });

  it("blocks duplicate local status submissions while a request is pending", async () => {
    const pending = deferred<Response>(); let posts = 0;
    const fetch = fetcher({ "/api/v1/application-tracking/track-1": () => json(tracking()), "POST /api/v1/application-tracking/track-1/status-events": () => { posts += 1; return pending.promise; } });
    renderApp(fetch, "/tracking/track-1");
    fireEvent.change(await screen.findByLabelText("Record another status"), { target: { value: "applied" } });
    const save = screen.getByRole("button", { name: "Save status" });
    fireEvent.click(save); fireEvent.click(save);
    await waitFor(() => expect(posts).toBe(1));
    expect(save).toBeDisabled();
    pending.resolve(json(tracking("track-1", "applied", 2)));
    expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Applied");
  });

  it("guards refresh during a pending status write and unlocks for the next update", async () => {
    const firstPost = deferred<Response>(); let posts = 0; let gets = 0;
    const fetch = fetcher({
      "/api/v1/application-tracking/track-1": () => { gets += 1; return json(tracking("track-1", gets === 1 ? "applied" : "interview", gets === 1 ? 1 : 2)); },
      "POST /api/v1/application-tracking/track-1/status-events": () => {
        posts += 1;
        return posts === 1 ? firstPost.promise : json(tracking("track-1", "offer", 3));
      },
    });
    renderApp(fetch, "/tracking/track-1");
    fireEvent.change(await screen.findByLabelText("Record another status"), { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    await waitFor(() => expect(posts).toBe(1));

    const refresh = screen.getByRole("button", { name: "Refresh tracking" });
    expect(refresh).toBeDisabled();
    fireEvent.click(refresh);
    expect(gets).toBe(1);
    expect(screen.getByRole("button", { name: "Saving status…" })).toBeDisabled();

    firstPost.resolve(json(tracking("track-1", "interview", 2)));
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh tracking" })).toBeEnabled());
    expect(screen.queryByRole("button", { name: "Saving status…" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Record another status"), { target: { value: "offer" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Offer · Revision 3"));
    expect(posts).toBe(2);
  });

  it("resets tracking write state on A-to-B navigation and ignores A's delayed update", async () => {
    const delayedUpdate = deferred<Response>(); let postsA = 0; let postsB = 0;
    const fetch = fetcher({
      "/api/v1/application-tracking/track-A": () => json(tracking("track-A", "applied", 1)),
      "/api/v1/application-tracking/track-B": () => json({ ...tracking("track-B", "offer", 4), preparation_id: "prep-B", target: { ...tracking().target, preparation_id: "prep-B", title: "Role B" } }),
      "POST /api/v1/application-tracking/track-A/status-events": () => { postsA += 1; return delayedUpdate.promise; },
      "POST /api/v1/application-tracking/track-B/status-events": () => { postsB += 1; return json({ ...tracking("track-B", "interview", 5), preparation_id: "prep-B", target: { ...tracking().target, preparation_id: "prep-B", title: "Role B" } }); },
    });
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/tracking/track-A"]}><AuthProvider><Navigation /><App /></AuthProvider></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("Record another status"), { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    await waitFor(() => expect(postsA).toBe(1));
    expect(screen.getByRole("button", { name: "Saving status…" })).toBeDisabled();

    fireEvent.click(screen.getByRole("link", { name: "B" }));
    expect(await screen.findByRole("heading", { name: "Role B" })).toBeInTheDocument();
    const selectB = screen.getByLabelText("Record another status");
    expect(selectB).toHaveValue("");
    expect(selectB).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save status" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Saving status…" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh tracking" })).toBeEnabled();

    delayedUpdate.resolve(json(tracking("track-A", "interview", 2)));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Role B" })).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Offer · Revision 4");
    expect(screen.queryByText("Interview · Revision 2")).not.toBeInTheDocument();

    fireEvent.change(selectB, { target: { value: "applied" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Current recorded status" }).parentElement).toHaveTextContent("Interview · Revision 5"));
    expect(postsA).toBe(1); expect(postsB).toBe(1);
  });

  it("resets the selected initial status and pending start state when preparation changes", async () => {
    const delayedStart = deferred<Response>(); let startBody: unknown;
    const fetch = fetcher({
      "/api/v1/applications/prep-A": () => json(preparation("prep-A")),
      "/api/v1/applications/prep-B": () => json(preparation("prep-B")),
      "/api/v1/application-tracking/by-preparation/prep-A": () => json({ detail: "not found" }, 404),
      "/api/v1/application-tracking/by-preparation/prep-B": () => json({ detail: "not found" }, 404),
      "POST /api/v1/application-tracking": (_url, init) => { startBody = JSON.parse(String(init?.body)); return delayedStart.promise; },
    });
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/applications/prep-A"]}><AuthProvider><Navigation /><App /></AuthProvider></MemoryRouter>);
    const selectA = await screen.findByLabelText("Initial recorded status");
    fireEvent.change(selectA, { target: { value: "offer" } });
    fireEvent.click(screen.getByRole("button", { name: "Start tracking" }));
    await waitFor(() => expect(startBody).toEqual({ preparation_id: "prep-A", status: "offer" }));
    expect(screen.getByRole("button", { name: "Starting tracking…" })).toBeDisabled();

    fireEvent.click(screen.getByRole("link", { name: "Preparation B" }));
    const selectB = await screen.findByLabelText("Initial recorded status");
    expect(selectB).toHaveValue("prepared");
    expect(selectB).toBeEnabled();
    expect(screen.getByRole("button", { name: "Start tracking" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Starting tracking…" })).not.toBeInTheDocument();

    delayedStart.resolve(json({ ...tracking("track-A", "offer"), preparation_id: "prep-A" }));
    await waitFor(() => expect(screen.getByLabelText("Initial recorded status")).toHaveValue("prepared"));
    expect(screen.queryByText(/Current recorded status:/)).not.toBeInTheDocument();
  });

  it("does not allow an older accepted revision or delayed detail to replace newer selected state", async () => {
    const first = deferred<Response>(); let calls = 0;
    const fetch = fetcher({
      "/api/v1/application-tracking/track-A": () => ++calls === 1 ? first.promise : json(tracking("track-A", "offer", 5)),
      "/api/v1/application-tracking/track-B": () => json({ ...tracking("track-B", "rejected", 2), preparation_id: "prep-B", target: { ...tracking().target, preparation_id: "prep-B", title: "Role B" } }),
    });
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/tracking/track-A"]}><AuthProvider><Navigation /><App /></AuthProvider></MemoryRouter>);
    await waitFor(() => expect(paths(fetch)).toContain("/api/v1/application-tracking/track-A"));
    fireEvent.click(screen.getByRole("link", { name: "B" }));
    expect(await screen.findByRole("heading", { name: "Role B" })).toBeInTheDocument();
    first.resolve(json(tracking("track-A", "prepared", 1)));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Role B" })).toBeInTheDocument());
  });

  it("ignores a delayed status update after navigating to another tracking record", async () => {
    const delayedUpdate = deferred<Response>();
    const fetch = fetcher({
      "/api/v1/application-tracking/track-A": () => json(tracking("track-A", "applied", 1)),
      "/api/v1/application-tracking/track-B": () => json({ ...tracking("track-B", "offer", 4), preparation_id: "prep-B", target: { ...tracking().target, preparation_id: "prep-B", title: "Role B" } }),
      "POST /api/v1/application-tracking/track-A/status-events": () => delayedUpdate.promise,
    });
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/tracking/track-A"]}><AuthProvider><Navigation /><App /></AuthProvider></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("Record another status"), { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    await waitFor(() => expect(paths(fetch).some((path) => path.endsWith("status-events"))).toBe(true));
    fireEvent.click(screen.getByRole("link", { name: "B" }));
    expect(await screen.findByRole("heading", { name: "Role B" })).toBeInTheDocument();
    delayedUpdate.resolve(json(tracking("track-A", "interview", 2)));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Role B" })).toBeInTheDocument());
    expect(screen.queryByText("Interview · Revision 2")).not.toBeInTheDocument();
  });

  it("uses revision monotonicity for update responses and keeps old-session state isolated", async () => {
    const delayed = deferred<Response>();
    const fetch = fetcher({
      "/api/v1/application-tracking/track-1": () => json(tracking("track-1", "applied", 2)),
      "POST /api/v1/application-tracking/track-1/status-events": () => delayed.promise,
      "/api/v1/expire": () => json({ detail: "no" }, 401),
    });
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/tracking/track-1"]}><AuthProvider><Navigation /><App /></AuthProvider></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText("Record another status"), { target: { value: "interview" } });
    fireEvent.click(screen.getByRole("button", { name: "Save status" }));
    await waitFor(() => expect(paths(fetch).some((path) => path.endsWith("status-events"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Expire session" }));
    delayed.resolve(json(tracking("track-1", "interview", 3)));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "Historical Engineer" })).not.toBeInTheDocument();
  });

  it("removes stale edit controls after tracking authority returns 404", async () => {
    const fetch = fetcher({ "/api/v1/application-tracking/secret": () => json({ detail: "not found" }, 404) });
    renderApp(fetch, "/tracking/secret");
    expect(await screen.findByRole("alert")).toHaveTextContent(/not available to this account/);
    expect(screen.queryByLabelText("Record another status")).not.toBeInTheDocument();
  });
});

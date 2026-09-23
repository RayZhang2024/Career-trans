import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Link, MemoryRouter } from "react-router-dom";
import type { ApplicationPreparation, User } from "./api";
import { App } from "./App";
import { AuthProvider } from "./auth";

const TOKEN = "career-trans.access-token";
const user: User = { id: "owner", email: "owner@example.test", created_at: "2026-01-01T00:00:00Z" };
const ref = { source_type: "career_evidence", source_ref: "evidence:opaque-123" };
const preparation = (id: string, title = "Applied AI Engineer", cover = true): ApplicationPreparation => ({
  id,
  target: { source_kind: "discovered_job", canonical_discovered_job_id: "job-canonical", public_url: "https://jobs.example.test/current", title, company: "Example Co", location: "London", work_arrangement: "Hybrid", employment_type: "Full-time", job_content_hash: "hash", job_profile: { title, company: "Example Co", location: "London", work_arrangement: "Hybrid", seniority: null, salary: null, employment_type: "Full-time", application_deadline: null, responsibilities: [], requirements: [], technical_skills: [], domain_knowledge: [], security_requirements: [], work_authorization_requirements: [] } },
  identity: { display_name: "Historic Name", email: "historic@example.test", phone: "020 0000", location: "London", linkedin_url: "https://linkedin.example.test/person", github_url: null, portfolio_url: null },
  preparation_input_fingerprint: "input", preparation_contract_fingerprint: "contract",
  result: { cv: { professional_summary: "Evidence-grounded summary.", summary_source_refs: [ref], key_skills: ["Python"], roles: [{ employer: "Example Ltd", title: "Engineer", start_date: "2020", end_date: "2024", location: "London", bullets: [{ text: "Delivered a useful system.", source_refs: [ref], priority: 80 }] }], selected_projects: [{ name: "Project A", text: "Built a system.", source_refs: [{ source_type: "project", source_ref: "project:0" }], priority: 60 }], education: ["MSc, Example University"], credentials: ["Cloud certification"] }, cover_letter: cover ? { body: "Dear team,\n\nI am interested in the role.", source_refs: [ref] } : null, answers: [{ question: "Describe your experience.", status: "drafted", answer: "I delivered a useful system.", source_refs: [ref] }, { question: "Do you have a clearance?", status: "unsupported", answer: null, source_refs: [] }], layout_status: "fit", target_pages: 2, actual_pdf_pages: 2 },
  created_at: "2026-03-01T12:00:00Z",
});
const json = (body: unknown, status = 200, headers?: Record<string, string>) => new Response(JSON.stringify(body), { status, headers });
type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>;
function fetcher(overrides: Record<string, Handler> = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const path = url.pathname;
    const method = init?.method ?? "GET";
    const selected = overrides[`${method} ${path}`] ?? overrides[path];
    if (selected) return Promise.resolve(selected(url, init));
    if (path === "/api/v1/users/me") return Promise.resolve(json(user));
    if (path === "/api/v1/applications" && method === "GET") return Promise.resolve(json([preparation("p-1")]));
    if (path === "/api/v1/applications/p-1") return Promise.resolve(json(preparation("p-1")));
    if (path.endsWith(".docx")) return Promise.resolve(new Response(new Blob(["doc"]), { status: 200, headers: { "Content-Disposition": 'attachment; filename="Historic_CV.docx"' } }));
    if (path.endsWith(".pdf")) return Promise.resolve(new Response(new Blob(["pdf"]), { status: 200, headers: { "Content-Disposition": "attachment; filename*=UTF-8''Historic_CV.pdf" } }));
    throw new Error(`Unexpected request ${method} ${path}`);
  });
}
function renderApp(fetch = fetcher(), path = "/applications") {
  sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
  return { ...render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>), fetch };
}
function requestPaths(fetch: ReturnType<typeof fetcher>) { return fetch.mock.calls.map(([input]) => new URL(String(input), window.location.origin).pathname); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
function TestNavigation() { return <nav><Link to="/applications/p-A">Open A</Link><Link to="/applications/p-B">Open B</Link></nav>; }

beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });
afterEach(cleanup);

describe("Issue #180 Applications workspace", () => {
  it("loads history only when opened and preserves the backend order with summary fields", async () => {
    const fetch = fetcher({ "/api/v1/applications": () => json([preparation("newest", "Newest"), preparation("older", "Older", false)]) });
    renderApp(fetch);
    expect(await screen.findByRole("heading", { name: "Newest" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Applications" })).toHaveAttribute("href", "/applications");
    expect(screen.getAllByText(/Fits target/)).toHaveLength(2);
    expect(screen.getByText("Included")).toBeInTheDocument();
    expect(screen.getByText("Not included")).toBeInTheDocument();
    expect(requestPaths(fetch).filter((path) => path === "/api/v1/applications")).toHaveLength(1);
  });

  it("supports direct deep links and renders immutable target, identity, factual content and provenance", async () => {
    renderApp(fetcher(), "/applications/p-1");
    expect(await screen.findByRole("heading", { name: "Applied AI Engineer" })).toBeInTheDocument();
    expect(screen.getByText(/saved snapshot created/)).toBeInTheDocument();
    expect(screen.getByText("Historic Name")).toBeInTheDocument();
    expect(screen.getByText("historic@example.test")).toBeInTheDocument();
    expect(screen.getByText("Evidence-grounded summary.")).toBeInTheDocument();
    expect(screen.getByText("Delivered a useful system.")).toBeInTheDocument();
    expect(screen.getByText("Unsupported by the available evidence — no answer was drafted.")).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("Evidence references")[0]);
    expect(screen.getAllByText("Career evidence").length).toBeGreaterThan(0);
    expect(screen.getAllByText("evidence:opaque-123").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Open saved public vacancy" })).toHaveAttribute("href", "https://jobs.example.test/current");
  });

  it("omits cover-letter download controls when the historical preparation has no letter", async () => {
    renderApp(fetcher({ "/api/v1/applications/p-1": () => json(preparation("p-1", "Text target", false)) }), "/applications/p-1");
    expect(await screen.findByText("No cover letter exists for this preparation.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download CV DOCX" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /cover letter/i })).not.toBeInTheDocument();
  });

  it("downloads authenticated binaries using the response filename and revokes the object URL", async () => {
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:prepared-cv");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    let downloadedName = "";
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { downloadedName = this.download; });
    const fetch = fetcher(); renderApp(fetch, "/applications/p-1");
    await screen.findByRole("heading", { name: "Applied AI Engineer" });
    fireEvent.click(screen.getByRole("button", { name: "Download CV DOCX" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(create).toHaveBeenCalled();
    const download = fetch.mock.calls.find(([input]) => String(input).includes("/cv.docx"));
    expect(new Headers(download?.[1]?.headers).get("Authorization")).toBe("Bearer test-token");
    expect(downloadedName).toBe("Historic_CV.docx");
    const anchor = document.querySelector("a[download]");
    expect(anchor).toBeNull();
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:prepared-cv"));
  });

  it("keeps saved preparation detail visible after a document 404", async () => {
    renderApp(fetcher({ "/api/v1/applications/p-1/cv.pdf": () => json({ detail: "not found" }, 404) }), "/applications/p-1");
    expect(await screen.findByRole("heading", { name: "Applied AI Engineer" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Download CV PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("document could not be downloaded");
    expect(screen.getByRole("heading", { name: "Applied AI Engineer" })).toBeInTheDocument();
  });

  it("keeps the newest application list refresh when an older request resolves late", async () => {
    const old = deferred<Response>(); let calls = 0;
    const fetch = fetcher({ "/api/v1/applications": () => ++calls === 1 ? old.promise : json([preparation("fresh", "Fresh history")]) });
    renderApp(fetch);
    fireEvent.click(await screen.findByRole("button", { name: "Refresh history" }));
    expect(await screen.findByRole("heading", { name: "Fresh history" })).toBeInTheDocument();
    old.resolve(json([preparation("old", "Old late history")]));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Old late history" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Fresh history" })).toBeInTheDocument();
  });

  it("ignores a delayed detail response after a newer preparation is selected", async () => {
    const first = deferred<Response>();
    const fetch = fetcher({ "/api/v1/applications/p-A": () => first.promise, "/api/v1/applications/p-B": () => json(preparation("p-B", "Role B")) });
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/applications/p-A"]}><AuthProvider><TestNavigation /><App /></AuthProvider></MemoryRouter>);
    fireEvent.click(await screen.findByRole("link", { name: "Open B" }));
    expect(await screen.findByRole("heading", { name: "Role B" })).toBeInTheDocument();
    first.resolve(json(preparation("p-A", "Role A")));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Role A" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Role B" })).toBeInTheDocument();
  });

  it("does not expose whether another account owns a preparation ID", async () => {
    renderApp(fetcher({ "/api/v1/applications/secret-id": () => json({ detail: "not found" }, 404) }), "/applications/secret-id");
    expect(await screen.findByRole("alert")).toHaveTextContent("This preparation is not available to this account.");
    expect(screen.queryByText(/belongs to another user/i)).not.toBeInTheDocument();
  });
});

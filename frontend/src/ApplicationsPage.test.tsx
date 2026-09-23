import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Link, MemoryRouter } from "react-router-dom";
import type { ApplicationPreparation, ApplicationPreparationReview, User } from "./api";
import { App } from "./App";
import { AuthProvider, useAuth } from "./auth";
import { buildRequirementReview, collectFinalCitationUsage, evidenceIdentity } from "./applicationReview";

const TOKEN = "career-trans.access-token";
const user: User = { id: "owner", email: "owner@example.test", created_at: "2026-01-01T00:00:00Z" };
const ref = { source_type: "career_evidence", source_ref: "evidence:opaque-123" };
const requirements = [
  { text: "Python delivery", importance: "essential" as const, category: "technical" },
  { text: "MSc qualification", importance: "desirable" as const, category: "education" },
];
const preparation = (id: string, title = "Applied AI Engineer", cover = true): ApplicationPreparation => ({
  id,
  target: { source_kind: "discovered_job", canonical_discovered_job_id: "job-canonical", public_url: "https://jobs.example.test/current", title, company: "Example Co", location: "London", work_arrangement: "Hybrid", employment_type: "Full-time", job_content_hash: "hash", job_profile: { title, company: "Example Co", location: "London", work_arrangement: "Hybrid", seniority: null, salary: null, employment_type: "Full-time", application_deadline: null, responsibilities: [], requirements, technical_skills: [], domain_knowledge: [], security_requirements: [], work_authorization_requirements: [] }, requirement_matches: [
    { requirement_index: 1, requirement: requirements[1], match_type: "transferable", score: 0.7, evidence_ids: [], evidence_refs: [{ source_type: "education", source_ref: "education:0", value: "MSc, Example University" }], reasoning: "Persisted education match." },
    { requirement_index: 0, requirement: requirements[0], match_type: "demonstrated", score: 0.9, evidence_ids: [ref.source_ref], evidence_refs: [ref], reasoning: "Persisted technical match." },
  ] },
  identity: { display_name: "Historic Name", email: "historic@example.test", phone: "020 0000", location: "London", linkedin_url: "https://linkedin.example.test/person", github_url: null, portfolio_url: null },
  preparation_input_fingerprint: "input", preparation_contract_fingerprint: "contract",
  result: { cv: { professional_summary: "Evidence-grounded summary.", summary_source_refs: [ref], key_skills: ["Python"], roles: [{ employer: "Example Ltd", title: "Engineer", start_date: "2020", end_date: "2024", location: "London", bullets: [{ text: "Delivered a useful system.", source_refs: [ref], priority: 80 }] }], selected_projects: [{ name: "Project A", text: "Built a system.", source_refs: [{ source_type: "project", source_ref: "project:0" }], priority: 60 }], education: ["MSc, Example University"], credentials: ["Cloud certification"] }, cover_letter: cover ? { body: "Dear team,\n\nI am interested in the role.", source_refs: [ref] } : null, answers: [{ question: "Describe your experience.", status: "drafted", answer: "I delivered a useful system.", source_refs: [ref] }, { question: "Do you have a clearance?", status: "unsupported", answer: null, source_refs: [] }], layout_status: "fit", target_pages: 2, actual_pdf_pages: 2 },
  created_at: "2026-03-01T12:00:00Z",
});
const review = (preparationId: string, status: "available" | "legacy_unavailable" = "available", sources = [{ ...ref, text: "Historical delivery evidence from preparation time." }, { source_type: "project", source_ref: "project:0", text: "Historical project evidence." }]): ApplicationPreparationReview => ({ preparation_id: preparationId, evidence_snapshot_status: status, evidence_sources: sources });
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
    const reviewMatch = path.match(/^\/api\/v1\/applications\/([^/]+)\/review$/);
    if (reviewMatch) return Promise.resolve(json(review(decodeURIComponent(reviewMatch[1]))));
    const detailMatch = path.match(/^\/api\/v1\/applications\/([^/]+)$/);
    if (detailMatch && method === "GET") return Promise.resolve(json(preparation(decodeURIComponent(detailMatch[1]))));
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
function ExpireSession() { const { api } = useAuth(); return <button type="button" onClick={() => void api.request("/api/v1/expire-session").catch(() => {})}>Expire session</button>; }

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
    expect(requestPaths(fetch).some((path) => path.endsWith("/review"))).toBe(false);
  });

  it("supports direct deep links and renders immutable target, identity, factual content and provenance", async () => {
    const fetch = fetcher();
    renderApp(fetch, "/applications/p-1");
    expect(await screen.findByRole("heading", { name: "Applied AI Engineer" })).toBeInTheDocument();
    expect(screen.getByText(/saved snapshot created/)).toBeInTheDocument();
    expect(screen.getByText("Historic Name")).toBeInTheDocument();
    expect(screen.getByText("historic@example.test")).toBeInTheDocument();
    expect(screen.getByText("Evidence-grounded summary.")).toBeInTheDocument();
    expect(screen.getByText("Delivered a useful system.")).toBeInTheDocument();
    expect(screen.getByText("Unsupported by the available evidence — no answer was drafted.")).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("Evidence used")[0]);
    expect(screen.getAllByText("Career evidence").length).toBeGreaterThan(0);
    expect(screen.getAllByText("evidence:opaque-123").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Historical delivery evidence from preparation time.").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Preparation review summary" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Requirement review" })).toBeInTheDocument();
    expect(screen.getByText("At least one matched evidence source is cited in prepared materials")).toBeInTheDocument();
    expect(screen.getAllByText(/CV professional summary/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Cover letter/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/ready to submit|quality score/i)).not.toBeInTheDocument();
    expect(requestPaths(fetch)).not.toContain("/api/v1/profile");
    expect(requestPaths(fetch).filter((path) => path === "/api/v1/applications/p-1/review")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Open saved public vacancy" })).toHaveAttribute("href", "https://jobs.example.test/current");
  });

  it("renders legacy authority explicitly without deriving it from fingerprints or empty refs", async () => {
    const old = preparation("p-1");
    old.preparation_contract_fingerprint = "opaque-v2-looking-fingerprint";
    const fetch = fetcher({
      "/api/v1/applications/p-1": () => json(old),
      "/api/v1/applications/p-1/review": () => json(review("p-1", "legacy_unavailable", [])),
    });
    renderApp(fetch, "/applications/p-1");
    expect((await screen.findAllByText(/Bounded drafting evidence was not stored for this older preparation/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Drafting-context admission was not stored for this older preparation/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Historical evidence sources")).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByText("Evidence used")[0]);
    expect(screen.getAllByText(/Human-readable historical preparation evidence was not stored/).length).toBeGreaterThan(0);
  });

  it("shows an empty available snapshot as available with a factual count of zero", async () => {
    const fetch = fetcher({ "/api/v1/applications/p-1/review": () => json(review("p-1", "available", [])) });
    renderApp(fetch, "/applications/p-1");
    expect(await screen.findByText(/Preparation-time evidence snapshot available/)).toBeInTheDocument();
    const label = screen.getByText("Historical evidence sources");
    expect(label.parentElement).toHaveTextContent("0");
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

  it("ignores a delayed review response after a newer preparation is selected", async () => {
    const oldReview = deferred<Response>();
    const fetch = fetcher({ "/api/v1/applications/p-A/review": () => oldReview.promise, "/api/v1/applications/p-B": () => json(preparation("p-B", "Role B")) });
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/applications/p-A"]}><AuthProvider><TestNavigation /><App /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Applied AI Engineer" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Open B" }));
    expect(await screen.findByRole("heading", { name: "Role B" })).toBeInTheDocument();
    expect((await screen.findAllByText("Historical delivery evidence from preparation time.")).length).toBeGreaterThan(0);
    oldReview.resolve(json(review("p-A", "available", [{ ...ref, text: "OLD A REVIEW SECRET" }])));
    await waitFor(() => expect(screen.queryByText("OLD A REVIEW SECRET")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Role B" })).toBeInTheDocument();
  });

  it("discards a review response whose preparation id does not match detail", async () => {
    const fetch = fetcher({ "/api/v1/applications/p-1/review": () => json(review("different-preparation", "available", [{ ...ref, text: "MISMATCHED SECRET" }])) });
    renderApp(fetch, "/applications/p-1");
    expect(await screen.findByRole("heading", { name: "Applied AI Engineer" })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Historical evidence review did not match this preparation");
    expect(screen.queryByText("MISMATCHED SECRET")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Requirement review" })).not.toBeInTheDocument();
  });

  it("does not combine detail and review payloads for different selections", async () => {
    const aReview = deferred<Response>();
    const fetch = fetcher({ "/api/v1/applications/p-A/review": () => aReview.promise, "/api/v1/applications/p-B": () => json(preparation("p-B", "Role B")) });
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/applications/p-A"]}><AuthProvider><TestNavigation /><App /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Applied AI Engineer" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Open B" }));
    expect(await screen.findByRole("heading", { name: "Role B" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Requirement review" })).toBeInTheDocument();
    aReview.resolve(json(review("p-A", "available", [{ ...ref, text: "A snapshot only" }])));
    await waitFor(() => expect(screen.queryByText("A snapshot only")).not.toBeInTheDocument());
    expect(screen.getByText("Persisted technical match.")).toBeInTheDocument();
  });

  it("does not restore delayed review content after authenticated-session replacement", async () => {
    let resolveReviewBody!: (value: unknown) => void;
    const pendingBody = new Promise<unknown>((resolve) => { resolveReviewBody = resolve; });
    const fetch = fetcher({
      "/api/v1/applications/p-1/review": () => ({ status: 200, ok: true, headers: new Headers(), json: () => pendingBody } as unknown as Response),
      "/api/v1/expire-session": () => json({ detail: "expired" }, 401),
    });
    sessionStorage.setItem(TOKEN, "test-token"); vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/applications/p-1"]}><AuthProvider><ExpireSession /><App /></AuthProvider></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Applied AI Engineer" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expire session" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Applied AI Engineer" })).not.toBeInTheDocument());
    resolveReviewBody(review("p-1", "available", [{ ...ref, text: "OLD SESSION EVIDENCE" }]));
    await waitFor(() => expect(screen.queryByText("OLD SESSION EVIDENCE")).not.toBeInTheDocument());
  });

  it("does not expose whether another account owns a preparation ID", async () => {
    renderApp(fetcher({ "/api/v1/applications/secret-id": () => json({ detail: "not found" }, 404) }), "/applications/secret-id");
    expect(await screen.findByRole("alert")).toHaveTextContent("This preparation is not available to this account.");
    expect(screen.queryByText(/belongs to another user/i)).not.toBeInTheDocument();
  });
});

describe("Issue #182 deterministic review projection", () => {
  it("derives summary citations from final persisted surfaces only and deduplicates typed identities", () => {
    const value = preparation("final-only");
    value.result.cv.roles[0].bullets = [];
    value.result.cv.selected_projects = [];
    value.result.cover_letter = null;
    const citations = collectFinalCitationUsage(value);
    expect(citations.size).toBe(1);
    expect(citations.get(evidenceIdentity(ref))).toEqual(["CV professional summary", "Application answer · Describe your experience."]);
    expect(citations.has(evidenceIdentity({ source_type: "project", source_ref: "project:0" }))).toBe(false);
    expect(evidenceIdentity({ source_type: "career_evidence", source_ref: "same" })).not.toBe(evidenceIdentity({ source_type: "project", source_ref: "same" }));
  });

  it("associates matches by canonical requirement_index and uses only exact typed snapshot identities", () => {
    const value = preparation("indexed");
    const projection = review(value.id, "available", [{ source_type: "career_evidence", source_ref: ref.source_ref, text: "Exact career source" }, { source_type: "project", source_ref: ref.source_ref, text: "Different typed source" }]);
    const { rows } = buildRequirementReview(value, projection);
    expect(rows.map((row) => row.requirement.text)).toEqual(["Python delivery", "MSc qualification"]);
    expect(rows.map((row) => row.match?.match_type)).toEqual(["demonstrated", "transferable"]);
    expect(rows[0].evidence[0].historicalText).toBe("Exact career source");
    expect(rows[1].evidence[0].historicalTextSource).toBe("match_value");
    expect(rows[1].evidence[0].historicalText).toBe("MSc, Example University");
  });

  it("uses legacy evidence_ids only as career-evidence fallback and keeps empty V2C2 distinct from legacy", () => {
    const value = preparation("compat");
    const match = value.target.requirement_matches.find((item) => item.requirement_index === 0)!;
    match.evidence_refs = [];
    match.evidence_ids = ["legacy-id"];
    const legacyRow = buildRequirementReview(value, review(value.id, "legacy_unavailable", [] )).rows[0];
    expect(legacyRow.evidence[0].ref).toEqual({ source_type: "career_evidence", source_ref: "legacy-id" });
    expect(legacyRow.evidence[0].state).toBe("admission_unknown");
    const emptyAvailable = review(value.id, "available", []);
    const availableRow = buildRequirementReview(value, emptyAvailable).rows[0];
    expect(emptyAvailable.evidence_snapshot_status).toBe("available");
    expect(availableRow.aggregate).toBe("Matched evidence was not included in the bounded preparation context");
  });

  it("marks duplicate and out-of-range persisted requirement indexes inconsistent without throwing", () => {
    const value = preparation("malformed");
    value.target.requirement_matches.push({ ...value.target.requirement_matches.find((item) => item.requirement_index === 0)!, reasoning: "duplicate" });
    value.target.requirement_matches.push({ ...value.target.requirement_matches[0], requirement_index: 99 });
    const result = buildRequirementReview(value, review(value.id));
    expect(result.rows[0].inconsistent).toBe(true);
    expect(result.rows[0].match).toBeNull();
    expect(result.rows[1].match?.match_type).toBe("transferable");
    expect(result.invalidIndexCount).toBe(1);
  });

  it("applies aggregate precedence while retaining per-reference admission and citation states", () => {
    const value = preparation("mixed");
    value.target.requirement_matches.find((item) => item.requirement_index === 0)!.evidence_refs = [
      { source_type: "career_evidence", source_ref: ref.source_ref },
      { source_type: "project", source_ref: "admitted-only" },
      { source_type: "credential", source_ref: "not-admitted" },
    ];
    const projection = review(value.id, "available", [
      { source_type: "career_evidence", source_ref: ref.source_ref, text: "Cited" },
      { source_type: "project", source_ref: "admitted-only", text: "Available only" },
    ]);
    const row = buildRequirementReview(value, projection).rows[0];
    expect(row.aggregate).toBe("At least one matched evidence source is cited in prepared materials");
    expect(row.evidence.map((item) => item.state)).toEqual(["admitted_cited", "admitted_uncited", "not_admitted"]);
    value.result.cv.summary_source_refs = [];
    value.result.cv.roles[0].bullets = [];
    value.result.cv.selected_projects = [];
    value.result.cover_letter = null;
    value.result.answers[0].source_refs = [];
    const noCitations = buildRequirementReview(value, projection).rows[0];
    expect(noCitations.aggregate).toBe("Matched evidence was available to drafting, but none of the admitted sources is cited in prepared materials");
  });
});

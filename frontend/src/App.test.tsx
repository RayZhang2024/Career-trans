import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, vi, expect, it } from "vitest";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { ApiError } from "./auth";
import { ProfileHome } from "./App";

const request = vi.fn(async (..._args: unknown[]): Promise<unknown> => ({}));
vi.mock("./auth", () => ({ useAuth: () => ({ api: { request }, logout: vi.fn(), user: { email: "person@example.com" }, status: "authenticated" }), ApiError: class ApiError extends Error { constructor(public status: number, message = "Request failed") { super(message); } } }));
afterEach(() => { cleanup(); request.mockReset(); request.mockResolvedValue({}); });

const emptySnapshot = () => ({
  profile: null, structured_profile: null, active_evidence: [], adviser_intake: null,
  eligibility: { work_authorisation: [], security_clearances: [], locations: [] }, adviser_assessment: null,
  adviser_assessment_status: "not_available",
  readiness: { structured_profile_available: false, ready_for_candidate_context: false, evidence_materialization_status: "not_applicable", expected_evidence_count: 0, materialized_evidence_count: 0, missing_evidence_count: 0, stale_evidence_count: 0, latest_cv_draft_state: null },
});
const onboarding = { profile_exists: false, candidate_context_ready: false, latest_cv_draft: null, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 0, journey: { candidate_context_ready: false, job_search_ready: false, intake_exists: false, assessment_status: null, confirmed_guidance_active: false, current_follow_up_available: false, clarification_interpretation_awaiting_confirmation: false, unresolved_profile_enrichment_count: 0, next_enrichment_clarification_id: null, next_enrichment: null, active_profile_draft: false, next_action: "complete_profile", status_category: "setup", confirmed_clarification_count: 0 } } };
function renderHome(snapshot: unknown = emptySnapshot(), active: unknown = null, sharedStatus: unknown = onboarding) {
  request.mockImplementation(async (path) => path === "/api/v1/profile/snapshot" ? snapshot : path === "/api/v1/onboarding/status" ? sharedStatus : path === "/api/v1/profile/revisions/active" ? active : ({}));
  return render(<MemoryRouter><ProfileHome /></MemoryRouter>);
}

const nullProfile = { display_name: null, headline: null, current_role: null, location: null, summary: null, career_goal: null, job_search_criteria: null, preferred_email: null, phone: null, linkedin_url: null, github_url: null, portfolio_url: null };
const emptyStructured = { employment: [], education: [], credentials: [], skills: [], projects: [], achievements: [] };
const revision = (overrides: Record<string, unknown> = {}) => ({ id: "revision-1", state: "draft", revision: 1, proposed_profile: null, proposed_structured: null, structured_comparisons: [], changed_authorities: [], stale_authorities: [], created_at: "", updated_at: "", confirmed_at: null, discarded_at: null, ...overrides });

it("loads the candidate view from the canonical snapshot, never requiring the legacy profile read", async () => {
  renderHome();
  expect(await screen.findByRole("heading", { name: "Your career profile" })).toBeInTheDocument();
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/profile/snapshot"));
  expect(request).toHaveBeenCalledWith("/api/v1/profile/revisions/active");
  expect(request).not.toHaveBeenCalledWith("/api/v1/profile");
});

it("renders typed CV domains, preferences, eligibility, confirmed Adviser, and active evidence", async () => {
  renderHome({
    ...emptySnapshot(),
    profile: { id: "p", user_id: "u", created_at: "", updated_at: "", display_name: "Alex Example", headline: "Platform engineer", career_goal: "Lead reliability work", job_search_criteria: "Remote roles" },
    structured_profile: {
      employment: [{ employer: "Example Co", title: "Engineer", start_date: "2020", end_date: null, location: "Remote", description: "Built dependable services." }],
      education: [{ institution: "Example University", qualification: "BSc", field_of_study: "Computing", description: "" }],
      credentials: [{ name: "Cloud Certificate", credential_type: "certification", issuer: "Cloud Org", issued_date: null, expiry_date: null, status: null, description: "" }],
      skills: [{ name: "Python", category: "Engineering" }], projects: [{ name: "Platform", description: "Internal platform", skills: ["Python"] }], achievements: [{ text: "Delivered a reliable service." }], evidence: [],
    },
    active_evidence: [{ evidence_id: "e1", title: "Service migration", evidence_type: "project", text: "Moved production services safely.", skills: ["Migration"] }],
    adviser_intake: { career_direction: "Platform leadership", work_preferences: ["Remote"], constraints: ["Care responsibilities"], self_assessment: [], motivations: [], tradeoffs: ["Scope over title"], eligibility: { work_authorisation: [], security_clearances: [], locations: [] }, updated_at: "" },
    eligibility: { work_authorisation: ["United Kingdom"], security_clearances: ["Baseline"], locations: ["London"] },
    adviser_assessment_status: "confirmed",
    adviser_assessment: {
      professional_positioning: { text: "Reliability-focused engineer.", source_references: [{ source_type: "career_evidence", reference: "e1" }] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Ready to grow.", source_references: [{ source_type: "intake", reference: "goal" }] }, open_questions: [],
      career_strategy_summary: { text: "Build platform leadership experience.", source_references: [{ source_type: "intake", reference: "goal" }] }, job_search_strategy_summary: { text: "Target remote platform roles.", source_references: [{ source_type: "intake", reference: "prefs" }] },
    },
  }, null, { ...onboarding, candidate_context_ready: true, adviser: { ...onboarding.adviser, intake_exists: true, assessment_status: "confirmed", journey: { ...onboarding.adviser.journey, candidate_context_ready: true, intake_exists: true, assessment_status: "confirmed", confirmed_guidance_active: true, next_action: "find_jobs", status_category: "up_to_date" } } });
  expect(await screen.findByText("Alex Example")).toBeInTheDocument();
  expect(screen.getByText("Engineer at Example Co")).toBeInTheDocument();
  expect(screen.getByText("BSc — Example University")).toBeInTheDocument();
  expect(screen.getByText("Cloud Certificate")).toBeInTheDocument();
  expect(screen.getByText("Python · Engineering")).toBeInTheDocument();
  expect(screen.getByText("Platform")).toBeInTheDocument();
  expect(screen.getByText("Delivered a reliable service.")).toBeInTheDocument();
  expect(screen.getByText("Remote")).toBeInTheDocument();
  expect(screen.getByText("United Kingdom")).toBeInTheDocument();
  expect(screen.getByText("Reliability-focused engineer.")).toBeInTheDocument();
  expect(screen.getByText("Service migration")).toBeInTheDocument();
});

it("handles profile-only, structured-only, empty, and empty optional domains", async () => {
  const { unmount } = renderHome({ ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Saved headline" } });
  expect(await screen.findByText("Saved headline")).toBeInTheDocument();
  expect(screen.getByText(/Confirmed career information is not available yet/)).toBeInTheDocument();
  unmount();

  renderHome({ ...emptySnapshot(), structured_profile: { employment: [{ employer: "Only Co", title: "Analyst", start_date: null, end_date: null, location: null, description: "" }], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] } });
  expect(await screen.findByText("Analyst at Only Co")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit profile" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Continue CV onboarding" })).toHaveAttribute("href", "/profile/cv");
});

it("shows the empty state, a pending CV notice, and never mixes draft content into confirmed CV facts", async () => {
  const { unmount } = renderHome();
  expect(await screen.findByText("Let’s build your career profile")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Start CV onboarding" })).toHaveAttribute("href", "/profile/cv");
  unmount();
  renderHome({ ...emptySnapshot(), structured_profile: { employment: [{ employer: "Current Co", title: "Current role", start_date: null, end_date: null, location: null, description: "Current confirmed facts." }], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] }, readiness: { ...emptySnapshot().readiness, structured_profile_available: true, latest_cv_draft_state: "review_ready" } });
  expect(await screen.findByText(/Your current structured career information remains in use/)).toBeInTheDocument();
  expect(screen.getByText("Current role at Current Co")).toBeInTheDocument();
  expect(screen.queryByText(/draft-only content/i)).not.toBeInTheDocument();
});

it.each(["uploaded", "review_ready"] as const)("keeps confirmed CV facts visible while a newer %s CV awaits review", async (state) => {
  renderHome({ ...emptySnapshot(), structured_profile: { employment: [{ employer: "Current Co", title: "Current confirmed role", start_date: null, end_date: null, location: null, description: "Confirmed details only." }], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] }, readiness: { ...emptySnapshot().readiness, structured_profile_available: true, latest_cv_draft_state: state } });
  expect(await screen.findByText(/newer CV update is awaiting review/)).toBeInTheDocument();
  expect(screen.getByText("Current confirmed role at Current Co")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Review CV update" })).toHaveAttribute("href", "/profile/cv");
});

it("shows pending manual Profile changes and a pending CV update together", async () => {
  const activeManual = revision({ proposed_profile: { ...nullProfile, headline: "Proposed headline" }, changed_authorities: ["profile"] });
  renderHome({
    ...emptySnapshot(),
    profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Current headline" },
    structured_profile: { employment: [], education: [], credentials: [], skills: [], projects: [], achievements: [] },
    readiness: { ...emptySnapshot().readiness, structured_profile_available: true, latest_cv_draft_state: "review_ready" },
  }, activeManual);
  expect(await screen.findByText(/You have pending profile changes/)).toBeInTheDocument();
  expect(screen.getByText(/A newer CV update is awaiting review/)).toBeInTheDocument();
});

it("shows Adviser review and stale states without displaying draft assessment content", async () => {
  const draft = { ...emptySnapshot(), adviser_assessment_status: "review_ready", adviser_assessment: { professional_positioning: { text: "Do not show this draft", source_references: [] } } };
  const { unmount } = renderHome(draft, null, { ...onboarding, candidate_context_ready: true, adviser: { ...onboarding.adviser, intake_exists: true, assessment_status: "review_ready", journey: { ...onboarding.adviser.journey, candidate_context_ready: true, intake_exists: true, assessment_status: "review_ready", next_action: "review_assessment", status_category: "review" } } });
  expect(await screen.findByText(/This assessment is ready for your review/)).toBeInTheDocument();
  expect(screen.queryByText("Do not show this draft")).not.toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "Review career assessment" }).every((link) => link.getAttribute("href") === "/profile/adviser")).toBe(true);
  unmount();
  renderHome({ ...emptySnapshot(), adviser_assessment_status: "stale" }, null, { ...onboarding, candidate_context_ready: true, adviser: { ...onboarding.adviser, intake_exists: true, assessment_status: "stale", journey: { ...onboarding.adviser.journey, candidate_context_ready: true, intake_exists: true, assessment_status: "stale", next_action: "update_assessment", status_category: "update" } } });
  expect(await screen.findByText(/New information is available/i)).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "Update career assessment" }).every((link) => link.getAttribute("href") === "/profile/adviser")).toBe(true);
});

it("warns with safe materialisation counts when evidence is incomplete", async () => {
  renderHome({ ...emptySnapshot(), readiness: { ...emptySnapshot().readiness, evidence_materialization_status: "incomplete", expected_evidence_count: 5, materialized_evidence_count: 3, missing_evidence_count: 1, stale_evidence_count: 1 } });
  expect(await screen.findByRole("alert")).toHaveTextContent("Expected: 5; materialised: 3; missing: 1; stale: 1");
  expect(screen.getByRole("alert")).toHaveTextContent("Some matching or application actions may be temporarily unavailable");
  expect(screen.getByRole("alert")).not.toHaveTextContent("re-upload");
});

it("saves only dirty Profile authority and keeps the canonical snapshot unchanged", async () => {
  const original = { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Before" } };
  const existingStructured = { ...emptyStructured, skills: [{ name: "Python", category: "Engineering" }] };
  const active = revision({ proposed_profile: { ...nullProfile, headline: "Before" }, proposed_structured: existingStructured, changed_authorities: ["profile", "structured"] });
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/profile/snapshot") return original;
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/revisions/active") return active;
    if (path === "/api/v1/profile/revisions/revision-1" && (options as { method?: string })?.method === "PATCH") return { ...active, revision: 2, proposed_profile: { ...nullProfile, headline: "Edited" } };
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Resume editing" }));
  const headline = await screen.findByRole("textbox", { name: "Headline" });
  expect(headline).toHaveValue("Before");
  fireEvent.change(headline, { target: { value: "Edited" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText(/Draft saved/)).toBeInTheDocument();
  expect(screen.getByText("Before")).toBeInTheDocument();
  const [, init] = request.mock.calls.find(([path, opts]) => path === "/api/v1/profile/revisions/revision-1" && (opts as { method?: string })?.method === "PATCH") as unknown as [string, { body: string }];
  const payload = JSON.parse(init.body);
  expect(payload).toEqual({ expected_revision: 1, proposed_profile: { ...nullProfile, headline: "Edited" } });
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Python");
  expect(request).not.toHaveBeenCalledWith("/api/v1/profile", expect.objectContaining({ method: expect.any(String) }));
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/profile/snapshot")).toHaveLength(1);
});

it("keeps absent authorities absent until edited and activates only structured authority when adding a record", async () => {
  const created = revision();
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/profile/snapshot") return emptySnapshot();
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/revisions/active") return null;
    if (path === "/api/v1/profile/revisions" && (options as { method?: string })?.method === "POST") return created;
    if (path === "/api/v1/profile/revisions/revision-1" && (options as { method?: string })?.method === "PATCH") return { ...created, revision: 2, proposed_structured: { ...emptyStructured, employment: [{ employer: "Example Co", title: "Engineer", start_date: null, end_date: null, location: null, description: "" }] } };
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Edit profile" }));
  expect(await screen.findByRole("button", { name: "Save draft" })).toBeDisabled();
  expect(request).not.toHaveBeenCalledWith("/api/v1/profile", expect.anything());
  fireEvent.click(screen.getByRole("button", { name: "Add Employment record" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Employer" }), { target: { value: "Example Co" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Engineer" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await screen.findByText(/Draft saved/);
  const [, init] = request.mock.calls.find(([path, opts]) => path === "/api/v1/profile/revisions/revision-1" && (opts as { method?: string })?.method === "PATCH") as unknown as [string, { body: string }];
  const payload = JSON.parse(init.body);
  expect(payload.expected_revision).toBe(1);
  expect(payload.proposed_profile).toBeUndefined();
  expect(payload.proposed_structured.employment[0]).toMatchObject({ employer: "Example Co", title: "Engineer" });
  expect("evidence" in payload.proposed_structured).toBe(false);
});

it("offers retry after snapshot failure without falling back to profile reads", async () => {
  let failed = false;
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") { if (!failed) { failed = true; throw new Error("offline"); } return emptySnapshot(); }
    if (path === "/api/v1/profile/revisions/active") return null;
    return onboarding;
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Your career profile could not be loaded.");
  fireEvent.click(screen.getByRole("button", { name: "Retry profile" }));
  expect(await screen.findByText("Let’s build your career profile")).toBeInTheDocument();
  expect(request).not.toHaveBeenCalledWith("/api/v1/profile");
});

it("keeps the canonical snapshot visible when the active revision read fails", async () => {
  let revisionReads = 0;
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") return { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Canonical" } };
    if (path === "/api/v1/profile/revisions/active") { revisionReads += 1; if (revisionReads === 1) throw new Error("offline"); return null; }
    return onboarding;
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  expect(await screen.findByText("Canonical")).toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent("Saved profile changes could not be loaded.");
  fireEvent.click(screen.getByRole("button", { name: "Retry saved changes" }));
  expect(await screen.findByRole("button", { name: "Edit profile" })).toBeEnabled();
  expect(screen.getByText("Canonical")).toBeInTheDocument();
});

it("does not use a saved proposal as the current view when the canonical snapshot fails", async () => {
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") throw new Error("offline");
    if (path === "/api/v1/profile/revisions/active") return revision({ state: "review_ready", proposed_profile: { ...nullProfile, headline: "Draft-only proposal" }, changed_authorities: ["profile"] });
    return onboarding;
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));
  expect(await screen.findByText("Load the current Profile to compare saved and proposed information.")).toBeInTheDocument();
  expect(screen.queryByText("Draft-only proposal")).not.toBeInTheDocument();
});

it("keeps the latest snapshot authoritative when refresh responses resolve out of order", async () => {
  let reads = 0;
  let resolveOld!: (value: unknown) => void;
  let resolveNew!: (value: unknown) => void;
  const base = { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Initial" } };
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") {
      reads += 1;
      if (reads === 1) return base;
      if (reads === 2) return new Promise((resolve) => { resolveOld = resolve; });
      return new Promise((resolve) => { resolveNew = resolve; });
    }
    if (path === "/api/v1/profile/revisions/active") return null;
    return onboarding;
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  expect(await screen.findByText("Initial")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh profile" }));
  fireEvent.click(screen.getByRole("button", { name: "Refresh profile" }));
  await vi.waitFor(() => expect(reads).toBe(3));
  await act(async () => { resolveNew({ ...base, profile: { ...base.profile, headline: "Newest result" } }); });
  expect(await screen.findByText("Newest result")).toBeInTheDocument();
  await act(async () => { resolveOld({ ...base, profile: { ...base.profile, headline: "Outdated result" } }); });
  expect(screen.getByText("Newest result")).toBeInTheDocument();
  expect(screen.queryByText("Outdated result")).not.toBeInTheDocument();
});

it("loads a fresh canonical snapshot when navigating away from and back to Profile", async () => {
  let reads = 0;
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") {
      reads += 1;
      return { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: reads === 1 ? "First view" : "Fresh view" } };
    }
    if (path === "/api/v1/profile/revisions/active") return null;
    return onboarding;
  });
  render(<MemoryRouter><Routes>
    <Route path="/" element={<><Link to="/away">Leave Profile</Link><ProfileHome /></>} />
    <Route path="/away" element={<Link to="/">Back to Profile</Link>} />
  </Routes></MemoryRouter>);
  expect(await screen.findByText("First view")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "Leave Profile" }));
  fireEvent.click(await screen.findByRole("link", { name: "Back to Profile" }));
  expect(await screen.findByText("Fresh view")).toBeInTheDocument();
  expect(reads).toBe(2);
});

it("loads Profile source history independently and keeps canonical Profile visible after failure and retry", async () => {
  let provenanceReads = 0;
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") return { ...emptySnapshot(), structured_profile: { ...emptyStructured, skills: [{ name: "Python", category: "Engineering" }], evidence: [] } };
    if (path === "/api/v1/profile/revisions/active") return null;
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/structured-provenance") { provenanceReads += 1; if (provenanceReads === 1) throw new Error("offline"); return { items: [] }; }
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  expect(await screen.findByText("Python · Engineering")).toBeInTheDocument();
  expect(request).not.toHaveBeenCalledWith("/api/v1/profile/structured-provenance");
  fireEvent.click(screen.getByRole("button", { name: "Show source history" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Source history is unavailable");
  expect(screen.getByText("Python · Engineering")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry source history" }));
  await vi.waitFor(() => expect(provenanceReads).toBe(2));
  expect(screen.getByText("Python · Engineering")).toBeInTheDocument();
});

it("separates direct Adviser/CV sources from predecessor history and preserves the saved Adviser wording", async () => {
  const current = { name: "Python", category: "Engineering" };
  const event = (id: string, item: typeof current, source: Record<string, unknown>, relationship: string) => ({ event_id: id, section: "skills", item_fingerprint: "f".repeat(64), item, source_kind: source.kind, relationship, predecessor_fingerprint: null, predecessor_item: null, created_at: "2025-01-01T00:00:00Z", source });
  const adviserEvent = event("adviser", current, { kind: "candidate_adviser", source_id: "secret-id", source_clarification_id: "clarification-id", clarification_question: "Which platform did you deliver?", proposal_item: { name: " PYTHON ", category: "Engineering" }, transferred_at: "2025-01-01T00:00:00Z", available: true }, "reinforcement");
  const cvEvent = event("cv", { name: " PYTHON ", category: "Engineering" }, { kind: "cv", source_id: "draft-private", filenames: ["resume.pdf"], source_state: "confirmed", source_created_at: null, source_updated_at: null, available: true }, "new");
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") return { ...emptySnapshot(), structured_profile: { ...emptyStructured, skills: [current], evidence: [] } };
    if (path === "/api/v1/profile/revisions/active") return null;
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/structured-provenance") return { items: [{ section: "skills", item_index: 0, item: current, item_fingerprint: "not-display-this", direct_events: [adviserEvent], history: [{ depth: 2, lineage_event: cvEvent }], source_history_available: true }] };
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  expect(await screen.findByText("Python · Engineering")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show source history" }));
  fireEvent.click(await screen.findByText(/Source history · skills 1/));
  expect(await screen.findByText("Sources for this current item")).toBeInTheDocument();
  expect(screen.getByText("Supported through Candidate Adviser.")).toBeInTheDocument();
  expect(screen.getByText("Which platform did you deliver?")).toBeInTheDocument();
  expect(screen.getAllByText((_, element) => element?.textContent?.includes(" PYTHON ") ?? false).length).toBeGreaterThan(0);
  expect(screen.getByText("Earlier source chain")).toBeInTheDocument();
  expect(screen.getByText("Confirmed from CV: resume.pdf")).toBeInTheDocument();
  expect(screen.queryByText(/not-display-this|draft-private|secret-id|extracted text/i)).not.toBeInTheDocument();
});

it.each(["new", "reinforcement", "refinement", "conflict", "ambiguous"] as const)("shows explanatory %s relationship without adding a second Profile approval", async (relationship) => {
  const currentSkill = { name: "Python", category: "Engineering" };
  const proposedSkill = { name: "Python", category: "Cloud engineering" };
  const active = revision({ state: "review_ready", proposed_structured: { ...emptyStructured, skills: [proposedSkill] }, changed_authorities: ["structured"], structured_comparisons: [{ item_key: "opaque-key", comparison: { section: "skills", relationship, incoming_item: proposedSkill, incoming_fingerprint: "raw-fingerprint", candidate_matches: [], target_fingerprint: null, current_item: null } }] });
  renderHome({ ...emptySnapshot(), structured_profile: { ...emptyStructured, skills: [currentSkill], evidence: [] } }, active);
  fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));
  expect(await screen.findByText(relationship === "new" ? "New information" : relationship === "reinforcement" ? "Same fact" : relationship === "refinement" ? "More detailed version" : relationship === "conflict" ? "Conflicting information" : "Possible duplicate")).toBeInTheDocument();
  expect(screen.queryByText(/raw-fingerprint|opaque-key/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm changes" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /choose|resolve/i })).not.toBeInTheDocument();
});

it("invalidates an in-flight source-history read after Profile confirmation", async () => {
  let finishOld!: (value: unknown) => void;
  let provenanceReads = 0;
  let active: unknown = revision({ state: "review_ready", proposed_profile: { ...nullProfile, headline: "Confirmed headline" }, changed_authorities: ["profile"] });
  const source = (filename: string) => ({ items: [{ section: "skills", item_index: 0, item: { name: "Python", category: "Engineering" }, item_fingerprint: "hidden", direct_events: [{ event_id: filename, section: "skills", item_fingerprint: "hidden", item: { name: "Python", category: "Engineering" }, source_kind: "cv", relationship: "new", predecessor_fingerprint: null, predecessor_item: null, created_at: "2026-01-01T00:00:00Z", source: { kind: "cv", source_id: "hidden-id", filenames: [filename], source_state: "confirmed", source_created_at: null, source_updated_at: null, available: true } }], history: [], source_history_available: true }] });
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/profile/snapshot") return { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Current" }, structured_profile: { ...emptyStructured, skills: [{ name: "Python", category: "Engineering" }], evidence: [] } };
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/revisions/active") return active;
    if (String(path).endsWith("/confirm") && (options as { method?: string })?.method === "POST") { active = null; return {}; }
    if (path === "/api/v1/profile/structured-provenance") { provenanceReads += 1; if (provenanceReads === 1) return new Promise((resolve) => { finishOld = resolve; }); return source("after-confirm.pdf"); }
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Show source history" }));
  await vi.waitFor(() => expect(provenanceReads).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  fireEvent.click(await screen.findByRole("button", { name: "Confirm changes" }));
  expect(await screen.findByText(/Profile changes confirmed/)).toBeInTheDocument();
  await vi.waitFor(() => expect(provenanceReads).toBe(2));
  expect(await screen.findByText("Confirmed from CV: after-confirm.pdf")).toBeInTheDocument();
  await act(async () => finishOld(source("before-confirm.pdf")));
  expect(screen.getByText("Confirmed from CV: after-confirm.pdf")).toBeInTheDocument();
  expect(screen.queryByText("Confirmed from CV: before-confirm.pdf")).not.toBeInTheDocument();
});

it("labels unavailable and legacy Profile source history without inferring a source", async () => {
  const unavailable = { section: "skills", item_index: 0, item: { name: "Python", category: "Engineering" }, item_fingerprint: "hidden", direct_events: [{ event_id: "missing", section: "skills", item_fingerprint: "hidden", item: { name: "Python", category: "Engineering" }, source_kind: "cv", relationship: "new", predecessor_fingerprint: null, predecessor_item: null, created_at: "2026-01-01T00:00:00Z", source: { kind: "cv", source_id: "hidden", filenames: ["old.pdf"], source_state: null, source_created_at: null, source_updated_at: null, available: false } }], history: [], source_history_available: true };
  const legacy = { ...unavailable, item_index: 1, item: { name: "Go", category: "Engineering" }, direct_events: [], source_history_available: false };
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") return { ...emptySnapshot(), structured_profile: { ...emptyStructured, skills: [{ name: "Python", category: "Engineering" }, { name: "Go", category: "Engineering" }], evidence: [] } };
    if (path === "/api/v1/profile/revisions/active") return null;
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/structured-provenance") return { items: [unavailable, legacy] };
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Show source history" }));
  fireEvent.click(await screen.findByText(/Source history · skills 1/));
  expect(await screen.findByText(/original source record is no longer available/)).toBeInTheDocument();
  fireEvent.click(screen.getByText(/Source history · skills 2/));
  expect(screen.getByText(/Source history is unavailable for this older item/)).toBeInTheDocument();
  expect(screen.queryByText(/inferred source/i)).not.toBeInTheDocument();
});

it("requires saved changes before review and shows deterministic current and proposed values", async () => {
  const saved = revision({ revision: 2, state: "draft", proposed_profile: { ...nullProfile, headline: "Proposed" }, changed_authorities: ["profile"] });
  const updated = { ...saved, revision: 3, proposed_profile: { ...nullProfile, headline: "Unsaved" } };
  const ready = { ...updated, revision: 4, state: "review_ready" };
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/profile/snapshot") return { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Current" } };
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/revisions/active") return saved;
    if (path === "/api/v1/profile/revisions/revision-1" && (options as { method?: string })?.method === "PATCH") return updated;
    if (String(path).endsWith("/review") && (options as { method?: string })?.method === "POST") return ready;
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Resume editing" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "Headline" }), { target: { value: "Unsaved" } });
  expect(screen.getByRole("button", { name: "Review changes" })).toBeDisabled();
  expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await screen.findByText(/Draft saved/);
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  expect(await screen.findByRole("heading", { name: "Review profile changes" })).toBeInTheDocument();
  expect(screen.getByText("Current", { selector: "span" })).toBeInTheDocument();
  expect(screen.getByText("Unsaved", { selector: "span" })).toBeInTheDocument();
  expect(request).toHaveBeenCalledWith("/api/v1/profile/revisions/revision-1/review", expect.objectContaining({ method: "POST", body: JSON.stringify({ expected_revision: 3 }) }));
});

it("keeps unsaved edits on expected-revision conflict and offers explicit reload", async () => {
  const active = revision({ proposed_profile: { ...nullProfile, headline: "Saved" }, changed_authorities: ["profile"] });
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/profile/snapshot") return emptySnapshot();
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/revisions/active") return active;
    if (path === "/api/v1/profile/revisions/revision-1" && (options as { method?: string })?.method === "PATCH") throw new ApiError(409, "Conflict");
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Resume editing" }));
  const headline = await screen.findByRole("textbox", { name: "Headline" });
  fireEvent.change(headline, { target: { value: "Local unsaved edit" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText(/updated elsewhere/i)).toBeInTheDocument();
  expect(headline).toHaveValue("Local unsaved edit");
  expect(screen.getByRole("button", { name: "Reload saved draft" })).toBeInTheDocument();
});

it("disables confirmation for stale revisions but leaves the proposal inspectable and discardable", async () => {
  renderHome(emptySnapshot(), revision({ state: "review_ready", proposed_profile: { ...nullProfile, headline: "Proposal" }, changed_authorities: ["profile"], stale_authorities: ["profile"] }));
  fireEvent.click(await screen.findByRole("button", { name: "Inspect proposal" }));
  expect(await screen.findByText(/proposal is stale/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm changes" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Discard draft" })).toBeEnabled();
});

it("confirms only after the canonical snapshot and active revision refresh, and can discard without changing current data", async () => {
  let active: unknown = revision({ state: "review_ready", revision: 3, proposed_profile: { ...nullProfile, headline: "New" }, changed_authorities: ["profile"] });
  let snapshotReads = 0;
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/profile/snapshot") { snapshotReads += 1; return { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: snapshotReads === 1 ? "Old" : "New" } }; }
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/revisions/active") return active;
    if (String(path).endsWith("/confirm") && (options as { method?: string })?.method === "POST") { active = null; return {}; }
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm changes" }));
  expect(await screen.findByText(/Profile changes confirmed/)).toBeInTheDocument();
  expect(screen.getByText("New")).toBeInTheDocument();
  expect(snapshotReads).toBe(2);
  expect(request).toHaveBeenCalledWith("/api/v1/profile/revisions/revision-1/confirm", expect.objectContaining({ method: "POST", body: JSON.stringify({ expected_revision: 3 }) }));
});

it("handles a confirmation conflict as a controlled state refresh", async () => {
  const initial = revision({ state: "review_ready", proposed_profile: { ...nullProfile, headline: "Proposed" }, changed_authorities: ["profile"] });
  const refreshed = { ...initial, stale_authorities: ["profile"] };
  let reads = 0;
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/profile/snapshot") return { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Current" } };
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/revisions/active") { reads += 1; return reads === 1 ? initial : refreshed; }
    if (String(path).endsWith("/confirm") && (options as { method?: string })?.method === "POST") throw new ApiError(409, "Conflict");
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm changes" }));
  expect(await screen.findByText(/Confirmation conflicted/)).toBeInTheDocument();
  expect(await screen.findByText(/Your proposal is stale/)).toBeInTheDocument();
  expect(screen.getAllByText("Current").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "Confirm changes" })).toBeDisabled();
});

it("discard releases the active draft without changing the canonical snapshot", async () => {
  let active: unknown = revision({ proposed_profile: { ...nullProfile, headline: "Not current" }, changed_authorities: ["profile"] });
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/profile/snapshot") return { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Current" } };
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile/revisions/active") return active;
    if (String(path).endsWith("/discard") && (options as { method?: string })?.method === "POST") { active = null; return {}; }
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Discard draft" }));
  expect(await screen.findByText(/Draft discarded/)).toBeInTheDocument();
  expect(screen.getByText("Current")).toBeInTheDocument();
  expect(screen.queryByText("Not current")).not.toBeInTheDocument();
  expect(request).toHaveBeenCalledWith("/api/v1/profile/revisions/revision-1/discard", expect.objectContaining({ method: "POST", body: JSON.stringify({ expected_revision: 1 }) }));
});

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, vi, expect, it } from "vitest";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { ProfileForm, ProfileHome } from "./App";

const request = vi.fn(async (..._args: unknown[]): Promise<unknown> => ({}));
vi.mock("./auth", () => ({ useAuth: () => ({ api: { request }, logout: vi.fn(), user: { email: "person@example.com" }, status: "authenticated" }), ApiError: class ApiError extends Error {} }));
afterEach(() => { cleanup(); request.mockReset(); request.mockResolvedValue({}); });

const emptySnapshot = () => ({
  profile: null, structured_profile: null, active_evidence: [], adviser_intake: null,
  eligibility: { work_authorisation: [], security_clearances: [], locations: [] }, adviser_assessment: null,
  adviser_assessment_status: "not_available",
  readiness: { structured_profile_available: false, ready_for_candidate_context: false, evidence_materialization_status: "not_applicable", expected_evidence_count: 0, materialized_evidence_count: 0, missing_evidence_count: 0, stale_evidence_count: 0, latest_cv_draft_state: null },
});
const onboarding = { profile_exists: false, candidate_context_ready: false, latest_cv_draft: null, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 0 } };
function renderHome(snapshot: unknown = emptySnapshot()) {
  request.mockImplementation(async (path) => path === "/api/v1/profile/snapshot" ? snapshot : path === "/api/v1/onboarding/status" ? onboarding : ({}));
  return render(<MemoryRouter><ProfileHome /></MemoryRouter>);
}

it("loads the candidate view from the canonical snapshot, never requiring the legacy profile read", async () => {
  renderHome();
  expect(await screen.findByRole("heading", { name: "Your career profile" })).toBeInTheDocument();
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/profile/snapshot"));
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
  });
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
  expect(screen.getByText(/Confirmed CV information is not available yet/)).toBeInTheDocument();
  unmount();

  renderHome({ ...emptySnapshot(), structured_profile: { employment: [{ employer: "Only Co", title: "Analyst", start_date: null, end_date: null, location: null, description: "" }], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] } });
  expect(await screen.findByText("Analyst at Only Co")).toBeInTheDocument();
  expect(screen.getByText("Create saved profile details")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Continue CV onboarding" })).toHaveAttribute("href", "/cv");
});

it("shows the empty state, a pending CV notice, and never mixes draft content into confirmed CV facts", async () => {
  const { unmount } = renderHome();
  expect(await screen.findByText("Let’s build your career profile")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Start CV onboarding" })).toHaveAttribute("href", "/cv");
  unmount();
  renderHome({ ...emptySnapshot(), structured_profile: { employment: [{ employer: "Current Co", title: "Current role", start_date: null, end_date: null, location: null, description: "Current confirmed facts." }], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] }, readiness: { ...emptySnapshot().readiness, structured_profile_available: true, latest_cv_draft_state: "review_ready" } });
  expect(await screen.findByText(/Your current profile is still in use/)).toBeInTheDocument();
  expect(screen.getByText("Current role at Current Co")).toBeInTheDocument();
  expect(screen.queryByText(/draft-only content/i)).not.toBeInTheDocument();
});

it.each(["uploaded", "review_ready"] as const)("keeps confirmed CV facts visible while a newer %s CV awaits review", async (state) => {
  renderHome({ ...emptySnapshot(), structured_profile: { employment: [{ employer: "Current Co", title: "Current confirmed role", start_date: null, end_date: null, location: null, description: "Confirmed details only." }], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] }, readiness: { ...emptySnapshot().readiness, structured_profile_available: true, latest_cv_draft_state: state } });
  expect(await screen.findByText(/newer CV update is awaiting review/)).toBeInTheDocument();
  expect(screen.getByText("Current confirmed role at Current Co")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Review CV update" })).toHaveAttribute("href", "/cv");
});

it("shows Adviser review and stale states without displaying draft assessment content", async () => {
  const draft = { ...emptySnapshot(), adviser_assessment_status: "review_ready", adviser_assessment: { professional_positioning: { text: "Do not show this draft", source_references: [] } } };
  const { unmount } = renderHome(draft);
  expect(await screen.findByText(/draft assessment is not shown as current/)).toBeInTheDocument();
  expect(screen.queryByText("Do not show this draft")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Review Career Adviser assessment" })).toHaveAttribute("href", "/adviser");
  unmount();
  renderHome({ ...emptySnapshot(), adviser_assessment_status: "stale" });
  expect(await screen.findByText(/reassessment needed/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Revisit Career Adviser" })).toHaveAttribute("href", "/adviser");
});

it("warns with safe materialisation counts when evidence is incomplete", async () => {
  renderHome({ ...emptySnapshot(), readiness: { ...emptySnapshot().readiness, evidence_materialization_status: "incomplete", expected_evidence_count: 5, materialized_evidence_count: 3, missing_evidence_count: 1, stale_evidence_count: 1 } });
  expect(await screen.findByRole("alert")).toHaveTextContent("Expected: 5; materialised: 3; missing: 1; stale: 1");
  expect(screen.getByRole("alert")).toHaveTextContent("Some matching or application actions may be temporarily unavailable");
  expect(screen.getByRole("alert")).not.toHaveTextContent("re-upload");
});

it("initialises editing from snapshot profile and reloads snapshot and onboarding after save", async () => {
  let snapshotReads = 0;
  const original = { ...emptySnapshot(), profile: { id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Before" } };
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/profile/snapshot") { snapshotReads += 1; return snapshotReads > 1 ? { ...original, profile: { ...original.profile, headline: "After" } } : original; }
    if (path === "/api/v1/onboarding/status") return onboarding;
    if (path === "/api/v1/profile") return {};
    return {};
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.click(await screen.findByText("Edit saved profile details"));
  const headline = await screen.findByRole("textbox", { name: "Headline" });
  expect(headline).toHaveValue("Before");
  fireEvent.change(headline, { target: { value: "Edited" } });
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await vi.waitFor(() => expect(screen.getByText("After")).toBeInTheDocument());
  expect(request).toHaveBeenCalledWith("/api/v1/profile", expect.objectContaining({ method: "PATCH" }));
  expect(snapshotReads).toBe(2);
  expect(request).toHaveBeenCalledWith("/api/v1/onboarding/status");
});

it("creates with POST when the snapshot has no scalar profile and displays only the reloaded server state", async () => {
  let snapshotReads = 0;
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") { snapshotReads += 1; return snapshotReads === 1 ? emptySnapshot() : { ...emptySnapshot(), profile: { id: "created", user_id: "u", created_at: "", updated_at: "", headline: "Server returned" } }; }
    return onboarding;
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  fireEvent.change(await screen.findByRole("textbox", { name: "Headline" }), { target: { value: "Submitted value" } });
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  expect(await screen.findByText("Server returned")).toBeInTheDocument();
  expect(request).toHaveBeenCalledWith("/api/v1/profile", expect.objectContaining({ method: "POST" }));
  expect(snapshotReads).toBe(2);
  expect(screen.queryByText("Submitted value")).not.toBeInTheDocument();
});

it("offers retry after snapshot failure without falling back to profile reads", async () => {
  let failed = false;
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/profile/snapshot") { if (!failed) { failed = true; throw new Error("offline"); } return emptySnapshot(); }
    return onboarding;
  });
  render(<MemoryRouter><ProfileHome /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Your career profile could not be loaded.");
  fireEvent.click(screen.getByRole("button", { name: "Retry profile" }));
  expect(await screen.findByText("Let’s build your career profile")).toBeInTheDocument();
  expect(request).not.toHaveBeenCalledWith("/api/v1/profile");
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

it("populates an existing profile and preserves untouched fields on patch", async () => {
  render(<ProfileForm profile={{ id: "p", user_id: "u", created_at: "", updated_at: "", headline: "Engineer", location: "London" }} onSaved={() => {}} onFeedbackClear={() => {}} />);
  expect(screen.getByRole("textbox", { name: "Headline" })).toHaveValue("Engineer");
  fireEvent.change(screen.getByRole("textbox", { name: "Location" }), { target: { value: "Oxford" } });
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalled());
  const call = request.mock.calls[0] as unknown as [string, { method: string; body: string }];
  const payload = JSON.parse(call[1].body);
  expect(payload.headline).toBe("Engineer");
  expect(payload.location).toBe("Oxford");
  expect(call[1].method).toBe("PATCH");
});

it("uses multi-line controls for the long-form optional profile fields", () => {
  render(<ProfileForm profile={null} onSaved={() => {}} onFeedbackClear={() => {}} />);
  expect(screen.getByRole("textbox", { name: "Summary" }).tagName).toBe("TEXTAREA");
  expect(screen.getByRole("textbox", { name: "Career goal" }).tagName).toBe("TEXTAREA");
  expect(screen.getByRole("textbox", { name: "Job-search criteria" }).tagName).toBe("TEXTAREA");
});

it("uses create semantics when no profile exists", async () => {
  request.mockClear(); render(<ProfileForm profile={null} onSaved={() => {}} onFeedbackClear={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalled());
  const call = request.mock.calls[0] as unknown as [string, { method: string }];
  expect(call[1].method).toBe("POST");
});

it("shows pending feedback, blocks duplicate profile submits, and calls success only after save resolves", async () => {
  let resolveSave!: (value: {}) => void;
  request.mockReturnValueOnce(new Promise<{}>((resolve) => { resolveSave = resolve; }));
  const onSaved = vi.fn();
  render(<ProfileForm profile={null} onSaved={onSaved} onFeedbackClear={() => {}} />);

  const save = screen.getByRole("button", { name: "Save profile" });
  fireEvent.submit(save.closest("form")!);
  expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Saving profile…");
  expect(onSaved).not.toHaveBeenCalled();
  fireEvent.submit(save.closest("form")!);
  expect(request).toHaveBeenCalledTimes(1);

  await act(async () => { resolveSave({}); });
  expect(onSaved).toHaveBeenCalledTimes(1);
});

it("preserves profile values on failure and permits an explicit retry", async () => {
  request.mockRejectedValueOnce(new Error("offline"));
  const onSaved = vi.fn();
  render(<ProfileForm profile={null} onSaved={onSaved} onFeedbackClear={() => {}} />);
  const headline = screen.getByRole("textbox", { name: "Headline" });
  fireEvent.change(headline, { target: { value: "Synthetic profile headline" } });
  fireEvent.submit(screen.getByRole("button", { name: "Save profile" }).closest("form")!);

  expect(await screen.findByRole("alert")).toHaveTextContent("Profile could not be saved.");
  expect(headline).toHaveValue("Synthetic profile headline");
  expect(screen.getByRole("button", { name: "Save profile" })).toBeEnabled();
  expect(onSaved).not.toHaveBeenCalled();

  fireEvent.submit(screen.getByRole("button", { name: "Save profile" }).closest("form")!);
  await vi.waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
});

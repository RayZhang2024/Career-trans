import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation, useNavigationType } from "react-router-dom";
import { App, ProfileFamilyShell, ProfileHome } from "./App";
import { AdviserPage } from "./AdviserPage";
import { CvPage } from "./CvPage";

const request = vi.fn(async (..._args: unknown[]): Promise<unknown> => ({}));
vi.mock("./auth", () => ({
  useAuth: () => ({ api: { request }, logout: vi.fn(), user: { email: "person@example.com" }, status: "authenticated" }),
  ApiError: class ApiError extends Error { constructor(public status: number, message = "Request failed") { super(message); } },
}));

const onboarding = { profile_exists: false, candidate_context_ready: false, latest_cv_draft: null, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 0 } };
const snapshot = { profile: null, structured_profile: null, active_evidence: [], adviser_intake: null, eligibility: { work_authorisation: [], security_clearances: [], locations: [] }, adviser_assessment: null, adviser_assessment_status: "not_available", readiness: { structured_profile_available: false, ready_for_candidate_context: false, evidence_materialization_status: "not_applicable", expected_evidence_count: 0, materialized_evidence_count: 0, missing_evidence_count: 0, stale_evidence_count: 0, latest_cv_draft_state: null } };

function renderShell(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><ProfileFamilyShell><main>Profile content</main></ProfileFamilyShell></MemoryRouter>);
}

function RouteLocation() {
  return <output aria-label="Route location">{useLocation().pathname}:{useNavigationType()}</output>;
}

function renderApp(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><App /><RouteLocation /></MemoryRouter>);
}

afterEach(() => { cleanup(); request.mockReset(); request.mockResolvedValue({}); });

describe("Profile secondary navigation", () => {
  it.each([
    ["/profile", "Overview"],
    ["/profile/cv", "CV"],
    ["/profile/adviser", "Career Adviser"],
  ])("renders the canonical Profile sections with %s active", (path, active) => {
    renderShell(path);
    const secondary = screen.getByRole("navigation", { name: "Profile sections" });
    expect(within(secondary).getAllByRole("link")).toHaveLength(3);
    expect(within(secondary).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Overview", "/profile"], ["CV", "/profile/cv"], ["Career Adviser", "/profile/adviser"],
    ]);
    expect(within(secondary).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page").map((link) => link.textContent)).toEqual([active]);
    if (active === "Overview") expect(within(secondary).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    else expect(within(secondary).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    const primary = screen.getByRole("navigation", { name: "Workspace" });
    expect(within(primary).getAllByRole("link").map((link) => link.textContent)).toEqual(["Home", "Profile", "Job Search", "Applications", "Tracking", "Settings"]);
    expect(within(primary).getByRole("link", { name: "Profile" })).toHaveAttribute("aria-current", "page");
  });

  it("renders without making readiness or provider requests", () => {
    renderShell("/profile/cv");
    expect(request).not.toHaveBeenCalled();
  });

  it("stays visible while Profile is loading and after its data read fails", async () => {
    let rejectSnapshot!: (reason?: unknown) => void;
    request.mockImplementation(async (path: unknown) => {
      const requestPath = String(path);
      if (requestPath === "/api/v1/profile/snapshot") return new Promise((_, reject) => { rejectSnapshot = reject; });
      return new Promise(() => undefined);
    });
    render(<MemoryRouter><ProfileHome /></MemoryRouter>);
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
    expect(screen.getByText("Loading your career profile…")).toBeInTheDocument();
    rejectSnapshot(new Error("offline"));
    expect(await screen.findByText("Your career profile could not be loaded.")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
  });

  it("stays visible in the empty Profile state", async () => {
    request.mockImplementation(async (path: unknown) => String(path) === "/api/v1/profile/snapshot" ? snapshot : null);
    render(<MemoryRouter><ProfileHome /></MemoryRouter>);
    expect(await screen.findByText("Let’s build your career profile")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
  });

  it("stays visible through CV loading, error, and empty states", async () => {
    let resolveStatus!: (value: unknown) => void;
    request.mockImplementation(async () => new Promise((resolve) => { resolveStatus = resolve; }));
    render(<MemoryRouter><ProfileFamilyShell><CvPage /></ProfileFamilyShell></MemoryRouter>);
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
    expect(screen.getByText("Loading CV onboarding…")).toBeInTheDocument();
    resolveStatus(onboarding);
    expect(await screen.findByRole("heading", { name: "Upload your CV" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
  });

  it("stays visible when CV status is unavailable", async () => {
    request.mockRejectedValue(new Error("offline"));
    render(<MemoryRouter><ProfileFamilyShell><CvPage /></ProfileFamilyShell></MemoryRouter>);
    expect(await screen.findByText("CV status is unavailable. Please retry.")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
  });

  it("stays visible through Adviser loading and readiness-blocked states", async () => {
    let resolveStatus!: (value: unknown) => void;
    request.mockImplementation(async () => new Promise((resolve) => { resolveStatus = resolve; }));
    render(<MemoryRouter><ProfileFamilyShell><AdviserPage /></ProfileFamilyShell></MemoryRouter>);
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
    expect(screen.getByText("Loading Career Adviser…")).toBeInTheDocument();
    resolveStatus(onboarding);
    expect(await screen.findByRole("heading", { name: "Complete your CV first" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("stays visible when Adviser status is unavailable", async () => {
    request.mockRejectedValue(new Error("offline"));
    render(<MemoryRouter><ProfileFamilyShell><AdviserPage /></ProfileFamilyShell></MemoryRouter>);
    expect(await screen.findByText("Adviser status is unavailable.")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
  });

  it.each([
    ["/", "/profile", "Your career profile"],
    ["/cv", "/profile/cv", "Upload your CV"],
    ["/adviser", "/profile/adviser", "Complete your CV first"],
  ])("preserves the %s compatibility route as a replace redirect", async (legacy, canonical, content) => {
    request.mockImplementation(async (path: unknown) => { const requestPath = String(path); return requestPath === "/api/v1/profile/snapshot" ? snapshot : requestPath === "/api/v1/profile/revisions/active" ? null : onboarding; });
    renderApp(legacy);
    expect(await screen.findByRole("heading", { name: content })).toBeInTheDocument();
    expect(screen.getByLabelText("Route location")).toHaveTextContent(`${canonical}:REPLACE`);
    expect(screen.getByRole("navigation", { name: "Profile sections" })).toBeInTheDocument();
  });
});

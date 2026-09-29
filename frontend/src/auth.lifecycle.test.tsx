import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { AuthProvider } from "./auth";

const TOKEN = "career-trans.access-token";
const user = { id: "user-1", email: "person@example.test", created_at: "2026-01-01T00:00:00Z" };
const status = {
  profile_exists: false,
  candidate_context_ready: false,
  latest_cv_draft: null,
  adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 0 },
};
const snapshot = (profile: unknown = null) => ({
  profile, structured_profile: null, active_evidence: [], adviser_intake: null,
  eligibility: { work_authorisation: [], security_clearances: [], locations: [] },
  adviser_assessment: null, adviser_assessment_status: "not_available",
  readiness: { structured_profile_available: false, ready_for_candidate_context: false, evidence_materialization_status: "not_applicable", expected_evidence_count: 0, materialized_evidence_count: 0, missing_evidence_count: 0, stale_evidence_count: 0, latest_cv_draft_state: null },
});
const readyRevision = () => ({
  id: "revision-1", state: "review_ready", revision: 3,
  proposed_profile: { display_name: null, headline: "Confirmed", current_role: null, location: null, summary: null, career_goal: null, job_search_criteria: null, preferred_email: null, phone: null, linkedin_url: null, github_url: null, portfolio_url: null },
  proposed_structured: null, changed_authorities: ["profile"], stale_authorities: [],
  created_at: "", updated_at: "", confirmed_at: null, discarded_at: null,
});
const passwordPolicy = { version: 2, min_length: 8, max_length: 128, common_passwords_rejected: true, composition_requirements: [], whitespace_allowed: false };

function response(value: unknown, statusCode = 200): Response {
  return new Response(value === undefined ? "" : JSON.stringify(value), { status: statusCode });
}

function renderApp(path = "/") {
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
}

function passwordInput(): HTMLInputElement {
  const input = document.querySelector('input[name="password"]');
  if (!(input instanceof HTMLInputElement)) throw new Error("Password input missing");
  return input;
}

function authenticatedFetch(overrides: Record<string, () => Response | Promise<Response>> = {}) {
  return vi.fn((url: string, init?: RequestInit) => {
    const path = new URL(url, window.location.origin).pathname;
    if (overrides[path]) return overrides[path]();
    if (path === "/api/v1/users/me") return Promise.resolve(response(user));
    if (path === "/api/v1/onboarding/status") return Promise.resolve(response(status));
    if (path === "/api/v1/profile/snapshot") return Promise.resolve(response(snapshot()));
    if (path === "/api/v1/profile/revisions/active") return Promise.resolve(response(null));
    if (path === "/api/v1/profile/revisions" && init?.method === "POST") return Promise.resolve(response({
      id: "revision-1", state: "draft", revision: 1, proposed_profile: null, proposed_structured: null,
      changed_authorities: [], stale_authorities: [], created_at: "", updated_at: "", confirmed_at: null, discarded_at: null,
    }));
    throw new Error(`Unexpected request: ${path}`);
  });
}

beforeEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});
afterEach(cleanup);

describe("AuthProvider routed lifecycle", () => {
  it("protects the Adviser route for an unauthenticated session", async () => {
    vi.stubGlobal("fetch", vi.fn());
    renderApp("/adviser");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("exposes the Career Adviser link when confirmed candidate context is ready", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    const readyStatus = { ...status, candidate_context_ready: true, latest_cv_draft: { id: "draft", state: "confirmed", created_at: "", updated_at: "" } };
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/onboarding/status": () => response(readyStatus) }));
    renderApp();
    expect(await screen.findByRole("link", { name: "Start Career Adviser" })).toHaveAttribute("href", "/profile/adviser");
  });

  it("keeps protected UI in checking state without a login flicker", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let resolve!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
    renderApp();
    expect(screen.getByText("Checking your session…")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    resolve(response(user));
    await screen.findByRole("heading", { name: "Your career profile" });
  });

  it("restores a valid stored session and preserves its token", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch());
    renderApp();
    await screen.findByRole("heading", { name: "Your career profile" });
    expect(sessionStorage.getItem(TOKEN)).toBe("stored-token");
  });

  it("clears only an invalid stored session", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/users/me": () => response(undefined, 401) }));
    renderApp();
    await screen.findByRole("heading", { name: "Sign in" });
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it.each([500, 503])("keeps a token and provides retry UI for a %s bootstrap failure", async (statusCode) => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/users/me": () => response(undefined, statusCode) }));
    renderApp();
    await screen.findByText(/Session validation is temporarily unavailable/);
    expect(sessionStorage.getItem(TOKEN)).toBe("stored-token");
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("retries a transient bootstrap failure without discarding the original token", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let attempts = 0;
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/users/me": () => {
      attempts += 1;
      return attempts === 1 ? response(undefined, 503) : response(user);
    } }));
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { name: "Your career profile" });
    expect(sessionStorage.getItem(TOKEN)).toBe("stored-token");
  });

  it("recovers a rejected bootstrap fetch without discarding the original token", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let attempts = 0;
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/users/me": () => {
      attempts += 1;
      return attempts === 1 ? Promise.reject(new TypeError("network unavailable")) : response(user);
    } }));
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { name: "Your career profile" });
    expect(sessionStorage.getItem(TOKEN)).toBe("stored-token");
  });

  it("clears the session when retry proves it is invalid", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let attempts = 0;
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/users/me": () => {
      attempts += 1;
      return attempts === 1 ? response(undefined, 503) : response(undefined, 401);
    } }));
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it("registers without creating a session and reports safe registration errors", async () => {
    const fetch = vi.fn((url: string, _init?: RequestInit) => {
      const path = new URL(url, window.location.origin).pathname;
      if (path === "/api/v1/auth/password-policy") return Promise.resolve(response(passwordPolicy));
      if (path === "/api/v1/auth/register") return Promise.resolve(response({}, 201));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("/register");
    expect(await screen.findByText(/Use at least 8 characters/)).toBeInTheDocument();
    expect(screen.getByText("Met: No whitespace characters")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "alongandordinaryphrase" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "alongandordinaryphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
    expect(JSON.parse(fetch.mock.calls.find(([url]) => String(url).includes("/auth/register"))?.[1]?.body as string)).toEqual({ email: "new@example.test", password: "alongandordinaryphrase" });
  });

  it.each([[409, "An account already exists for this email."], [422, "Registration is unavailable."]])(
    "shows a safe registration error for %s", async (statusCode, message) => {
      vi.stubGlobal("fetch", vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(passwordPolicy) : response(undefined, statusCode)));
      renderApp("/register");
      await screen.findByText(/Use at least 8 characters/);
      fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.test" } });
      fireEvent.change(screen.getByLabelText("Password"), { target: { value: "alongandordinaryphrase" } });
      fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "alongandordinaryphrase" } });
      fireEvent.click(screen.getByRole("button", { name: "Create account" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(sessionStorage.getItem(TOKEN)).toBeNull();
    },
  );

  it("loads the public contract before displaying requirements and blocks submission on load failure", async () => {
    const fetch = vi.fn(async () => { throw new Error("offline"); });
    vi.stubGlobal("fetch", fetch);
    renderApp("/register");
    expect(await screen.findByRole("alert")).toHaveTextContent("Password requirements could not be loaded");
    expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v1/auth/password-policy"), expect.anything());
  });

  it("uses policy length values from the endpoint and updates both local length indicators", async () => {
    const customPolicy = { ...passwordPolicy, min_length: 12, max_length: 16 };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(customPolicy) : response({}, 201)));
    renderApp("/register");
    expect(await screen.findByText(/Use at least 12 characters/)).toBeInTheDocument();
    expect(screen.getByText("Not met: At least 12 characters")).toBeInTheDocument();
    expect(screen.getByText("Met: No more than 16 characters")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "short" } });
    expect(screen.getByText("Not met: At least 12 characters")).toBeInTheDocument();
    expect(screen.getByText("Met: No more than 16 characters")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "12345678901234567" } });
    expect(screen.getByText("Met: At least 12 characters")).toBeInTheDocument();
    expect(screen.getByText("Not met: No more than 16 characters")).toBeInTheDocument();
  });

  it("updates the whitespace rule as the password changes", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(passwordPolicy) : response({}, 201)));
    renderApp("/register");
    await screen.findByText(/Use at least 8 characters/);
    const password = screen.getByLabelText("Password");
    expect(screen.getByText("Met: No whitespace characters")).toBeInTheDocument();
    fireEvent.change(password, { target: { value: "abc defg" } });
    expect(screen.getByText("Not met: No whitespace characters")).toBeInTheDocument();
    fireEvent.change(password, { target: { value: "abcdefgh" } });
    expect(screen.getByText("Met: No whitespace characters")).toBeInTheDocument();
  });

  it("shows the single unmet password rule on submit and does not call registration", async () => {
    const fetch = vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(passwordPolicy) : response({}, 201));
    vi.stubGlobal("fetch", fetch);
    renderApp("/register");
    await screen.findByText(/Use at least 8 characters/);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "short@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "abcdefg" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "abcdefg" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("At least 8 characters");
    expect(alert).not.toHaveTextContent("No whitespace characters");
    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-describedby", "password-policy password-validation-error");
    expect(fetch.mock.calls.some(([url]) => new URL(url, window.location.origin).pathname === "/api/v1/auth/register")).toBe(false);
  });

  it("shows password feedback for a blank value instead of browser-blocking silently", async () => {
    const fetch = vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(passwordPolicy) : response({}, 201));
    vi.stubGlobal("fetch", fetch);
    renderApp("/register");
    await screen.findByText(/Use at least 8 characters/);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "blank@example.test" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("At least 8 characters");
    expect(fetch.mock.calls.some(([url]) => new URL(url, window.location.origin).pathname === "/api/v1/auth/register")).toBe(false);
  });

  it("shows every unmet password rule on submit", async () => {
    const fetch = vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(passwordPolicy) : response({}, 201));
    vi.stubGlobal("fetch", fetch);
    renderApp("/register");
    await screen.findByText(/Use at least 8 characters/);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "multiple@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "abc d" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "abc d" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("At least 8 characters");
    expect(alert).toHaveTextContent("No whitespace characters");
    expect(fetch.mock.calls.some(([url]) => new URL(url, window.location.origin).pathname === "/api/v1/auth/register")).toBe(false);
  });

  it("counts Unicode code points when checking password length", async () => {
    const customPolicy = { ...passwordPolicy, min_length: 2, max_length: 2 };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(customPolicy) : response({}, 201)));
    renderApp("/register");
    await screen.findByText(/Use at least 2 characters/);
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "🙂" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "🙂" } });
    expect(screen.getByText("Not met: At least 2 characters")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "🙂🙂" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "🙂🙂" } });
    expect(screen.getByText("Met: At least 2 characters")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
  });

  it("requires confirmation after interaction and accepts long passphrases without composition checks", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(passwordPolicy) : response({}, 201)));
    renderApp("/register");
    await screen.findByText(/Use at least 8 characters/);
    const phrase = "anentirelyordinarylongpassphrase";
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: phrase } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "different phrase" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Passwords do not match.");
    expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: phrase } });
    expect(screen.queryByText("Passwords do not match.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
  });

  it("shows a safe message when the backend rejects a common password", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(passwordPolicy) : response({ detail: "password_too_common" }, 422)));
    renderApp("/register");
    await screen.findByText(/Use at least 8 characters/);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "preserved@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "password123456789" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "password123456789" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a less common password.");
    expect(screen.getByLabelText("Email")).toHaveValue("preserved@example.test");
    expect(screen.getByLabelText("Password")).toHaveValue("password123456789");
  });

  it("shows a meaningful message when the backend rejects a password policy rule", async () => {
    const stalePolicy = { ...passwordPolicy, min_length: 7 };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy" ? response(stalePolicy) : response({ detail: "password_too_short" }, 422)));
    renderApp("/register");
    await screen.findByText(/Use at least 7 characters/);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "rejected@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "abcdefg" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "abcdefg" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Use at least 7 characters.");
  });

  it("prevents duplicate registration requests and shows a pending state", async () => {
    let resolveRegistration!: (value: Response) => void;
    const fetch = vi.fn((url: string) => new URL(url, window.location.origin).pathname === "/api/v1/auth/password-policy"
      ? Promise.resolve(response(passwordPolicy))
      : new Promise<Response>((resolve) => { resolveRegistration = resolve; }));
    vi.stubGlobal("fetch", fetch);
    renderApp("/register");
    await screen.findByText(/Use at least 8 characters/);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "pending@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "thisisalongpassphrase" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "thisisalongpassphrase" } });
    const button = screen.getByRole("button", { name: "Create account" });
    fireEvent.click(button);
    expect(await screen.findByRole("button", { name: "Creating account…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Creating account…" }));
    expect(fetch.mock.calls.filter(([url]) => String(url).includes("/auth/register"))).toHaveLength(1);
    resolveRegistration(response({}, 201));
    await screen.findByRole("heading", { name: "Sign in" });
  });

  it("logs in, validates the returned token, and reaches the protected app", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      const path = new URL(url, window.location.origin).pathname;
      if (path === "/api/v1/auth/login") return Promise.resolve(response({ access_token: "new-token" }));
      if (path === "/api/v1/users/me") return Promise.resolve(response(user));
      if (path === "/api/v1/onboarding/status") return Promise.resolve(response(status));
      if (path === "/api/v1/profile/snapshot") return Promise.resolve(response(snapshot()));
      throw new Error(`Unexpected request: ${path}`);
    }));
    renderApp("/login");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "person@example.test" } });
    fireEvent.change(passwordInput(), { target: { value: "valid-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: "Home" });
    expect(sessionStorage.getItem(TOKEN)).toBe("new-token");
  });

  it("shows login pending feedback and prevents duplicate authentication requests", async () => {
    let resolveLogin!: (value: Response) => void;
    const fetch = vi.fn((url: string) => {
      const path = new URL(url, window.location.origin).pathname;
      if (path === "/api/v1/auth/login") return new Promise<Response>((done) => { resolveLogin = done; });
      if (path === "/api/v1/users/me") return Promise.resolve(response(user));
      if (path === "/api/v1/onboarding/status") return Promise.resolve(response(status));
      if (path === "/api/v1/profile/snapshot") return Promise.resolve(response(snapshot()));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("/login");
    fireEvent.change(screen.getByRole("textbox", { name: "Email" }), { target: { value: "person@example.test" } });
    fireEvent.change(passwordInput(), { target: { value: "valid-password" } });
    const form = screen.getByRole("button", { name: "Sign in" }).closest("form")!;
    fireEvent.submit(form);
    expect(await screen.findByRole("button", { name: "Signing in…" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Signing in…");
    expect(screen.getByRole("textbox", { name: "Email" })).toBeDisabled();
    expect(passwordInput()).toBeDisabled();
    fireEvent.submit(form);
    expect(fetch.mock.calls.filter(([url]) => new URL(url, window.location.origin).pathname === "/api/v1/auth/login")).toHaveLength(1);
    await act(async () => { resolveLogin(response({ access_token: "new-token" })); });
    expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
  });

  it("shows registration pending feedback and prevents duplicate registration requests", async () => {
    let resolveRegistration!: (value: Response) => void;
    const fetch = vi.fn((url: string) => {
      const path = new URL(url, window.location.origin).pathname;
      if (path === "/api/v1/auth/password-policy") return Promise.resolve(response(passwordPolicy));
      if (path === "/api/v1/auth/register") return new Promise<Response>((done) => { resolveRegistration = done; });
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("/register");
    await screen.findByText(/Use at least 8 characters/);
    fireEvent.change(screen.getByRole("textbox", { name: "Email" }), { target: { value: "new@example.test" } });
    fireEvent.change(passwordInput(), { target: { value: "a-long-enough-password" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "a-long-enough-password" } });
    const form = screen.getByRole("button", { name: "Create account" }).closest("form")!;
    fireEvent.submit(form);
    expect(await screen.findByRole("button", { name: "Creating account…" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Creating your account…");
    expect(screen.getByLabelText("Email")).toBeDisabled();
    expect(passwordInput()).toBeDisabled();
    expect(screen.getByLabelText("Confirm password")).toBeDisabled();
    fireEvent.submit(form);
    expect(fetch.mock.calls.filter(([url]) => new URL(url, window.location.origin).pathname === "/api/v1/auth/register")).toHaveLength(1);
    await act(async () => { resolveRegistration(response({}, 201)); });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("reports invalid credentials without recursively expiring an authenticated session", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(undefined, 401)));
    renderApp("/login");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "person@example.test" } });
    fireEvent.change(passwordInput(), { target: { value: "bad-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it("logs out and makes the protected UI unavailable", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch());
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it("keeps profile editing usable when onboarding status fails", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch({
      "/api/v1/onboarding/status": () => response(undefined, 503),
      "/api/v1/profile/snapshot": () => response(snapshot({ id: "p", user_id: "user-1", created_at: "", updated_at: "", headline: "Existing" })),
    }));
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Edit profile" }));
    await screen.findByRole("heading", { name: "Edit proposed profile changes" });
    expect(screen.getByRole("alert")).toHaveTextContent("Onboarding status is unavailable.");
  });

  it("does not infer profile absence from a server failure", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/profile/snapshot": () => response(undefined, 503) }));
    renderApp();
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.map((alert) => alert.textContent)).toContain("Your career profile could not be loaded.");
    expect(screen.getByText("Not saved yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit profile" })).toBeDisabled();
  });

  it("does not infer profile absence from a rejected fetch", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/profile/snapshot": () => Promise.reject(new TypeError("network unavailable")) }));
    renderApp();
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.map((alert) => alert.textContent)).toContain("Your career profile could not be loaded.");
    expect(screen.getByText("Not saved yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit profile" })).toBeDisabled();
  });

  it("clears an authenticated session after a later protected 401", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url, window.location.origin).pathname;
      if (path === "/api/v1/users/me") return Promise.resolve(response(user));
      if (path === "/api/v1/onboarding/status") return Promise.resolve(response(status));
      if (path === "/api/v1/profile/snapshot") return Promise.resolve(response(snapshot()));
      if (path === "/api/v1/profile/revisions/active") return Promise.resolve(response(null));
      if (path === "/api/v1/profile/revisions" && init?.method === "POST") return Promise.resolve(response(undefined, 401));
      throw new Error(`Unexpected request: ${path}`);
    }));
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Edit profile" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it("invalidates an in-flight response after logout", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let resolveSnapshot!: (value: Response) => void;
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/profile/snapshot": () => new Promise<Response>((done) => { resolveSnapshot = done; }) }));
    renderApp();
    await screen.findByRole("heading", { name: "Your career profile" });
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    resolveSnapshot(response(snapshot({ id: "p", user_id: "user-1", created_at: "", updated_at: "", headline: "late" })));
    await screen.findByRole("heading", { name: "Sign in" });
    await waitFor(() => expect(screen.queryByText("late")).not.toBeInTheDocument());
  });

  it("renders labelled auth controls and router navigation links", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderApp("/login");
    expect(screen.getByLabelText("Email")).toHaveAttribute("name", "email");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autoComplete", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute("name", "password");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autoComplete", "current-password");
    expect(screen.getByRole("link", { name: "Create account" })).toHaveAttribute("href", "/register");
  });

  it("renders labelled register controls and a sign-in router link", () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(passwordPolicy)));
    renderApp("/register");
    expect(screen.getByLabelText("Email")).toHaveAttribute("name", "email");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autoComplete", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute("name", "password");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autoComplete", "new-password");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute("autoComplete", "new-password");
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("keeps pending onboarding neutral instead of implying a negative backend profile state", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let resolveStatus!: (value: Response) => void;
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/onboarding/status": () => new Promise<Response>((done) => { resolveStatus = done; }) }));
    renderApp();
    await screen.findByRole("button", { name: "Edit profile" });
    expect(screen.getByText("Loading onboarding status…")).toBeInTheDocument();
    expect(screen.queryByText("Not saved yet")).not.toBeInTheDocument();
    resolveStatus(response(status));
    await screen.findByText("Not saved yet");
  });

  it("keeps profile and onboarding failures independently visible", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch({
      "/api/v1/onboarding/status": () => response(undefined, 503),
      "/api/v1/profile/snapshot": () => Promise.reject(new TypeError("network unavailable")),
    }));
    renderApp();
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.map((alert) => alert.textContent)).toEqual(expect.arrayContaining(["Onboarding status is unavailable.", "Your career profile could not be loaded."]));
    expect(screen.queryByRole("heading", { name: "Create profile" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Edit profile" })).not.toBeInTheDocument();
  });

  it("keeps the newest onboarding refresh authoritative when an older request resolves late", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let resolveInitialStatus!: (value: Response) => void;
    let statusCalls = 0;
    let activeReads = 0;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url, window.location.origin).pathname;
      if (path === "/api/v1/users/me") return Promise.resolve(response(user));
      if (path === "/api/v1/onboarding/status") {
        statusCalls += 1;
        if (statusCalls === 1) return new Promise<Response>((done) => { resolveInitialStatus = done; });
        return Promise.resolve(response({ ...status, profile_exists: true }));
      }
      if (path === "/api/v1/profile/revisions/active") { activeReads += 1; return Promise.resolve(response(activeReads === 1 ? readyRevision() : null)); }
      if (path === "/api/v1/profile/revisions/revision-1/confirm" && init?.method === "POST") return Promise.resolve(response(readyRevision()));
      if (path === "/api/v1/profile/snapshot") return Promise.resolve(response(snapshot({ id: "p", user_id: "user-1", created_at: "", updated_at: "", headline: "Confirmed" })));
      throw new Error(`Unexpected request: ${path}`);
    }));
    renderApp();
    await screen.findByRole("button", { name: "Review changes" });
    expect(screen.getByText("Loading onboarding status…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm changes" }));
    await screen.findByText("Saved");
    resolveInitialStatus(response(status));
    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument());
    expect(screen.queryByText("Not saved yet")).not.toBeInTheDocument();
  });

  it("does not render stale negative onboarding state when the post-confirm status refresh fails", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let statusCalls = 0;
    let snapshotReads = 0;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url, window.location.origin).pathname;
      if (path === "/api/v1/users/me") return Promise.resolve(response(user));
      if (path === "/api/v1/onboarding/status") {
        statusCalls += 1;
        return Promise.resolve(statusCalls === 1 ? response(status) : response(undefined, 503));
      }
      if (path === "/api/v1/profile/revisions/active") return Promise.resolve(response(statusCalls > 1 ? null : readyRevision()));
      if (path === "/api/v1/profile/revisions/revision-1/confirm" && init?.method === "POST") return Promise.resolve(response(readyRevision()));
      if (path === "/api/v1/profile/snapshot") { snapshotReads += 1; return Promise.resolve(response(snapshot({ id: "p", user_id: "user-1", created_at: "", updated_at: "", headline: snapshotReads > 1 ? "Confirmed" : "Before" }))); }
      throw new Error(`Unexpected request: ${path}`);
    }));
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Onboarding status is unavailable.");
    await screen.findByRole("heading", { name: "Saved profile details" });
    expect(screen.queryByText("Not saved yet")).not.toBeInTheDocument();
  });

  it("does not render an editable proposal as current when the post-confirm profile refresh fails", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let statusCalls = 0;
    let snapshotReads = 0;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url, window.location.origin).pathname;
      if (path === "/api/v1/users/me") return Promise.resolve(response(user));
      if (path === "/api/v1/onboarding/status") {
        statusCalls += 1;
        return Promise.resolve(response(statusCalls === 1 ? status : { ...status, profile_exists: true }));
      }
      if (path === "/api/v1/profile/revisions/active") return Promise.resolve(response(statusCalls > 1 ? null : readyRevision()));
      if (path === "/api/v1/profile/revisions/revision-1/confirm" && init?.method === "POST") return Promise.resolve(response(readyRevision()));
      if (path === "/api/v1/profile/snapshot") { snapshotReads += 1; return Promise.resolve(snapshotReads === 1 ? response(snapshot()) : response(undefined, 503)); }
      throw new Error(`Unexpected request: ${path}`);
    }));
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm changes" }));
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.map((alert) => alert.textContent)).toContain("Your career profile could not be loaded.");
    expect(screen.getByRole("button", { name: "Edit profile" })).toBeDisabled();
    expect(screen.getByText("Saved")).toBeInTheDocument();
  });

  it("shows confirmation feedback only after the refreshed canonical snapshot resolves", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let resolveRefreshedSnapshot!: (value: Response) => void;
    let snapshotReads = 0;
    let activeReads = 0;
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url, window.location.origin).pathname;
      if (path === "/api/v1/users/me") return Promise.resolve(response(user));
      if (path === "/api/v1/onboarding/status") return Promise.resolve(response(status));
      if (path === "/api/v1/profile/revisions/active") { activeReads += 1; return Promise.resolve(response(activeReads === 1 ? readyRevision() : null)); }
      if (path === "/api/v1/profile/revisions/revision-1/confirm" && init?.method === "POST") return Promise.resolve(response(readyRevision()));
      if (path === "/api/v1/profile/snapshot") { snapshotReads += 1; return snapshotReads === 1 ? Promise.resolve(response(snapshot())) : new Promise<Response>((done) => { resolveRefreshedSnapshot = done; }); }
      throw new Error(`Unexpected request: ${path}`);
    }));
    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "Review changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm changes" }));
    await waitFor(() => expect(snapshotReads).toBe(2));
    expect(screen.queryByText(/Profile changes confirmed/)).not.toBeInTheDocument();
    await act(async () => { resolveRefreshedSnapshot(response(snapshot({ id: "p", user_id: "user-1", created_at: "", updated_at: "", headline: "Confirmed" }))); });
    expect(await screen.findByText(/Profile changes confirmed/)).toBeInTheDocument();
  });

  it("protects /cv and resumes the authenticated CV page", async () => {
    renderApp("/cv");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();

    cleanup();
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch());
    renderApp("/cv");
    expect(await screen.findByRole("heading", { name: "Upload your CV" })).toBeInTheDocument();
  });

  it("links the CV stage while keeping Career Adviser non-interactive", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch());
    renderApp();
    const cvLinks = await screen.findAllByRole("link", { name: "Continue CV onboarding" });
    expect(cvLinks.length).toBeGreaterThanOrEqual(1);
    expect(cvLinks.every((link) => link.getAttribute("href") === "/profile/cv")).toBe(true);
    expect(screen.getByText("Complete CV first").closest("li")).toBeInTheDocument();
  });
});

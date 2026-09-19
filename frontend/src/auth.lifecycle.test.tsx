import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  return vi.fn((url: string) => {
    const path = new URL(url).pathname;
    if (overrides[path]) return overrides[path]();
    if (path === "/api/v1/users/me") return Promise.resolve(response(user));
    if (path === "/api/v1/onboarding/status") return Promise.resolve(response(status));
    if (path === "/api/v1/profile") return Promise.resolve(response({}, 404));
    throw new Error(`Unexpected request: ${path}`);
  });
}

beforeEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});
afterEach(cleanup);

describe("AuthProvider routed lifecycle", () => {
  it("keeps protected UI in checking state without a login flicker", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let resolve!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
    renderApp();
    expect(screen.getByText("Checking your session…")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    resolve(response(user));
    await screen.findByText("Welcome person@example.test");
  });

  it("restores a valid stored session and preserves its token", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch());
    renderApp();
    await screen.findByText("Welcome person@example.test");
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
    await screen.findByText("Welcome person@example.test");
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
    const fetch = vi.fn((url: string) => {
      const path = new URL(url).pathname;
      if (path === "/api/v1/auth/register") return Promise.resolve(response({}, 201));
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("/register");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "new@example.test" } });
    fireEvent.change(passwordInput(), { target: { value: "a-long-enough-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it.each([[409, "An account already exists for this email."], [422, "Registration is unavailable."]])(
    "shows a safe registration error for %s", async (statusCode, message) => {
      vi.stubGlobal("fetch", vi.fn(async () => response(undefined, statusCode)));
      renderApp("/register");
      fireEvent.change(screen.getByRole("textbox"), { target: { value: "new@example.test" } });
      fireEvent.change(passwordInput(), { target: { value: "a-long-enough-password" } });
      fireEvent.click(screen.getByRole("button", { name: "Create account" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(sessionStorage.getItem(TOKEN)).toBeNull();
    },
  );

  it("logs in, validates the returned token, and reaches the protected app", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      const path = new URL(url).pathname;
      if (path === "/api/v1/auth/login") return Promise.resolve(response({ access_token: "new-token" }));
      if (path === "/api/v1/users/me") return Promise.resolve(response(user));
      if (path === "/api/v1/onboarding/status") return Promise.resolve(response(status));
      if (path === "/api/v1/profile") return Promise.resolve(response({}, 404));
      throw new Error(`Unexpected request: ${path}`);
    }));
    renderApp("/login");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "person@example.test" } });
    fireEvent.change(passwordInput(), { target: { value: "valid-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText("Welcome person@example.test");
    expect(sessionStorage.getItem(TOKEN)).toBe("new-token");
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
      "/api/v1/profile": () => response({ id: "p", user_id: "user-1", created_at: "", updated_at: "", headline: "Existing" }),
    }));
    renderApp();
    await screen.findByRole("heading", { name: "Edit profile" });
    expect(screen.getByRole("alert")).toHaveTextContent("onboarding status is unavailable");
  });

  it("keeps the onboarding stepper usable when profile loading fails", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/profile": () => response(undefined, 503) }));
    renderApp();
    expect(await screen.findByRole("alert")).toHaveTextContent("profile is unavailable");
    expect(screen.getByText("○ Profile")).toBeInTheDocument();
  });

  it("clears an authenticated session after a later protected 401", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let profileCalls = 0;
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/profile": () => {
      profileCalls += 1;
      return profileCalls === 1 ? response({}, 404) : response(undefined, 401);
    } }));
    renderApp();
    await screen.findByRole("heading", { name: "Create profile" });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
  });

  it("invalidates an in-flight response after logout", async () => {
    sessionStorage.setItem(TOKEN, "stored-token");
    let resolveProfile!: (value: Response) => void;
    vi.stubGlobal("fetch", authenticatedFetch({ "/api/v1/profile": () => new Promise<Response>((done) => { resolveProfile = done; }) }));
    renderApp();
    await screen.findByText("Welcome person@example.test");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    resolveProfile(response({ id: "p", user_id: "user-1", created_at: "", updated_at: "", headline: "late" }));
    await screen.findByRole("heading", { name: "Sign in" });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Edit profile" })).not.toBeInTheDocument());
  });
});

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { SessionApi } from "./api";
import { App } from "./App";
import { AuthProvider, useAuth } from "./auth";

const TOKEN = "career-trans.access-token";
const policy = {
  version: 2, min_length: 8, max_length: 128, common_passwords_rejected: true,
  composition_requirements: [], whitespace_allowed: false,
};
const owner = { id: "owner-1", email: "owner@example.test", created_at: "2026-01-01T00:00:00Z" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

let calls: Array<{ path: string; method: string; body: string | undefined }> = [];
let changeResponse = async () => json({ changed: true });
let capturedApi: SessionApi | null = null;

function CaptureApi() {
  capturedApi = useAuth().api;
  return null;
}

function renderSecurity() {
  return render(<MemoryRouter initialEntries={["/settings/security"]}><AuthProvider><CaptureApi /><App /></AuthProvider></MemoryRouter>);
}

function setPasswordValues(current: string, next: string, confirmation: string) {
  fireEvent.change(screen.getByLabelText("Current password"), { target: { value: current } });
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: next } });
  fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: confirmation } });
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear(); calls = []; capturedApi = null;
  sessionStorage.setItem(TOKEN, "old-session-token");
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const method = init?.method ?? "GET";
    calls.push({ path: url.pathname, method, body: typeof init?.body === "string" ? init.body : undefined });
    if (url.pathname === "/api/v1/users/me") return Promise.resolve(json(owner));
    if (url.pathname === "/api/v1/auth/password-policy") return Promise.resolve(json(policy));
    if (url.pathname === "/api/v1/auth/change-password") return changeResponse();
    throw new Error(`Unexpected request: ${method} ${url.pathname}`);
  }));
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Issue #298 Account Security", () => {
  it("loads the shared policy and offers masked, password-manager-friendly fields", async () => {
    renderSecurity();
    expect(await screen.findByRole("heading", { name: "Account Security" })).toBeInTheDocument();
    expect(screen.getByLabelText("Current password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Current password")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("New password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Confirm new password")).toHaveAttribute("autocomplete", "new-password");
    expect(await screen.findByText(/Use 8–128 Unicode characters, with no whitespace/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AI Models" })).toHaveAttribute("href", "/settings/ai");
    expect(screen.getByRole("link", { name: "Job Discovery" })).toHaveAttribute("href", "/settings/discovery");
  });

  it("sends only current and new password, then clears this session and shows a truthful sign-in notice", async () => {
    changeResponse = async () => json({ changed: true });
    renderSecurity();
    await screen.findByRole("heading", { name: "Account Security" });
    await waitFor(() => expect(screen.getByLabelText("Current password")).not.toBeDisabled());
    setPasswordValues("old secret", "Better-password-42", "Better-password-42");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Password updated. Sign in again; existing sessions have been signed out.");
    const mutation = calls.find((call) => call.path === "/api/v1/auth/change-password");
    expect(mutation?.method).toBe("POST");
    expect(JSON.parse(mutation?.body ?? "{}" )).toEqual({ current_password: "old secret", new_password: "Better-password-42" });
    expect(mutation?.body).not.toContain("confirm");
    expect(sessionStorage.getItem(TOKEN)).toBeNull();
    expect(localStorage.length).toBe(0);
  });

  it("keeps correction available after a definite current-password rejection", async () => {
    changeResponse = async () => json({ detail: "current_password_incorrect" }, 400);
    renderSecurity();
    await screen.findByRole("heading", { name: "Account Security" });
    await waitFor(() => expect(screen.getByLabelText("Current password")).not.toBeDisabled());
    setPasswordValues("wrong", "Better-password-42", "Better-password-42");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Current password is incorrect.");
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.getByLabelText("New password")).toHaveValue("Better-password-42");
    expect(screen.getByRole("button", { name: "Change password" })).toBeInTheDocument();
    expect(sessionStorage.getItem(TOKEN)).toBe("old-session-token");
  });

  it("blocks mismatched confirmation in the browser without sending a request", async () => {
    changeResponse = async () => json({ changed: true });
    renderSecurity();
    await screen.findByRole("heading", { name: "Account Security" });
    await waitFor(() => expect(screen.getByLabelText("Current password")).not.toBeDisabled());
    setPasswordValues("old secret", "Better-password-42", "Different-password-42");
    expect(screen.getByRole("alert")).toHaveTextContent("The new password and confirmation do not match.");
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
    expect(calls.some((call) => call.path === "/api/v1/auth/change-password")).toBe(false);
  });

  it("reports an expired authenticated session at sign-in", async () => {
    changeResponse = async () => json({ detail: "Invalid or expired authentication token." }, 401);
    renderSecurity();
    await screen.findByRole("heading", { name: "Account Security" });
    await waitFor(() => expect(screen.getByLabelText("Current password")).not.toBeDisabled());
    setPasswordValues("old secret", "Better-password-42", "Better-password-42");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session expired. Sign in again to continue.");
  });

  it("does not apply an interrupted response after the session epoch changes", async () => {
    let resolve: ((response: Response) => void) | undefined;
    changeResponse = () => new Promise<Response>((done) => { resolve = done; });
    renderSecurity();
    await screen.findByRole("heading", { name: "Account Security" });
    await waitFor(() => expect(screen.getByLabelText("Current password")).not.toBeDisabled());
    setPasswordValues("old secret", "Better-password-42", "Better-password-42");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(calls.some((call) => call.path === "/api/v1/auth/change-password")).toBe(true));
    expect(capturedApi).not.toBeNull();
    act(() => capturedApi?.replaceToken("replacement-session-token"));
    await act(async () => { resolve?.(json({ changed: true })); });
    await waitFor(() => expect(screen.getByRole("heading", { name: "Account Security" })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Password updated/)).not.toBeInTheDocument();
  });

  it("does not retry an uncertain network outcome and explains how to check safely", async () => {
    changeResponse = async () => { throw new TypeError("network interrupted"); };
    renderSecurity();
    await screen.findByRole("heading", { name: "Account Security" });
    await waitFor(() => expect(screen.getByLabelText("Current password")).not.toBeDisabled());
    setPasswordValues("old secret", "Better-password-42", "Better-password-42");
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not confirm the password change/);
    expect(calls.filter((call) => call.path === "/api/v1/auth/change-password")).toHaveLength(1);
    expect(screen.getByLabelText("Current password")).toHaveValue("old secret");
    expect(sessionStorage.getItem(TOKEN)).toBe("old-session-token");
  });
});

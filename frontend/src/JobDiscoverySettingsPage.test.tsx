import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "./auth";
import { App } from "./App";
import type { JobDiscoverySettings } from "./api";

const TOKEN = "career-trans.access-token";
const configured = (overrides: Partial<JobDiscoverySettings> = {}): JobDiscoverySettings => ({
  revision: 0, provider_override: null, deployment_provider: "brave", effective_provider: "brave",
  tavily_credential_configured: false, tavily_credential_source: null,
  tavily_user_credential_storage_available: true, tavily_credential_usable: false, ...overrides,
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function renderDiscovery() {
  const calls: Array<{ method: string; path: string; body?: string }> = [];
  sessionStorage.setItem(TOKEN, "session-token");
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const method = init?.method ?? "GET";
    calls.push({ method, path: url.pathname, body: typeof init?.body === "string" ? init.body : undefined });
    if (url.pathname === "/api/v1/users/me") return Promise.resolve(json({ id: "owner", email: "owner@example.test", created_at: "2026-01-01T00:00:00Z" }));
    if (url.pathname === "/api/v1/job-discovery/settings" && method === "PUT") return Promise.resolve(json(configured({ revision: 1, provider_override: "disabled", effective_provider: "disabled" })));
    if (url.pathname === "/api/v1/job-discovery/settings") return Promise.resolve(json(configured()));
    if (url.pathname === "/api/v1/job-discovery/tavily-credential") return Promise.resolve(json(configured({ revision: 1, tavily_credential_configured: true, tavily_credential_source: "user", tavily_credential_usable: true })));
    if (url.pathname === "/api/v1/job-discovery/tavily-connection-test") return Promise.resolve(json({ success: true, credential_source: "user", message: "Tavily connection test succeeded. This test used one Basic Search request." }));
    throw new Error(`Unexpected request: ${method} ${url.pathname}`);
  }));
  render(<MemoryRouter initialEntries={["/settings/discovery"]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
  return calls;
}

beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });
afterEach(cleanup);

describe("Issue #256 Settings → Job Discovery", () => {
  it("shows separate discovery settings, inherited provider, and the semantic-AI boundary", async () => {
    renderDiscovery();
    expect(await screen.findByRole("heading", { name: "Job Discovery" })).toBeInTheDocument();
    const provider = await screen.findByText("Brave Search");
    expect(provider.closest("p")).toHaveTextContent("Effective provider: Brave Search (deployment default).");
    expect(screen.getByText(/AI Models settings independently control semantic strategy generation/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AI Models" })).toHaveAttribute("href", "/settings/ai");
    expect(screen.getByRole("link", { name: "Job Discovery" })).toHaveAttribute("aria-current", "page");
  });

  it("saves the key without retaining or displaying it and can test the connection", async () => {
    const calls = renderDiscovery();
    await screen.findByRole("heading", { name: "Job Discovery" });
    const input = await screen.findByLabelText("Tavily API key");
    fireEvent.change(input, { target: { value: "private-test-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));
    await screen.findByText("Tavily key saved securely. The key is not shown again.");
    expect(input).toHaveValue("");
    expect(screen.queryByText("private-test-key")).not.toBeInTheDocument();
    const saveCall = calls.find((call) => call.method === "PUT" && call.path.endsWith("tavily-credential"));
    expect(JSON.parse(saveCall?.body ?? "{}")).toMatchObject({ expected_revision: 0, api_key: "private-test-key" });
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/used one Basic Search request/)).toBeInTheDocument();
    await waitFor(() => expect(calls.some((call) => call.path.endsWith("tavily-connection-test"))).toBe(true));
  });

  it("persists the explicit Disabled choice with optimistic revision", async () => {
    const calls = renderDiscovery();
    await screen.findByRole("button", { name: "Save provider" });
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "disabled" } });
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }));
    expect(await screen.findByText("Job Discovery provider settings saved.")).toBeInTheDocument();
    const saved = calls.find((call) => call.method === "PUT" && call.path.endsWith("/settings"));
    expect(JSON.parse(saved?.body ?? "{}")).toEqual({ expected_revision: 0, provider_override: "disabled" });
    expect(screen.getAllByText("Disabled")[0].closest("p")).toHaveTextContent("Effective provider: Disabled (your choice).");
  });

  it("explains that the connection check failed", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (url.pathname === "/api/v1/users/me") return Promise.resolve(json({ id: "owner", email: "owner@example.test", created_at: "now" }));
      if (url.pathname === "/api/v1/job-discovery/settings") return Promise.resolve(json(configured({ tavily_credential_configured: true, tavily_credential_source: "deployment" })));
      if (url.pathname === "/api/v1/job-discovery/tavily-connection-test") return Promise.resolve(json({ detail: "Tavily rejected the configured key." }, 502));
      throw new Error(`Unexpected request: ${init?.method} ${url.pathname}`);
    }));
    sessionStorage.setItem(TOKEN, "session-token");
    render(<MemoryRouter initialEntries={["/settings/discovery"]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
    await screen.findByRole("heading", { name: "Job Discovery" });
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Tavily rejected the configured key.");
  });
});

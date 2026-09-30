import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "./auth";
import { App } from "./App";
import type { JobDiscoverySettings } from "./api";
import type { LocalCodexStatus } from "./api";

const TOKEN = "career-trans.access-token";
const configured = (overrides: Partial<JobDiscoverySettings> = {}): JobDiscoverySettings => ({
  revision: 0, provider_override: null, deployment_provider: "brave", effective_provider: "brave",
  tavily_credential_configured: false, tavily_credential_source: null,
  tavily_user_credential_storage_available: true, tavily_credential_usable: false, ...overrides,
});
const localStatus: LocalCodexStatus = {
  enabled_by_deployment: true, cli_installed: true, version: "0.155.0", authentication_status: "signed_out",
  structured_invocation_status: "available", search_capability_status: "available",
  manual_discovery_status: "not_ready", scheduled_discovery_status: "unverified",
  message: "Codex authentication is required on the backend host.", setup_guidance: "Complete codex login on the backend host.",
};
let receivedJsonBodies: unknown[] = [];
const json = (body: unknown, status = 200) => {
  receivedJsonBodies.push(body);
  return new Response(JSON.stringify(body), { status });
};
const storageContents = (storage: Storage) => Array.from({ length: storage.length }, (_, index) => {
  const key = storage.key(index);
  return key === null ? "" : `${key}=${storage.getItem(key) ?? ""}`;
}).join(" ");

type HarnessOptions = {
  initialSettings?: JobDiscoverySettings;
  deploymentTavilyConfigured?: boolean;
  authoritativeAfterConflict?: JobDiscoverySettings;
  conflictOn?: "provider" | "credential";
};

function renderDiscovery(options: HarnessOptions = {}) {
  const calls: Array<{ method: string; path: string; body?: string }> = [];
  let serverSettings = options.initialSettings ?? configured();
  let conflictUsed = false;
  sessionStorage.setItem(TOKEN, "session-token");
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const method = init?.method ?? "GET";
    calls.push({ method, path: url.pathname, body: typeof init?.body === "string" ? init.body : undefined });
    if (url.pathname === "/api/v1/users/me") {
      return Promise.resolve(json({ id: "owner", email: "owner@example.test", created_at: "2026-01-01T00:00:00Z" }));
    }
    if (url.pathname === "/api/v1/job-discovery/settings" && method === "GET") {
      return Promise.resolve(json(serverSettings));
    }
    if (url.pathname === "/api/v1/job-discovery/local-codex/status") return Promise.resolve(json(localStatus));
    if (url.pathname === "/api/v1/job-discovery/local-codex/test") return Promise.resolve(json({ success: true, result_count: 1, message: "Local Codex search succeeded. This test used one live web-search request and may consume Codex usage." }));
    if (url.pathname === "/api/v1/job-discovery/settings" && method === "PUT") {
      if (options.conflictOn === "provider" && !conflictUsed) {
        conflictUsed = true;
        serverSettings = options.authoritativeAfterConflict ?? configured({ revision: 3, deployment_provider: "tavily", effective_provider: "tavily" });
        return Promise.resolve(json({ detail: "Job Discovery settings changed. Reload the current settings and try again." }, 409));
      }
      const payload = JSON.parse(String(init?.body ?? "{}")) as { provider_override: JobDiscoverySettings["provider_override"] };
      const effective = payload.provider_override ?? serverSettings.deployment_provider;
      serverSettings = { ...serverSettings, revision: serverSettings.revision + 1, provider_override: payload.provider_override, effective_provider: effective };
      return Promise.resolve(json(serverSettings));
    }
    if (url.pathname === "/api/v1/job-discovery/tavily-credential" && method === "PUT") {
      if (options.conflictOn === "credential" && !conflictUsed) {
        conflictUsed = true;
        serverSettings = options.authoritativeAfterConflict ?? configured({ revision: 2, deployment_provider: "tavily", effective_provider: "tavily", tavily_credential_configured: true, tavily_credential_source: "deployment", tavily_credential_usable: true });
        return Promise.resolve(json({ detail: "Job Discovery settings changed. Reload the current settings and try again." }, 409));
      }
      serverSettings = { ...serverSettings, revision: serverSettings.revision + 1, tavily_credential_configured: true, tavily_credential_source: "user", tavily_credential_usable: true };
      return Promise.resolve(json(serverSettings));
    }
    if (url.pathname === "/api/v1/job-discovery/tavily-credential" && method === "DELETE") {
      const hasDeploymentKey = options.deploymentTavilyConfigured ?? false;
      serverSettings = {
        ...serverSettings,
        revision: serverSettings.revision + 1,
        tavily_credential_configured: hasDeploymentKey,
        tavily_credential_source: hasDeploymentKey ? "deployment" : null,
        tavily_credential_usable: hasDeploymentKey,
      };
      return Promise.resolve(json(serverSettings));
    }
    if (url.pathname === "/api/v1/job-discovery/tavily-connection-test") {
      return Promise.resolve(json({ success: true, credential_source: "user", message: "Tavily connection test succeeded. This test used one Basic Search request." }));
    }
    throw new Error(`Unexpected request: ${method} ${url.pathname}`);
  }));
  render(<MemoryRouter initialEntries={["/settings/discovery"]}><AuthProvider><App /></AuthProvider></MemoryRouter>);
  return calls;
}

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); receivedJsonBodies = []; vi.restoreAllMocks(); });
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

  it("shows Local Codex backend-host readiness and runs the explicit live test", async () => {
    const calls = renderDiscovery();
    fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "local_codex" } });
    expect(screen.getByRole("option", { name: "Local Codex" })).toBeInTheDocument();
    expect(screen.getByText(/Runs on the machine hosting the Career-trans backend/)).toBeInTheDocument();
    expect(screen.getByText(/lets eligible Career-trans users consume the Codex session available to the backend OS account/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Codex.*(token|credential)/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByText("Sign-in required")).toBeInTheDocument();
    expect(screen.getByText("Unverified")).toBeInTheDocument();
    expect(screen.getByText(/Complete codex login on the backend host/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test Local Codex" }));
    expect(await screen.findByText(/may consume Codex usage/)).toBeInTheDocument();
    expect(calls.some((call) => call.path.endsWith("/local-codex/status"))).toBe(true);
    expect(calls.some((call) => call.path.endsWith("/local-codex/test"))).toBe(true);
  });

  it("can clear an explicit choice and return to the inherited Tavily deployment provider", async () => {
    const calls = renderDiscovery({
      initialSettings: configured({ provider_override: "disabled", deployment_provider: "tavily", effective_provider: "disabled", revision: 1 }),
    });
    await screen.findByRole("button", { name: "Save provider" });
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "inherit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }));
    expect(await screen.findByText("Job Discovery provider settings saved.")).toBeInTheDocument();
    const saved = calls.find((call) => call.method === "PUT" && call.path.endsWith("/settings"));
    expect(JSON.parse(saved?.body ?? "{}")).toEqual({ expected_revision: 1, provider_override: null });
    expect(screen.getAllByText("Tavily")[0].closest("p")).toHaveTextContent("Effective provider: Tavily (deployment default).");
  });

  it("persists Local Codex as the explicit provider identifier", async () => {
    const calls = renderDiscovery();
    await screen.findByRole("button", { name: "Save provider" });
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "local_codex" } });
    fireEvent.click(screen.getByRole("button", { name: "Save provider" }));
    expect(await screen.findByText("Job Discovery provider settings saved.")).toBeInTheDocument();
    const saved = calls.find((call) => call.method === "PUT" && call.path.endsWith("/settings"));
    expect(JSON.parse(saved?.body ?? "{}")).toEqual({ expected_revision: 0, provider_override: "local_codex" });
  });

  it("replaces a user key, clears the plaintext input, and keeps settings responses redacted", async () => {
    const calls = renderDiscovery({
      initialSettings: configured({ tavily_credential_configured: true, tavily_credential_source: "user", tavily_credential_usable: true, revision: 2 }),
    });
    const input = await screen.findByLabelText("Replace saved Tavily key");
    expect(input).toHaveAttribute("type", "password");
    expect(screen.queryByText("old-personal-key")).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "replacement-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));
    await screen.findByText("Tavily key saved securely. The key is not shown again.");
    expect(input).toHaveValue("");
    expect(screen.queryByText("replacement-secret")).not.toBeInTheDocument();
    expect(calls.filter((call) => call.method === "GET" && call.path.endsWith("/settings"))).toHaveLength(1);
    const save = calls.find((call) => call.method === "PUT" && call.path.endsWith("tavily-credential"));
    expect(JSON.parse(save?.body ?? "{}")).toMatchObject({ expected_revision: 2, api_key: "replacement-secret" });
    expect(screen.getByText("A key is configured (your saved key).")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled();
    expect(JSON.stringify(receivedJsonBodies)).not.toContain("replacement-secret");
  });

  it("warns when a saved user credential is unusable while allowing replacement or removal", async () => {
    renderDiscovery({
      initialSettings: configured({
        provider_override: "tavily", effective_provider: "tavily",
        tavily_credential_configured: true, tavily_credential_source: "user", tavily_credential_usable: false,
      }),
    });
    const warnings = await screen.findAllByRole("alert");
    expect(warnings[0]).toHaveTextContent("Your saved Tavily key cannot be read with the current credential-encryption configuration. Replace or remove the saved key.");
    expect(screen.getByText("A saved Tavily key exists but cannot currently be used.")).toBeInTheDocument();
    expect(warnings.some((warning) => warning.textContent?.includes("Job Discovery cannot use Tavily until a usable credential is available."))).toBe(true);
    expect(screen.queryByText("A key is configured (your saved key).")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
    expect(screen.getByLabelText("Replace saved Tavily key")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Remove saved key" })).toBeEnabled();
  });

  it("warns when Tavily is selected without a usable credential and leaves key entry available", async () => {
    renderDiscovery({
      initialSettings: configured({ provider_override: "tavily", effective_provider: "tavily" }),
    });
    expect(await screen.findByText("Job Discovery cannot use Tavily until a usable credential is available.")).toBeInTheDocument();
    expect(screen.getByText("No usable Tavily key is configured.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
    expect(screen.getByLabelText("Tavily API key")).toBeEnabled();
  });

  it("keeps a usable deployment key configured and testable", async () => {
    renderDiscovery({
      initialSettings: configured({
        deployment_provider: "tavily", effective_provider: "tavily",
        tavily_credential_configured: true, tavily_credential_source: "deployment", tavily_credential_usable: true,
      }),
    });
    expect(await screen.findByText("A key is configured (deployment key).")).toBeInTheDocument();
    expect(screen.queryByText("Job Discovery cannot use Tavily until a usable credential is available.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled();
  });

  it.each([true, false])("removes a personal key and reflects deployment fallback (deployment key=%s)", async (deploymentKey) => {
    renderDiscovery({
      initialSettings: configured({ tavily_credential_configured: true, tavily_credential_source: "user", tavily_credential_usable: true, revision: 4 }),
      deploymentTavilyConfigured: deploymentKey,
    });
    await screen.findByRole("button", { name: "Remove saved key" });
    fireEvent.click(screen.getByRole("button", { name: "Remove saved key" }));
    if (deploymentKey) {
      expect(await screen.findByText("A key is configured (deployment key).")).toBeInTheDocument();
    } else {
      expect(await screen.findByText("No usable Tavily key is configured.")).toBeInTheDocument();
    }
  });

  it("disables personal key entry when encryption storage is unavailable but shows deployment Tavily", async () => {
    renderDiscovery({
      initialSettings: configured({ deployment_provider: "tavily", effective_provider: "tavily", tavily_credential_configured: true, tavily_credential_source: "deployment", tavily_credential_usable: true, tavily_user_credential_storage_available: false }),
    });
    const input = await screen.findByLabelText("Tavily API key");
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save key" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent("administrator configures the credential-encryption key");
    expect(screen.getByText("A key is configured (deployment key).")).toBeInTheDocument();
  });

  it("reloads authoritative settings after a 409 and clears the submitted secret", async () => {
    const calls = renderDiscovery({
      conflictOn: "credential",
      authoritativeAfterConflict: configured({ revision: 3, deployment_provider: "tavily", effective_provider: "tavily", tavily_credential_configured: true, tavily_credential_source: "deployment", tavily_credential_usable: true }),
    });
    const input = await screen.findByLabelText("Tavily API key");
    fireEvent.change(input, { target: { value: "do-not-restore-after-conflict" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Settings changed elsewhere. Current settings were reloaded");
    expect(input).toHaveValue("");
    expect(screen.getAllByText("Tavily")[0].closest("p")).toHaveTextContent("Effective provider: Tavily (deployment default).");
    expect(calls.filter((call) => call.method === "GET" && call.path.endsWith("/settings"))).toHaveLength(2);
    expect(sessionStorage.getItem("tavily-api-key")).toBeNull();
    expect(storageContents(localStorage)).not.toContain("do-not-restore-after-conflict");
    expect(storageContents(sessionStorage)).not.toContain("do-not-restore-after-conflict");
  });

  it("does not write Tavily key text to browser storage or expose it after saving", async () => {
    const calls = renderDiscovery();
    const input = await screen.findByLabelText("Tavily API key");
    fireEvent.change(input, { target: { value: "private-test-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));
    await screen.findByText("Tavily key saved securely. The key is not shown again.");
    expect(input).toHaveValue("");
    expect(screen.queryByText("private-test-key")).not.toBeInTheDocument();
    expect(localStorage.getItem("private-test-key")).toBeNull();
    expect(storageContents(localStorage)).not.toContain("private-test-key");
    expect(storageContents(sessionStorage)).not.toContain("private-test-key");
    expect(calls.filter((call) => call.method === "GET" && call.path.endsWith("/settings"))).toHaveLength(1);
    expect(screen.getByText("A key is configured (your saved key).")).toBeInTheDocument();
    expect(JSON.stringify(receivedJsonBodies)).not.toContain("private-test-key");
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
      if (url.pathname === "/api/v1/job-discovery/settings") return Promise.resolve(json(configured({ tavily_credential_configured: true, tavily_credential_source: "deployment", tavily_credential_usable: true })));
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

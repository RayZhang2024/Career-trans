import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Link, MemoryRouter } from "react-router-dom";
import type { AiModelCatalog, AiPreferences, AiSettings, ReasoningEffort, SemanticOperation, User } from "./api";
import { SEMANTIC_OPERATION_ORDER } from "./api";
import { App } from "./App";
import { AuthProvider, useAuth } from "./auth";
import { canonicalizeAiPreferences, emptyAiPreferences, effortOptionsFor, preferencesEqual, validateDraftMatrix } from "./aiSettings";

const TOKEN = "career-trans.access-token";
const owner: User = { id: "owner-a", email: "owner-a@example.test", created_at: "2026-01-01T00:00:00Z" };
const operations = SEMANTIC_OPERATION_ORDER.map(({ id }) => id);
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
type Handler = (url: URL, init?: RequestInit) => Response | Promise<Response>;
type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (reason?: unknown) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const catalog = (provider = "openai", supported = true): AiModelCatalog => ({
  provider,
  user_overrides_supported: supported,
  models: [
    { id: "model-a", label: "Backend Label A", structured_output: true, reasoning_efforts: ["none", "low", "medium", "high", "xhigh", "max"] },
    { id: "model-b", label: "Backend Label B", structured_output: true, reasoning_efforts: ["none", "low"] },
  ],
});

const effective = (model = "model-a", effort: ReasoningEffort | null = null) => Object.fromEntries(
  operations.map((operation) => [operation, { model, reasoning_effort: effort, inherited_model: true, inherited_reasoning_effort: true }]),
) as AiSettings["effective"];

const settings = (overrides: Partial<AiSettings> = {}): AiSettings => ({
  revision: 0,
  provider: "openai",
  user_overrides_supported: true,
  persisted_override_provider: null,
  overrides_active: false,
  preference_activity: "inherited",
  preferences: emptyAiPreferences(),
  effective: effective(),
  ...overrides,
});

function fetcher(
  handlers: Record<string, Handler> = {},
  readCatalog: AiModelCatalog = catalog(),
  readSettings: AiSettings = settings(),
) {
  const calls: Array<{ method: string; path: string; body?: string; token: string | null }> = [];
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const method = init?.method ?? "GET";
    const token = new Headers(init?.headers).get("Authorization");
    calls.push({ method, path: url.pathname, body: typeof init?.body === "string" ? init.body : undefined, token });
    const handler = handlers[`${method} ${url.pathname}`] ?? handlers[url.pathname];
    if (handler) return Promise.resolve(handler(url, init));
    if (url.pathname === "/api/v1/auth/login") return Promise.resolve(json({ access_token: "token-b" }));
    if (url.pathname === "/api/v1/users/me") {
      const user = token === "Bearer token-b" ? { ...owner, id: "owner-b", email: "owner-b@example.test" } : owner;
      return Promise.resolve(json(user));
    }
    if (url.pathname === "/api/v1/ai/models") return Promise.resolve(json(readCatalog));
    if (url.pathname === "/api/v1/ai/settings" && method === "GET") return Promise.resolve(json(readSettings));
    throw new Error(`Unexpected request: ${method} ${url.pathname}`);
  });
  return { mock, calls };
}

function renderSettings(fetch: ReturnType<typeof fetcher>["mock"], path = "/settings/ai", extra?: React.ReactNode) {
  sessionStorage.setItem(TOKEN, "token-a");
  vi.stubGlobal("fetch", fetch);
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider>{extra}<App /></AuthProvider></MemoryRouter>);
}

function SwitchAccount() {
  const { login } = useAuth();
  return <button type="button" onClick={() => void login("owner-b@example.test", "not-a-real-password")}>Switch account</button>;
}

function openAdvanced() {
  const details = screen.getByText("Advanced per-operation overrides").closest("details");
  if (!details?.open) fireEvent.click(screen.getByText("Advanced per-operation overrides"));
  return details;
}

async function waitForSettings() {
  await screen.findByRole("heading", { name: "AI Models" });
  await waitFor(() => expect(screen.getByLabelText("Default model")).toBeInTheDocument());
}

async function waitForReadOnlySettings() {
  await screen.findByRole("heading", { name: "Saved preferences are read-only" });
}

function operationControl(operationLabel: string, controlLabel: string) {
  const card = [...document.querySelectorAll<HTMLElement>(".ai-operation-card")]
    .find((candidate) => candidate.querySelector("h3")?.textContent === operationLabel);
  if (!card) throw new Error(`${operationLabel} operation card is not rendered`);
  return within(card).getByLabelText(controlLabel);
}

beforeEach(() => { sessionStorage.clear(); vi.restoreAllMocks(); });
afterEach(cleanup);

describe("Issue #189 Settings → AI Models", () => {
  it("navigates through the authenticated Settings link to /settings/ai", async () => {
    const api = fetcher();
    renderSettings(api.mock, "/settings");
    await waitForSettings();
    expect(screen.getAllByRole("link", { name: "Settings" })[0]).toHaveAttribute("href", "/settings/ai");
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeInTheDocument();
  });

  it("loads the paired catalog/settings and renders inherited provider-owned defaults", async () => {
    const api = fetcher();
    renderSettings(api.mock);
    await waitForSettings();
    expect(api.calls.filter((item) => item.path === "/api/v1/ai/models")).toHaveLength(1);
    expect(api.calls.filter((item) => item.path === "/api/v1/ai/settings" && item.method === "GET")).toHaveLength(1);
    expect(screen.getByText(/Using deployment defaults/)).toBeInTheDocument();
    expect(screen.getByLabelText("Default model")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Reset to deployment defaults" })).not.toBeInTheDocument();
  });

  it("uses backend model labels, explains product boundaries, and avoids deployment-config/provider calls", async () => {
    const api = fetcher();
    renderSettings(api.mock);
    await waitForSettings();
    expect(screen.getAllByRole("option", { name: "Backend Label A" }).length).toBeGreaterThan(0);
    expect(screen.getByText(/semantic provider is controlled by your deployment/i)).toBeInTheDocument();
    expect(screen.getByText(/credentials are managed by the server and are not shown/i)).toBeInTheDocument();
    expect(screen.getByText(/future semantic work only/i)).toBeInTheDocument();
    expect(screen.getByText(/historical outputs are not rewritten/i)).toBeInTheDocument();
    expect(screen.getByText(/Host-side Codex external discovery is separate/i)).toBeInTheDocument();
    expect(api.calls.some((call) => call.path.includes("/config/llm") || call.path.includes("/models/"))).toBe(false);
    expect(api.calls.some((call) => /api.?key|credential/i.test(call.path))).toBe(false);
    expect(document.body.textContent?.toLowerCase()).not.toContain("openai_api_key");
    expect(document.body.textContent?.toLowerCase()).not.toContain("api key status");
  });

  it("saves one full replacement with the current revision and adopts the returned settings as clean authority", async () => {
    const persisted = settings({ revision: 7, preference_activity: "active", overrides_active: true });
    const api = fetcher({
      "PUT /api/v1/ai/settings": (_url, init) => {
        const payload = JSON.parse(String(init?.body)) as AiSettings["preferences"] & { expected_revision: number };
        return json(settings({ ...persisted, revision: 8, preferences: payload, effective: effective("model-b", "low") }));
      },
    }, catalog(), persisted);
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await screen.findByText("AI settings saved for future semantic work.");
    const writes = api.calls.filter((call) => call.method === "PUT");
    expect(writes).toHaveLength(1);
    expect(JSON.parse(writes[0].body ?? "{}")).toEqual({ expected_revision: 7, default_model: "model-b", default_reasoning_effort: null, operation_overrides: {} });
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    expect(document.body).toHaveTextContent(/Currently effective: Backend Label B/);
  });

  it("treats canonical empty operation entries and map order as clean, while explicit none remains dirty", () => {
    const empty = emptyAiPreferences();
    expect(preferencesEqual(empty, { ...empty, operation_overrides: { job_relevance: { model: null, reasoning_effort: null } } })).toBe(true);
    const left: AiPreferences = { ...empty, operation_overrides: { job_relevance: { model: "model-a" }, job_extraction: { reasoning_effort: "none" } } };
    const right: AiPreferences = { ...empty, operation_overrides: { job_extraction: { reasoning_effort: "none" }, job_relevance: { model: "model-a" } } };
    expect(preferencesEqual(left, right)).toBe(true);
    expect(preferencesEqual(empty, { ...empty, default_reasoning_effort: "none" })).toBe(false);
  });

  it("offers inheritance separately from explicit No reasoning and sends none as an enum value", async () => {
    const api = fetcher({ "PUT /api/v1/ai/settings": (_url, init) => {
      const payload = JSON.parse(String(init?.body)) as AiSettings["preferences"] & { expected_revision: number };
      return json(settings({ revision: 1, preference_activity: "active", overrides_active: true, preferences: payload, effective: effective("model-a", "none") }));
    } });
    renderSettings(api.mock);
    await waitForSettings();
    const effort = screen.getByLabelText("Default reasoning effort");
    expect(within(effort).getByRole("option", { name: "Use inherited/default reasoning" })).toHaveValue("");
    expect(within(effort).getByRole("option", { name: "No reasoning" })).toHaveValue("none");
    fireEvent.change(effort, { target: { value: "none" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await screen.findByText("AI settings saved for future semantic work.");
    expect(JSON.parse(api.calls.find((call) => call.method === "PUT")?.body ?? "{}")).toMatchObject({ default_reasoning_effort: "none" });
  });

  it("renders the canonical nine operations as responsive cards rather than a wide table", async () => {
    renderSettings(fetcher().mock);
    await waitForSettings();
    openAdvanced();
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(9);
    for (const { label } of SEMANTIC_OPERATION_ORDER) expect(screen.getByRole("heading", { name: label })).toBeInTheDocument();
    expect(document.querySelector("table")).toBeNull();
    expect(document.querySelectorAll(".ai-operation-card")).toHaveLength(9);
  });

  it("sends a model-only operation override and omits operations that inherit both fields", async () => {
    const api = fetcher({ "PUT /api/v1/ai/settings": (_url, init) => {
      const payload = JSON.parse(String(init?.body)) as AiSettings["preferences"] & { expected_revision: number };
      return json(settings({ revision: 1, preference_activity: "active", overrides_active: true, preferences: payload }));
    } });
    renderSettings(api.mock);
    await waitForSettings();
    openAdvanced();
    fireEvent.change(operationControl("CV extraction", "Model override"), { target: { value: "model-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await screen.findByText("AI settings saved for future semantic work.");
    expect(JSON.parse(api.calls.find((call) => call.method === "PUT")?.body ?? "{}").operation_overrides).toEqual({ cv_semantic_extraction: { model: "model-b" } });
  });

  it("sends an effort-only operation override without adding an empty model field", async () => {
    const api = fetcher({ "PUT /api/v1/ai/settings": (_url, init) => {
      const payload = JSON.parse(String(init?.body)) as AiSettings["preferences"] & { expected_revision: number };
      return json(settings({ revision: 1, preference_activity: "active", overrides_active: true, preferences: payload }));
    } });
    renderSettings(api.mock);
    await waitForSettings();
    openAdvanced();
    fireEvent.change(operationControl("CV extraction", "Reasoning override"), { target: { value: "low" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await screen.findByText("AI settings saved for future semantic work.");
    expect(JSON.parse(api.calls.find((call) => call.method === "PUT")?.body ?? "{}").operation_overrides).toEqual({ cv_semantic_extraction: { reasoning_effort: "low" } });
  });

  it("removes a fully inherited operation from the full-replacement document", async () => {
    const saved: AiPreferences = { ...emptyAiPreferences(), operation_overrides: { job_relevance: { model: "model-a" } } };
    const api = fetcher({ "PUT /api/v1/ai/settings": (_url, init) => {
      const payload = JSON.parse(String(init?.body)) as AiSettings["preferences"] & { expected_revision: number };
      return json(settings({ revision: 2, preference_activity: "active", overrides_active: true, preferences: payload }));
    } }, catalog(), settings({ revision: 1, preference_activity: "active", overrides_active: true, preferences: saved }));
    renderSettings(api.mock);
    await waitForSettings();
    openAdvanced();
    fireEvent.change(operationControl("Job relevance", "Model override"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await screen.findByText("AI settings saved for future semantic work.");
    expect(JSON.parse(api.calls.find((call) => call.method === "PUT")?.body ?? "{}").operation_overrides).toEqual({});
  });

  it("derives operation effort options from an explicit operation model", async () => {
    renderSettings(fetcher().mock);
    await waitForSettings();
    openAdvanced();
    fireEvent.change(operationControl("CV extraction", "Model override"), { target: { value: "model-b" } });
    const effort = operationControl("CV extraction", "Reasoning override");
    expect(within(effort).getByRole("option", { name: "Low" })).toBeInTheDocument();
    expect(within(effort).queryByRole("option", { name: "High" })).not.toBeInTheDocument();
    expect(within(effort).queryByRole("option", { name: "Maximum" })).not.toBeInTheDocument();
  });

  it("uses explicit default-model capabilities for inherited operation model and uses catalog union for unknown deployment model", async () => {
    const api = fetcher();
    renderSettings(api.mock);
    await waitForSettings();
    expect(effortOptionsFor(catalog(), emptyAiPreferences(), "job_relevance")).toEqual(["none", "low", "medium", "high", "xhigh", "max"]);
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    openAdvanced();
    const effort = operationControl("CV extraction", "Reasoning override");
    expect(within(effort).getByRole("option", { name: "Low" })).toBeInTheDocument();
    expect(within(effort).queryByRole("option", { name: "High" })).not.toBeInTheDocument();
    expect(within(effort).getByRole("option", { name: "Use inherited/default reasoning" })).toBeInTheDocument();
  });

  it("preserves a selected incompatible effort when changing model and blocks ordinary Save", async () => {
    renderSettings(fetcher().mock);
    await waitForSettings();
    openAdvanced();
    const model = operationControl("CV extraction", "Model override");
    const effort = operationControl("CV extraction", "Reasoning override");
    fireEvent.change(effort, { target: { value: "high" } });
    fireEvent.change(model, { target: { value: "model-b" } });
    expect(effort).toHaveValue("high");
    expect(within(effort).getByRole("option", { name: "High — not supported by selected model" })).toBeDisabled();
    expect(document.body).toHaveTextContent("CV extraction has a saved or selected model/reasoning combination");
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    fireEvent.change(effort, { target: { value: "low" } });
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeEnabled();
  });

  it("validates the complete matrix when inherited default effort conflicts with an operation model", async () => {
    renderSettings(fetcher().mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default reasoning effort"), { target: { value: "high" } });
    openAdvanced();
    fireEvent.change(operationControl("CV extraction", "Model override"), { target: { value: "model-b" } });
    expect(document.body).toHaveTextContent("CV extraction");
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
  });

  it("validates an explicit operation effort against an inherited explicit default model", () => {
    const prefs: AiPreferences = { ...emptyAiPreferences(), default_model: "model-b", operation_overrides: { job_relevance: { reasoning_effort: "max" } } };
    expect(validateDraftMatrix(catalog(), prefs).invalidOperations).toContain("job_relevance");
  });

  it("allows locally unknown deployment compatibility to be checked by V1A", () => {
    const prefs: AiPreferences = { ...emptyAiPreferences(), default_reasoning_effort: "max" };
    expect(validateDraftMatrix(catalog(), prefs).invalidOperations).toEqual([]);
  });

  it("shows a typed stale model without promoting it and blocks ordinary Save until explicit correction or reset", async () => {
    const prefs: AiPreferences = { ...emptyAiPreferences(), default_model: "retired-model" };
    renderSettings(fetcher({}, catalog(), settings({ revision: 5, preference_activity: "inactive_invalid", preferences: prefs, overrides_active: false })) .mock);
    await waitForSettings();
    const model = screen.getByLabelText("Default model");
    expect(within(model).getByRole("option", { name: "Unsupported saved model: retired-model" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset to deployment defaults" })).toBeEnabled();
    expect(screen.getByText(/Unsupported values remain visible/)).toBeInTheDocument();
    expect(screen.getByLabelText("Default model")).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "" } });
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeEnabled();
  });

  it("preserves an unsupported per-operation model and blocks Save until that override is explicitly corrected", async () => {
    const preferences: AiPreferences = { ...emptyAiPreferences(), operation_overrides: { job_relevance: { model: "retired-model" } } };
    renderSettings(fetcher({}, catalog(), settings({ revision: 5, preference_activity: "inactive_invalid", preferences, overrides_active: false })).mock);
    await waitForSettings();
    openAdvanced();
    const model = operationControl("Job relevance", "Model override");
    expect(within(model).getByRole("option", { name: "Unsupported saved model: retired-model" })).toBeDisabled();
    expect(model).toHaveValue("retired-model");
    expect(model).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    fireEvent.change(model, { target: { value: "" } });
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeEnabled();
  });

  it("preserves a locally incompatible persisted default effort until explicitly corrected", async () => {
    const preferences: AiPreferences = { ...emptyAiPreferences(), default_model: "model-b", default_reasoning_effort: "high" };
    renderSettings(fetcher({}, catalog(), settings({ revision: 5, preference_activity: "inactive_invalid", preferences, overrides_active: false })).mock);
    await waitForSettings();
    const effort = screen.getByLabelText("Default reasoning effort");
    expect(effort).toHaveValue("high");
    expect(within(effort).getByRole("option", { name: "High — not supported by selected model" })).toBeDisabled();
    expect(effort).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    fireEvent.change(effort, { target: { value: "low" } });
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeEnabled();
  });

  it("handles structurally invalid empty settings without fabricating values and allows an explicit reset", async () => {
    const api = fetcher({ "PUT /api/v1/ai/settings": (_url, init) => {
      const payload = JSON.parse(String(init?.body)) as AiSettings["preferences"] & { expected_revision: number };
      return json(settings({ revision: 5, preference_activity: "inherited", preferences: payload }));
    } }, catalog(), settings({ revision: 4, preference_activity: "inactive_invalid", preferences: emptyAiPreferences() }));
    renderSettings(api.mock);
    await waitForSettings();
    expect(screen.getByText(/Saved AI settings exist but cannot be represented safely/)).toBeInTheDocument();
    expect(document.body).toHaveTextContent(/Currently effective: Backend Label A/);
    fireEvent.click(screen.getByRole("button", { name: "Reset to deployment defaults" }));
    await screen.findByText("AI settings were reset to deployment defaults.");
    expect(JSON.parse(api.calls.find((call) => call.method === "PUT")?.body ?? "{}")).toEqual({ expected_revision: 4, default_model: null, default_reasoning_effort: null, operation_overrides: {} });
  });

  it.each([
    ["inactive_provider_mismatch", "openai"],
    ["unsupported", null],
  ] as const)("renders %s preferences read-only without reset or mutation", async (activity, retainedProvider) => {
    const state = settings({ provider: "ollama", user_overrides_supported: false, persisted_override_provider: retainedProvider, preference_activity: activity, preferences: { ...emptyAiPreferences(), default_model: "saved-model" } });
    const api = fetcher({}, catalog("ollama", false), state);
    renderSettings(api.mock);
    if (activity === "inactive_provider_mismatch" || activity === "unsupported") await waitForReadOnlySettings();
    else await waitForSettings();
    expect(screen.getByRole("heading", { name: "Saved preferences are read-only" })).toBeInTheDocument();
    expect(screen.getByText(/saved-model/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset to deployment defaults" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Default model")).not.toBeInTheDocument();
    expect(api.calls.some((call) => call.method === "PUT")).toBe(false);
  });

  it("does not offer a redundant reset for revision-zero or valid inherited persisted preferences", async () => {
    for (const revision of [0, 9]) {
      cleanup(); sessionStorage.clear();
      renderSettings(fetcher({}, catalog(), settings({ revision })).mock);
      await waitForSettings();
      expect(screen.queryByRole("button", { name: "Reset to deployment defaults" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    }
  });

  it("reconciles a 409 with both GETs and never repeats PUT or reapplies the attempted draft", async () => {
    let settingsReads = 0;
    let catalogReads = 0;
    const api = fetcher({
      "GET /api/v1/ai/models": () => { catalogReads += 1; return json(catalog()); },
      "GET /api/v1/ai/settings": () => { settingsReads += 1; return json(settings({ revision: settingsReads === 1 ? 2 : 4 })); },
      "PUT /api/v1/ai/settings": () => json({ detail: "private backend text" }, 409),
    });
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await screen.findByText(/settings changed elsewhere/i);
    expect(catalogReads).toBe(2);
    expect(settingsReads).toBe(2);
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(screen.getByLabelText("Default model")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
  });

  it("removes stale writable authority after a 409 reconciliation fails, then restores only a fresh baseline", async () => {
    let catalogReads = 0;
    let settingsReads = 0;
    const persisted = { ...emptyAiPreferences(), default_model: "model-a" };
    const api = fetcher({
      "GET /api/v1/ai/models": () => ++catalogReads === 2 ? Promise.reject(new TypeError("offline")) : json(catalog()),
      "GET /api/v1/ai/settings": () => ++settingsReads === 2 ? Promise.reject(new TypeError("offline")) : json(settings({ revision: settingsReads === 1 ? 2 : 4, preference_activity: settingsReads === 1 ? "active" : "inherited", preferences: settingsReads === 1 ? persisted : emptyAiPreferences() })),
      "PUT /api/v1/ai/settings": () => json({ detail: "private backend text" }, 409),
    });
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Current AI settings could not be reloaded");
    expect(screen.queryByRole("button", { name: "Save AI settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset to deployment defaults" })).not.toBeInTheDocument();
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Reload current AI settings" }));
    await waitForSettings();
    expect(catalogReads).toBe(3);
    expect(settingsReads).toBe(3);
    expect(screen.getByLabelText("Default model")).toHaveValue("");
    expect(screen.getByLabelText("Default model")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1);
  });

  it("reconciles an interrupted PUT without retry and uses neutral saved-state wording", async () => {
    let settingsReads = 0;
    const api = fetcher({
      "GET /api/v1/ai/settings": () => json(settings({ revision: ++settingsReads === 1 ? 0 : 1 })),
      "PUT /api/v1/ai/settings": () => Promise.reject(new TypeError("offline")),
    });
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await screen.findByText("The save request was interrupted. Current saved settings have been reloaded; no automatic retry was performed.");
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(api.calls.filter((call) => call.method === "GET" && call.path === "/api/v1/ai/models")).toHaveLength(2);
    expect(api.calls.filter((call) => call.method === "GET" && call.path === "/api/v1/ai/settings")).toHaveLength(2);
  });

  it("keeps an ambiguous PUT non-writable when reconciliation also fails and never retries the mutation", async () => {
    let catalogReads = 0;
    let settingsReads = 0;
    const persisted = { ...emptyAiPreferences(), default_model: "model-a" };
    const api = fetcher({
      "GET /api/v1/ai/models": () => ++catalogReads === 2 ? Promise.reject(new TypeError("offline")) : json(catalog()),
      "GET /api/v1/ai/settings": () => ++settingsReads === 2 ? Promise.reject(new TypeError("offline")) : json(settings({ revision: settingsReads === 1 ? 2 : 4, preference_activity: settingsReads === 1 ? "active" : "inherited", preferences: settingsReads === 1 ? persisted : emptyAiPreferences() })),
      "PUT /api/v1/ai/settings": () => Promise.reject(new TypeError("offline")),
    });
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Current AI settings could not be reloaded");
    expect(screen.queryByRole("button", { name: "Save AI settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset to deployment defaults" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reload current AI settings" }));
    await waitForSettings();
    expect(catalogReads).toBe(3);
    expect(settingsReads).toBe(3);
    expect(screen.getByLabelText("Default model")).toHaveValue("");
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1);
  });

  it("preserves a dirty draft after a bounded 422 message without exposing backend detail", async () => {
    const api = fetcher({ "PUT /api/v1/ai/settings": () => json({ detail: "private provider payload" }, 422) });
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("These AI settings are not valid for the current model/provider configuration");
    expect(screen.getByLabelText("Default model")).toHaveValue("model-b");
    expect(screen.getByRole("button", { name: "Save AI settings" })).toBeEnabled();
    expect(document.body.textContent).not.toContain("private provider payload");
    expect(api.calls.filter((call) => call.method === "GET")).toHaveLength(3);
  });

  it("prevents duplicate concurrent saves", async () => {
    const write = deferred<Response>();
    const api = fetcher({ "PUT /api/v1/ai/settings": () => write.promise });
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    const save = screen.getByRole("button", { name: "Save AI settings" });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Saving AI settings");
    write.resolve(json(settings({ revision: 1, preference_activity: "active", preferences: { ...emptyAiPreferences(), default_model: "model-b" } })));
    await screen.findByText("AI settings saved for future semantic work.");
  });

  it("reconciles incoherent post-PUT settings with a fresh paired read and does not replay PUT", async () => {
    let settingsReads = 0;
    let catalogReads = 0;
    const api = fetcher({
      "GET /api/v1/ai/models": () => { catalogReads += 1; return json(catalog(catalogReads === 1 ? "openai" : "ollama", catalogReads === 1)); },
      "GET /api/v1/ai/settings": () => { settingsReads += 1; return json(settings({ revision: settingsReads === 1 ? 0 : 2, provider: settingsReads === 1 ? "openai" : "ollama", user_overrides_supported: settingsReads === 1, preference_activity: settingsReads === 1 ? "inherited" : "unsupported" })); },
      "PUT /api/v1/ai/settings": () => json(settings({ revision: 1, provider: "ollama", user_overrides_supported: false, preference_activity: "unsupported" })),
    });
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await screen.findByRole("heading", { name: "Saved preferences are read-only" });
    expect(catalogReads).toBe(2);
    expect(settingsReads).toBe(2);
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1);
  });

  it("does not restore pre-PUT authority when the successful response is incoherent and both recovery reads fail", async () => {
    let catalogReads = 0;
    let settingsReads = 0;
    const api = fetcher({
      "GET /api/v1/ai/models": () => ++catalogReads === 1 ? json(catalog()) : Promise.reject(new TypeError("offline")),
      "GET /api/v1/ai/settings": () => ++settingsReads === 1 ? json(settings()) : Promise.reject(new TypeError("offline")),
      "PUT /api/v1/ai/settings": (_url, init) => {
        const payload = JSON.parse(String(init?.body)) as AiPreferences & { expected_revision: number };
        return json(settings({ revision: payload.expected_revision + 1, provider: "ollama", user_overrides_supported: false, preference_activity: "unsupported", preferences: payload }));
      },
    });
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Current AI settings could not be reloaded");
    expect(screen.queryByRole("button", { name: "Save AI settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset to deployment defaults" })).not.toBeInTheDocument();
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1);
    expect(catalogReads).toBe(2);
    expect(settingsReads).toBe(2);
  });

  it("fails closed on catalog/settings provider or support mismatches and retries only as an explicit coherent pair", async () => {
    let catalogReads = 0;
    let settingsReads = 0;
    const api = fetcher({
      "GET /api/v1/ai/models": () => json(++catalogReads === 1 ? catalog("ollama", false) : catalog()),
      "GET /api/v1/ai/settings": () => json(++settingsReads === 1 ? settings({ provider: "openai", user_overrides_supported: true }) : settings()),
    });
    renderSettings(api.mock);
    expect(await screen.findByRole("alert")).toHaveTextContent("AI configuration changed while this page was loading");
    expect(screen.queryByLabelText("Default model")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reload current AI settings" }));
    await waitForSettings();
    expect(catalogReads).toBe(2);
    expect(settingsReads).toBe(2);
    expect(screen.getByLabelText("Default model")).toBeInTheDocument();
  });

  it("keeps initial load errors non-writable and retries both resources only after an explicit action", async () => {
    let catalogReads = 0;
    let settingsReads = 0;
    const api = fetcher({
      "GET /api/v1/ai/models": () => { catalogReads += 1; return json(catalog()); },
      "GET /api/v1/ai/settings": () => ++settingsReads === 1 ? Promise.reject(new TypeError("offline")) : json(settings()),
    });
    renderSettings(api.mock);
    expect(await screen.findByRole("alert")).toHaveTextContent("AI settings are unavailable");
    expect(screen.queryByLabelText("Default model")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save AI settings" })).not.toBeInTheDocument();
    expect(catalogReads).toBe(1);
    expect(settingsReads).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Reload current AI settings" }));
    await waitForSettings();
    expect(catalogReads).toBe(2);
    expect(settingsReads).toBe(2);
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("discards a partial or incoherent reconciliation rather than restoring writable authority", async () => {
    const firstCatalog = json(catalog());
    const firstSettings = json(settings());
    const pendingCatalog = deferred<Response>();
    const api = fetcher({
      "GET /api/v1/ai/models": (() => { let count = 0; return () => ++count === 1 ? firstCatalog : pendingCatalog.promise; })(),
      "GET /api/v1/ai/settings": (() => { let count = 0; return () => ++count === 1 ? firstSettings : json(settings({ revision: 3 })); })(),
    });
    renderSettings(api.mock);
    await waitForSettings();
    fireEvent.click(screen.getByRole("button", { name: "Reload current settings" }));
    expect(await screen.findByText("Reloading the authoritative model catalog and saved settings…")).toBeInTheDocument();
    expect(screen.queryByLabelText("Default model")).not.toBeInTheDocument();
    pendingCatalog.resolve(json(catalog("ollama", false)));
    expect(await screen.findByRole("alert")).toHaveTextContent("AI configuration changed while this page was loading");
    expect(screen.queryByRole("button", { name: "Save AI settings" })).not.toBeInTheDocument();
  });

  it("keeps one explicit paired reload in flight and applies its delayed authority only when it settles", async () => {
    let catalogReads = 0;
    let settingsReads = 0;
    const oldCatalog = deferred<Response>(); const oldSettings = deferred<Response>();
    const api = fetcher({
      "GET /api/v1/ai/models": () => {
        catalogReads += 1;
        if (catalogReads === 1) return json(catalog());
        if (catalogReads === 2) return oldCatalog.promise;
        return json(catalog());
      },
      "GET /api/v1/ai/settings": () => {
        settingsReads += 1;
        if (settingsReads === 1) return json(settings());
        if (settingsReads === 2) return oldSettings.promise;
        return json(settings({ revision: 1, effective: effective("model-b", "low") }));
      },
    });
    renderSettings(api.mock);
    await waitForSettings();
    const firstReloadStart = api.calls.filter((call) => call.method === "GET" && call.path.includes("/ai/")).length;
    fireEvent.click(screen.getByRole("button", { name: "Reload current settings" }));
    expect(screen.queryByRole("button", { name: "Reload current settings" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Reloading the authoritative model catalog and saved settings"));
    oldCatalog.resolve(json(catalog()));
    oldSettings.resolve(json(settings({ revision: 1, effective: effective("model-b", "low") })));
    await waitFor(() => expect(document.body).toHaveTextContent(/Revision 1/));
    expect(document.body).toHaveTextContent(/Currently effective: Backend Label B/);
    expect(firstReloadStart).toBe(2);
  });

  it("never combines an old authenticated-session pair with the replacement account", async () => {
    const oldCatalog = deferred<Response>(); const oldSettings = deferred<Response>();
    let oldReadCount = 0;
    const api = fetcher({
      "GET /api/v1/ai/models": (_url, init) => {
        if (new Headers(init?.headers).get("Authorization") === "Bearer token-a" && ++oldReadCount <= 1) return oldCatalog.promise;
        return json(catalog());
      },
      "GET /api/v1/ai/settings": (_url, init) => new Headers(init?.headers).get("Authorization") === "Bearer token-a" ? oldSettings.promise : json(settings({ revision: 11, effective: effective("model-b", "low") })),
    });
    sessionStorage.setItem(TOKEN, "token-a"); vi.stubGlobal("fetch", api.mock);
    render(<MemoryRouter initialEntries={["/settings/ai"]}><AuthProvider><SwitchAccount /><App /></AuthProvider></MemoryRouter>);
    await screen.findByRole("button", { name: "Switch account" });
    fireEvent.click(screen.getByRole("button", { name: "Switch account" }));
    await waitFor(() => expect(document.body).toHaveTextContent(/Revision 11/));
    oldCatalog.resolve(json(catalog("old-provider", false)));
    oldSettings.resolve(json(settings({ provider: "old-provider", revision: 1 })));
    await waitFor(() => expect(screen.getByText(/Revision 11/)).toBeInTheDocument());
    expect(document.body).toHaveTextContent(/Currently effective: Backend Label B/);
    expect(api.calls.filter((call) => call.path === "/api/v1/ai/settings" && call.method === "PUT")).toHaveLength(0);
  });

  it("discards a delayed 409 reconciliation pair after a newer authenticated session establishes authority", async () => {
    const oldCatalog = deferred<Response>();
    const oldSettings = deferred<Response>();
    let catalogReadsA = 0;
    let settingsReadsA = 0;
    const api = fetcher({
      "GET /api/v1/ai/models": (_url, init) => {
        if (new Headers(init?.headers).get("Authorization") === "Bearer token-b") return json(catalog());
        return ++catalogReadsA === 1 ? json(catalog()) : oldCatalog.promise;
      },
      "GET /api/v1/ai/settings": (_url, init) => {
        if (new Headers(init?.headers).get("Authorization") === "Bearer token-b") return json(settings({ revision: 11, effective: effective("model-b", "low") }));
        return ++settingsReadsA === 1 ? json(settings({ revision: 2, preference_activity: "active", preferences: { ...emptyAiPreferences(), default_model: "model-a" } })) : oldSettings.promise;
      },
      "PUT /api/v1/ai/settings": () => json({ detail: "private conflict payload" }, 409),
    });
    sessionStorage.setItem(TOKEN, "token-a"); vi.stubGlobal("fetch", api.mock);
    render(<MemoryRouter initialEntries={["/settings/ai"]}><AuthProvider><SwitchAccount /><App /></AuthProvider></MemoryRouter>);
    await waitForSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "model-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save AI settings" }));
    await waitFor(() => expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Reloading the authoritative model catalog and saved settings"));
    expect(screen.queryByLabelText("Default model")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Switch account" }));
    await waitFor(() => expect(document.body).toHaveTextContent(/Revision 11/));
    oldCatalog.resolve(json(catalog("ollama", false)));
    oldSettings.resolve(json(settings({ provider: "ollama", user_overrides_supported: false, revision: 99 })));
    await waitFor(() => expect(document.body).toHaveTextContent(/Revision 11/));
    expect(document.body).toHaveTextContent(/Currently effective: Backend Label B/);
    expect(screen.getByLabelText("Default model")).toHaveValue("");
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(1);
  });

  it("keeps loading non-writable until the initial paired response settles", async () => {
    const catalogs = deferred<Response>(); const saved = deferred<Response>();
    const api = fetcher({ "GET /api/v1/ai/models": () => catalogs.promise, "GET /api/v1/ai/settings": () => saved.promise });
    renderSettings(api.mock);
    expect(await screen.findByRole("status")).toHaveTextContent("Loading AI model catalog and saved settings");
    expect(screen.queryByRole("button", { name: "Save AI settings" })).not.toBeInTheDocument();
    expect(api.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
    catalogs.resolve(json(catalog())); saved.resolve(json(settings()));
    await waitForSettings();
  });

  it("renders non-table responsive override cards and preserves existing workspace navigation", async () => {
    renderSettings(fetcher().mock);
    await waitForSettings();
    for (const name of ["Profile", "CV", "Career Adviser", "Jobs", "Applications", "Tracking", "Saved searches", "Settings"]) {
      expect(screen.getAllByRole("link", { name }).length).toBeGreaterThan(0);
    }
    openAdvanced();
    expect(document.querySelector("table")).toBeNull();
    expect(document.querySelectorAll(".ai-operation-card")).toHaveLength(9);
  });
});

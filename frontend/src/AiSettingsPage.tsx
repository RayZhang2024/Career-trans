import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { AiModelCatalog, AiPreferences, AiSettings, AiSettingsReplace, ReasoningEffort, SemanticOperation } from "./api";
import { SEMANTIC_OPERATION_ORDER } from "./api";
import { ApiError, useAuth } from "./auth";
import {
  REASONING_EFFORT_LABELS,
  canonicalizeAiPreferences,
  catalogSettingsCoherent,
  effortOptionsFor,
  emptyAiPreferences,
  hasResettablePreferences,
  isEditableSettings,
  modelLabel,
  preferencesEqual,
  replacePayload,
  validateDraftMatrix,
} from "./aiSettings";

type ReferenceState = { catalog: AiModelCatalog; settings: AiSettings; draft: AiPreferences };
type ReadyView = {
  kind: "ready"; ownerId: string | null; catalog: AiModelCatalog; settings: AiSettings;
  baseline: AiPreferences; draft: AiPreferences; pending: boolean; notice?: string; noticeRole?: "status" | "alert";
};
type View =
  | { kind: "loading"; ownerId: string | null }
  | { kind: "load_error"; ownerId: string | null; message: string }
  | { kind: "reconciling"; ownerId: string | null; reference?: ReferenceState }
  | { kind: "unavailable"; ownerId: string | null; message: string; reference?: ReferenceState }
  | ReadyView;

const activityCopy: Record<AiSettings["preference_activity"], string> = {
  inherited: "Using deployment defaults",
  active: "Custom AI settings active",
  inactive_provider_mismatch: "Saved settings are retained but inactive because the deployment provider changed",
  inactive_invalid: "Saved settings are retained but no longer valid under the current supported catalog",
  unsupported: "User model overrides are not available for the current provider",
};

const statusLabel = (settings: AiSettings) => settings.preference_activity === "inherited"
  ? "Deployment defaults"
  : settings.preference_activity === "active"
    ? "Custom settings active"
    : settings.preference_activity === "inactive_provider_mismatch"
      ? "Retained, inactive settings"
      : settings.preference_activity === "inactive_invalid"
        ? "Saved settings need attention"
        : "Overrides unavailable";

function canonicalTypedSettings(settings: AiSettings): AiPreferences {
  return canonicalizeAiPreferences(settings.preferences);
}

function preferenceSummary(preferences: AiPreferences, catalog: AiModelCatalog): string {
  const overrides = SEMANTIC_OPERATION_ORDER.flatMap(({ id, label }) => {
    const value = preferences.operation_overrides[id];
    if (!value) return [];
    const parts = [value.model ? modelLabel(catalog, value.model) : null, value.reasoning_effort ?? null].filter(Boolean);
    return parts.length ? [`${label}: ${parts.join(" · ")}`] : [];
  });
  const summary = [
    preferences.default_model ? modelLabel(catalog, preferences.default_model) : null,
    preferences.default_reasoning_effort ? REASONING_EFFORT_LABELS[preferences.default_reasoning_effort] : null,
    ...overrides,
  ].filter(Boolean);
  return summary.length ? summary.join("; ") : "No typed overrides are available to display.";
}

function modelOptions(catalog: AiModelCatalog, current: string | null, inheritLabel: string) {
  const isKnown = current === null || catalog.models.some((model) => model.id === current);
  return <>
    {!isKnown && <option value={current ?? ""} disabled>Unsupported saved model: {current}</option>}
    <option value="">{inheritLabel}</option>
    {catalog.models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
  </>;
}

function effortOptions(catalog: AiModelCatalog, preferences: AiPreferences, operation: SemanticOperation | undefined, current: ReasoningEffort | null) {
  const available = effortOptionsFor(catalog, preferences, operation);
  const compatible = current === null || available.includes(current);
  return <>
    {!compatible && current && <option value={current} disabled>{REASONING_EFFORT_LABELS[current]} — not supported by selected model</option>}
    <option value="">Use inherited/default reasoning</option>
    {available.map((effort) => <option key={effort} value={effort}>{REASONING_EFFORT_LABELS[effort]}</option>)}
  </>;
}

function EffectiveSummary({ settings, catalog, operation }: { settings: AiSettings; catalog: AiModelCatalog; operation: SemanticOperation }) {
  const effective = settings.effective[operation];
  const model = modelLabel(catalog, effective.model);
  const effort = effective.reasoning_effort ? REASONING_EFFORT_LABELS[effective.reasoning_effort] : "Provider/deployment default reasoning";
  return <p className="ai-effective"><strong>Currently effective:</strong> {model} · {effort}<br /><span className="muted">Saved inheritance: model {effective.inherited_model ? "inherited" : "overridden"}; reasoning {effective.inherited_reasoning_effort ? "inherited" : "overridden"}.</span></p>;
}

export function AiSettingsPage() {
  const { api, user } = useAuth();
  const ownerId = user?.id ?? null;
  const [view, setView] = useState<View>({ kind: "loading", ownerId });
  const viewRef = useRef(view);
  const generation = useRef(0);
  const alive = useRef(false);
  const ownerRef = useRef<string | null>(ownerId);
  viewRef.current = view;
  if (ownerRef.current !== ownerId) {
    ownerRef.current = ownerId;
    generation.current += 1;
  }

  const publish = (next: View) => { viewRef.current = next; setView(next); };
  const referenceOf = (current: View): ReferenceState | undefined => {
    if (current.kind === "ready") return { catalog: current.catalog, settings: current.settings, draft: current.draft };
    return current.kind === "unavailable" || current.kind === "reconciling" ? current.reference : undefined;
  };
  const isCurrent = (request: number, owner: string | null) => alive.current && generation.current === request && ownerRef.current === owner;

  const loadPair = async (
    mode: "initial" | "explicit" | "reconcile",
    owner: string | null = ownerRef.current,
    notice?: string,
    reference?: ReferenceState,
  ) => {
    const request = ++generation.current;
    if (mode === "initial") publish({ kind: "loading", ownerId: owner });
    else publish({ kind: "reconciling", ownerId: owner, reference: reference ?? referenceOf(viewRef.current) });
    try {
      const [catalog, settings] = await Promise.all([
        api.request<AiModelCatalog>("/api/v1/ai/models"),
        api.request<AiSettings>("/api/v1/ai/settings"),
      ]);
      if (!isCurrent(request, owner)) return;
      if (!catalogSettingsCoherent(catalog, settings)) {
        publish({ kind: "unavailable", ownerId: owner, message: "AI configuration changed while this page was loading. Reload the current settings.", reference: { catalog, settings, draft: canonicalTypedSettings(settings) } });
        return;
      }
      const baseline = canonicalTypedSettings(settings);
      publish({ kind: "ready", ownerId: owner, catalog, settings, baseline, draft: baseline, pending: false, notice: notice ?? (mode === "explicit" ? "Current saved settings have been reloaded." : undefined), noticeRole: "status" });
    } catch {
      if (!isCurrent(request, owner)) return;
      const message = "Current AI settings could not be reloaded, so changes are disabled until the authoritative server state is available.";
      if (mode === "initial") publish({ kind: "load_error", ownerId: owner, message: "AI settings are unavailable. Retry to load the model catalog and saved settings." });
      else publish({ kind: "unavailable", ownerId: owner, message, reference: reference ?? referenceOf(viewRef.current) });
    }
  };

  useEffect(() => {
    alive.current = true;
    void loadPair("initial", ownerId);
    return () => { alive.current = false; generation.current += 1; };
    // The session owner and API instance define the authoritative view lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, ownerId]);

  const updateDraft = (transform: (draft: AiPreferences) => AiPreferences) => {
    const current = viewRef.current;
    if (current.kind !== "ready" || current.pending || !isEditableSettings(current.settings, current.catalog)) return;
    publish({ ...current, draft: canonicalizeAiPreferences(transform(current.draft)), notice: undefined, noticeRole: undefined });
  };

  const updateOperation = (operation: SemanticOperation, field: "model" | "reasoning_effort", value: string) => {
    updateDraft((draft) => {
      const current = draft.operation_overrides[operation] ?? {};
      const replacement = field === "model"
        ? { ...current, model: value || null }
        : { ...current, reasoning_effort: (value || null) as ReasoningEffort | null };
      const normalized = { ...draft.operation_overrides };
      if ((replacement.model ?? null) === null && (replacement.reasoning_effort ?? null) === null) delete normalized[operation];
      else normalized[operation] = replacement;
      return { ...draft, operation_overrides: normalized };
    });
  };

  const performMutation = async (target: AiPreferences, action: "save" | "reset") => {
    const current = viewRef.current;
    if (current.kind !== "ready" || current.pending || !isEditableSettings(current.settings, current.catalog)) return;
    const validation = validateDraftMatrix(current.catalog, target);
    if (action === "save" && (validation.invalidDefaultModel || validation.invalidDefaultEffort || validation.invalidOperations.length > 0)) return;

    const owner = current.ownerId;
    const request = ++generation.current;
    const payload: AiSettingsReplace = replacePayload(target, current.settings.revision);
    publish({ ...current, pending: true, notice: undefined, noticeRole: undefined });
    try {
      const saved = await api.request<AiSettings>("/api/v1/ai/settings", { method: "PUT", body: JSON.stringify(payload) });
      if (!isCurrent(request, owner)) return;
      if (!catalogSettingsCoherent(current.catalog, saved)) {
        const ref = { catalog: current.catalog, settings: saved, draft: canonicalizeAiPreferences(target) };
        await loadPair("reconcile", owner, "The save response used a changed AI configuration. The current catalog and saved settings were reloaded.", ref);
        return;
      }
      const baseline = canonicalTypedSettings(saved);
      publish({ kind: "ready", ownerId: owner, catalog: current.catalog, settings: saved, baseline, draft: baseline, pending: false, notice: action === "reset" ? "AI settings were reset to deployment defaults." : "AI settings saved for future semantic work.", noticeRole: "status" });
    } catch (cause) {
      if (!isCurrent(request, owner)) return;
      const reference = { catalog: current.catalog, settings: current.settings, draft: canonicalizeAiPreferences(target) };
      if (cause instanceof ApiError && cause.status === 409) {
        await loadPair("reconcile", owner, "AI settings changed elsewhere. The latest saved settings were loaded; your previous changes were not reapplied.", reference);
      } else if (cause instanceof ApiError && cause.status === 422) {
        publish({ ...current, draft: current.draft, pending: false, notice: "These AI settings are not valid for the current model/provider configuration. Review the selections and try again.", noticeRole: "alert" });
      } else if (!(cause instanceof ApiError) || cause.status >= 500) {
        await loadPair("reconcile", owner, "The save request was interrupted. Current saved settings have been reloaded; no automatic retry was performed.", reference);
      } else {
        publish({ ...current, pending: false, notice: "AI settings could not be saved. Review the current settings or reload them.", noticeRole: "alert" });
      }
    }
  };

  const visibleView = view.ownerId === ownerId ? view : { kind: "loading" as const, ownerId };
  if (visibleView.kind === "loading") return <main className="ai-settings-page"><header className="workspace-header"><div><p className="eyebrow"><Link to="/settings/ai">Settings</Link> / AI Models</p><h1>AI Models</h1></div></header><section className="card"><p role="status">Loading AI model catalog and saved settings…</p></section></main>;
  if (visibleView.kind === "load_error" || visibleView.kind === "reconciling" || visibleView.kind === "unavailable") {
    const reference = visibleView.kind === "unavailable" || visibleView.kind === "reconciling" ? visibleView.reference : undefined;
    const message = visibleView.kind === "load_error" || visibleView.kind === "unavailable" ? visibleView.message : "Reloading the authoritative model catalog and saved settings…";
    return <main className="ai-settings-page"><header className="workspace-header"><div><p className="eyebrow">Settings / AI Models</p><h1>AI Models</h1></div></header>
      <section className="card ai-state-card"><p role={visibleView.kind === "reconciling" ? "status" : "alert"}>{message}</p>
        {visibleView.kind !== "reconciling" && <button type="button" onClick={() => void loadPair("explicit", ownerId)}>Reload current AI settings</button>}
        {reference && <div className="ai-read-only-reference"><p>Previous or attempted values are shown for reference only and cannot authorize a save.</p><p>{preferenceSummary(reference.draft, reference.catalog)}</p></div>}
      </section></main>;
  }

  const catalog = visibleView.catalog;
  const settings = visibleView.settings;
  const draft = visibleView.draft;
  const canEdit = isEditableSettings(settings, catalog);
  const dirty = !preferencesEqual(draft, visibleView.baseline);
  const validation = validateDraftMatrix(catalog, draft);
  const invalid = validation.invalidDefaultModel || validation.invalidDefaultEffort || validation.invalidOperations.length > 0;
  const saveDisabled = !canEdit || visibleView.pending || !dirty || invalid;
  const resetVisible = canEdit && hasResettablePreferences(settings);
  const setDefault = (field: "default_model" | "default_reasoning_effort", value: string) => updateDraft((old) => ({
    ...old,
    [field]: field === "default_model" ? value || null : (value || null) as ReasoningEffort | null,
  }));
  const currentActivity = activityCopy[settings.preference_activity];

  return <main className="ai-settings-page">
    <header className="workspace-header"><div><p className="eyebrow"><Link to="/settings/ai">Settings</Link> / AI Models</p><h1>AI Models</h1></div></header>
    <nav className="settings-tabs" aria-label="Settings"><Link aria-current="page" to="/settings/ai">AI Models</Link><Link to="/settings/discovery">Job Discovery</Link></nav>
    <div className="ai-settings-content">
      <section className="card ai-boundary-copy">
        <h2>About these settings</h2>
        <p>The semantic provider is controlled by your deployment. Provider credentials are managed by the server and are not shown here.</p>
        <p>These preferences affect future semantic work only. Saved rankings, adviser assessments, preparations, and other historical outputs are not rewritten.</p>
        <p>Host-side Codex external discovery is separate and is not configured here.</p>
      </section>

      <section className="card ai-provider-summary" aria-label="AI provider and settings status">
        <h2>Current AI configuration</h2>
        <dl className="ai-summary-grid">
          <div><dt>Provider</dt><dd>{settings.provider}</dd></div>
          <div><dt>User model settings</dt><dd>{settings.user_overrides_supported ? "Available" : "Not available for this provider"}</dd></div>
          <div><dt>Preference activity</dt><dd>{statusLabel(settings)}</dd></div>
          {settings.persisted_override_provider && settings.persisted_override_provider !== settings.provider && <div><dt>Saved settings provider</dt><dd>{settings.persisted_override_provider}</dd></div>}
        </dl>
        <p className="muted">Revision {settings.revision} · {currentActivity}</p>
      </section>

      {!canEdit && <section className="card ai-read-only" aria-label="Read-only saved AI preferences">
        <h2>Saved preferences are read-only</h2>
        <p>{settings.preference_activity === "inactive_provider_mismatch" ? "Saved preferences are retained for their original provider. They are not changed by this page." : "The current deployment does not support editing these preferences."}</p>
        <p><strong>Retained typed preferences:</strong> {preferenceSummary(settings.preferences, catalog)}</p>
        <h3>Currently effective saved runtime</h3>
        <div className="ai-operation-list">{SEMANTIC_OPERATION_ORDER.map(({ id, label }) => <article className="ai-operation-card" key={id}><h4>{label}</h4><EffectiveSummary settings={settings} catalog={catalog} operation={id} /></article>)}</div>
      </section>}

      {canEdit && <>
        {settings.preference_activity === "inactive_invalid" && <p className="notice" role="status">Some saved preferences are no longer valid. Unsupported values remain visible; choose a supported value, inherit a default, or reset explicitly.</p>}
        {settings.preference_activity === "inactive_invalid" && preferencesEqual(settings.preferences, emptyAiPreferences()) && settings.revision > 0 && <p className="notice" role="status">Saved AI settings exist but cannot be represented safely by the current UI. No hidden values are shown. The current effective deployment runtime remains available below; you may explicitly reset the saved row.</p>}

        <section className="card ai-defaults-section">
          <h2>Default semantic settings</h2>
          <label htmlFor="ai-default-model">Default model</label>
          <select id="ai-default-model" value={draft.default_model ?? ""} disabled={visibleView.pending} aria-invalid={validation.invalidDefaultModel || undefined} onChange={(event) => setDefault("default_model", event.target.value)}>{modelOptions(catalog, draft.default_model, "Use deployment default")}</select>
          {validation.invalidDefaultModel && <p className="ai-validation" role="alert">The saved default model is no longer supported. Select a supported model or use the deployment default.</p>}
          <label htmlFor="ai-default-effort">Default reasoning effort</label>
          <select id="ai-default-effort" value={draft.default_reasoning_effort ?? ""} disabled={visibleView.pending} aria-invalid={validation.invalidDefaultEffort || undefined} onChange={(event) => setDefault("default_reasoning_effort", event.target.value)}>{effortOptions(catalog, draft, undefined, draft.default_reasoning_effort)}</select>
          <p className="muted">“Use inherited/default reasoning” is different from “No reasoning”.</p>
        </section>

        <details className="card ai-advanced">
          <summary>Advanced per-operation overrides</summary>
          <p className="muted">Overrides apply only to the named semantic operation. Inherit both fields to remove that operation from the saved override document.</p>
          <div className="ai-operation-list">
            {SEMANTIC_OPERATION_ORDER.map(({ id, label }) => {
              const override = draft.operation_overrides[id] ?? {};
              const invalidOperation = validation.invalidOperations.includes(id);
              return <article className="ai-operation-card" key={id}>
                <h3>{label}</h3>
                <label htmlFor={`ai-operation-model-${id}`}>Model override</label>
                <select id={`ai-operation-model-${id}`} value={override.model ?? ""} disabled={visibleView.pending} aria-invalid={invalidOperation || undefined} onChange={(event) => updateOperation(id, "model", event.target.value)}>{modelOptions(catalog, override.model ?? null, "Inherit default")}</select>
                <label htmlFor={`ai-operation-effort-${id}`}>Reasoning override</label>
                <select id={`ai-operation-effort-${id}`} value={override.reasoning_effort ?? ""} disabled={visibleView.pending} aria-invalid={invalidOperation || undefined} onChange={(event) => updateOperation(id, "reasoning_effort", event.target.value)}>{effortOptions(catalog, draft, id, override.reasoning_effort ?? null)}</select>
                <EffectiveSummary settings={settings} catalog={catalog} operation={id} />
                {invalidOperation && <p className="ai-validation" role="alert">{label} has a saved or selected model/reasoning combination that is not supported. The draft is unchanged; explicitly correct or inherit a value.</p>}
              </article>;
            })}
          </div>
        </details>

        {invalid && <section className="ai-validation-summary" role="alert"><strong>Review the AI settings before saving.</strong>{validation.invalidOperations.length > 0 && <p>Locally incompatible operations: {validation.invalidOperations.map((operation) => SEMANTIC_OPERATION_ORDER.find((item) => item.id === operation)?.label ?? operation).join(", ")}.</p>}</section>}
        {visibleView.pending && <p role="status">Saving AI settings…</p>}
        {visibleView.notice && <p role={visibleView.noticeRole ?? "status"}>{visibleView.notice}</p>}
        <div className="ai-actions">
          <button type="button" disabled={saveDisabled} onClick={() => void performMutation(draft, "save")}>Save AI settings</button>
          {resetVisible && <button type="button" className="button-secondary" disabled={visibleView.pending} onClick={() => void performMutation(emptyAiPreferences(), "reset")}>Reset to deployment defaults</button>}
          <button type="button" className="button-secondary" disabled={visibleView.pending} onClick={() => void loadPair("explicit", ownerId)}>Reload current settings</button>
        </div>
      </>}
    </div>
  </main>;
}

import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type {
  DiscoveryScheduleAcquisition,
  DiscoveryScheduleCreate,
  DiscoveryScheduleEvaluation,
  DiscoverySchedulePatch,
  DiscoveryScheduleQuery,
  DiscoveryScheduleRead,
  DiscoveryScheduleSpec,
  StructuredAtsScheduleConfig,
} from "./api";
import { ApiError, useAuth } from "./auth";

type ListState = { phase: "loading" | "loaded" | "error"; items?: DiscoveryScheduleRead[]; error?: string };
type ScopeMode = "unfiltered" | "filtered" | "legacy-mixed";
type Draft = {
  name: string; enabled: boolean; cadence: "daily" | "weekly"; timezone: string; localTime: string; weekdays: number[];
  keywords: string; locations: string; remotePolicy: "any" | "exclude_remote" | "legacy_true";
  excludedCompanies: string; excludedTitleTerms: string; employmentTypes: string;
  atsEnabled: boolean; atsScopeMode: ScopeMode; atsCompanies: string; atsProviders: string[]; atsMaxSources: string; atsMaxResults: string;
  agenticEnabled: boolean; agenticMaxQueries: string; agenticResultsPerQuery: string; agenticMaxPages: string; agenticMaxJobs: string;
  maxSemanticCandidates: string; maxFullAnalyses: string; minRelevanceScore: string;
};
type Editor = { id: string | null; loading: boolean; baseline?: DiscoveryScheduleRead; draft: Draft; dirty: Set<keyof Draft>; error?: string; pending?: boolean };
type DraftKey = keyof Draft;

const providers = ["greenhouse", "ashby", "lever", "smartrecruiters", "recruitee"] as const;
const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] as const;
const emptyList: ListState = { phase: "loading" };
const trimLines = (value: string) => value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
const sameArray = (left: string[] | number[], right: string[] | number[]) => left.length === right.length && left.every((value, index) => value === right[index]);
const numeric = (value: string) => Number(value);
const browserTimezone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; } };

function blankDraft(): Draft {
  return {
    name: "", enabled: false, cadence: "daily", timezone: browserTimezone(), localTime: "09:00:00", weekdays: [],
    keywords: "", locations: "", remotePolicy: "any", excludedCompanies: "", excludedTitleTerms: "", employmentTypes: "",
    atsEnabled: false, atsScopeMode: "unfiltered", atsCompanies: "", atsProviders: [], atsMaxSources: "20", atsMaxResults: "100",
    agenticEnabled: false, agenticMaxQueries: "6", agenticResultsPerQuery: "10", agenticMaxPages: "12", agenticMaxJobs: "20",
    maxSemanticCandidates: "10", maxFullAnalyses: "5", minRelevanceScore: "0.5",
  };
}

function toDraft(value: DiscoveryScheduleRead): Draft {
  const ats = value.acquisition.structured_ats;
  const hasFilters = ats.companies.length > 0 || ats.providers.length > 0;
  const mode: ScopeMode = ats.all_resolved_sources && hasFilters ? "legacy-mixed" : ats.all_resolved_sources ? "unfiltered" : "filtered";
  return {
    name: value.name, enabled: value.enabled, cadence: value.schedule.cadence, timezone: value.schedule.timezone,
    localTime: value.schedule.local_time, weekdays: [...value.schedule.weekdays], keywords: value.query.keywords.join("\n"),
    locations: value.query.locations.join("\n"), remotePolicy: value.query.remote_ok === true ? "legacy_true" : value.query.remote_ok === false ? "exclude_remote" : "any",
    excludedCompanies: value.query.excluded_companies.join("\n"), excludedTitleTerms: value.query.excluded_title_terms.join("\n"), employmentTypes: value.query.employment_types.join("\n"),
    atsEnabled: ats.enabled, atsScopeMode: mode, atsCompanies: ats.companies.join("\n"), atsProviders: [...ats.providers], atsMaxSources: String(ats.max_sources), atsMaxResults: String(ats.max_results),
    agenticEnabled: value.acquisition.agentic_web.enabled, agenticMaxQueries: String(value.acquisition.agentic_web.max_search_queries),
    agenticResultsPerQuery: String(value.acquisition.agentic_web.max_search_results_per_query), agenticMaxPages: String(value.acquisition.agentic_web.max_pages_to_open), agenticMaxJobs: String(value.acquisition.agentic_web.max_discovered_jobs),
    maxSemanticCandidates: String(value.evaluation.max_semantic_candidates), maxFullAnalyses: String(value.evaluation.max_full_analyses), minRelevanceScore: String(value.evaluation.min_relevance_score),
  };
}

function formatNextRun(value: string, timezone: string): string {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return `${value} (backend timestamp)`;
  try { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: timezone }).format(instant); }
  catch { return `${instant.toISOString()} (UTC fallback; configured timezone: ${timezone})`; }
}

function scheduleSpec(draft: Draft): DiscoveryScheduleSpec {
  return { cadence: draft.cadence, timezone: draft.timezone.trim(), local_time: draft.localTime, weekdays: draft.cadence === "weekly" ? [...draft.weekdays].sort((a, b) => a - b) : [] };
}

function queryFromDraft(draft: Draft): DiscoveryScheduleQuery {
  return {
    keywords: trimLines(draft.keywords), locations: trimLines(draft.locations), remote_ok: draft.remotePolicy === "exclude_remote" ? false : draft.remotePolicy === "legacy_true" ? true : null,
    companies: [], excluded_companies: trimLines(draft.excludedCompanies), excluded_title_terms: trimLines(draft.excludedTitleTerms), employment_types: trimLines(draft.employmentTypes), max_results: 50,
  };
}

function acquisitionFromDraft(draft: Draft, existing?: DiscoveryScheduleAcquisition): DiscoveryScheduleAcquisition {
  const oldAts = existing?.structured_ats;
  const ats: StructuredAtsScheduleConfig = {
    enabled: draft.atsEnabled,
    companies: draft.atsScopeMode === "unfiltered" ? [] : trimLines(draft.atsCompanies),
    providers: draft.atsScopeMode === "unfiltered" ? [] : [...draft.atsProviders],
    all_resolved_sources: draft.atsScopeMode === "unfiltered" || draft.atsScopeMode === "legacy-mixed" && oldAts?.all_resolved_sources === true,
    max_sources: numeric(draft.atsMaxSources), max_results: numeric(draft.atsMaxResults),
  };
  return {
    structured_ats: ats,
    agentic_web: {
      enabled: draft.agenticEnabled, country: existing?.agentic_web.country ?? "gb",
      max_search_queries: numeric(draft.agenticMaxQueries), max_search_results_per_query: numeric(draft.agenticResultsPerQuery),
      max_pages_to_open: numeric(draft.agenticMaxPages), max_discovered_jobs: numeric(draft.agenticMaxJobs),
    },
  };
}

function evaluationFromDraft(draft: Draft): DiscoveryScheduleEvaluation {
  return { max_semantic_candidates: numeric(draft.maxSemanticCandidates), max_full_analyses: numeric(draft.maxFullAnalyses), min_relevance_score: numeric(draft.minRelevanceScore) };
}

function buildCreate(draft: Draft): DiscoveryScheduleCreate {
  return { name: draft.name.trim(), enabled: draft.enabled, schedule: scheduleSpec(draft), query: queryFromDraft(draft), acquisition: acquisitionFromDraft(draft), evaluation: evaluationFromDraft(draft) };
}

function buildPatch(fresh: DiscoveryScheduleRead, draft: Draft, dirty: Set<DraftKey>): DiscoverySchedulePatch {
  const patch: DiscoverySchedulePatch = {};
  if (dirty.has("name") && draft.name.trim() !== fresh.name) patch.name = draft.name.trim();
  if (dirty.has("enabled") && draft.enabled !== fresh.enabled) patch.enabled = draft.enabled;

  const nextSchedule = { ...fresh.schedule };
  if (dirty.has("cadence")) nextSchedule.cadence = draft.cadence;
  if (dirty.has("timezone")) nextSchedule.timezone = draft.timezone.trim();
  if (dirty.has("localTime")) nextSchedule.local_time = draft.localTime;
  if (dirty.has("weekdays")) nextSchedule.weekdays = draft.cadence === "weekly" ? [...draft.weekdays].sort((a, b) => a - b) : [];
  if (JSON.stringify(nextSchedule) !== JSON.stringify(fresh.schedule)) patch.schedule = nextSchedule;

  const nextQuery = { ...fresh.query };
  const candidateQuery = queryFromDraft(draft);
  if (dirty.has("keywords") && !sameArray(candidateQuery.keywords, fresh.query.keywords)) nextQuery.keywords = candidateQuery.keywords;
  if (dirty.has("locations") && !sameArray(candidateQuery.locations, fresh.query.locations)) nextQuery.locations = candidateQuery.locations;
  if (dirty.has("remotePolicy") && candidateQuery.remote_ok !== fresh.query.remote_ok) nextQuery.remote_ok = candidateQuery.remote_ok;
  if (dirty.has("excludedCompanies") && !sameArray(candidateQuery.excluded_companies, fresh.query.excluded_companies)) nextQuery.excluded_companies = candidateQuery.excluded_companies;
  if (dirty.has("excludedTitleTerms") && !sameArray(candidateQuery.excluded_title_terms, fresh.query.excluded_title_terms)) nextQuery.excluded_title_terms = candidateQuery.excluded_title_terms;
  if (dirty.has("employmentTypes") && !sameArray(candidateQuery.employment_types, fresh.query.employment_types)) nextQuery.employment_types = candidateQuery.employment_types;
  if (JSON.stringify(nextQuery) !== JSON.stringify(fresh.query)) patch.query = nextQuery;

  const nextAts = { ...fresh.acquisition.structured_ats };
  const nextAgentic = { ...fresh.acquisition.agentic_web };
  if (dirty.has("atsEnabled")) nextAts.enabled = draft.atsEnabled;
  if (dirty.has("atsScopeMode")) {
    nextAts.all_resolved_sources = draft.atsScopeMode === "unfiltered";
    if (draft.atsScopeMode === "unfiltered") { nextAts.companies = []; nextAts.providers = []; }
  }
  if (dirty.has("atsCompanies")) nextAts.companies = draft.atsScopeMode === "unfiltered" ? [] : trimLines(draft.atsCompanies);
  if (dirty.has("atsProviders")) nextAts.providers = draft.atsScopeMode === "unfiltered" ? [] : [...draft.atsProviders];
  if (dirty.has("atsMaxSources")) nextAts.max_sources = numeric(draft.atsMaxSources);
  if (dirty.has("atsMaxResults")) nextAts.max_results = numeric(draft.atsMaxResults);
  if (dirty.has("agenticEnabled")) nextAgentic.enabled = draft.agenticEnabled;
  if (dirty.has("agenticMaxQueries")) nextAgentic.max_search_queries = numeric(draft.agenticMaxQueries);
  if (dirty.has("agenticResultsPerQuery")) nextAgentic.max_search_results_per_query = numeric(draft.agenticResultsPerQuery);
  if (dirty.has("agenticMaxPages")) nextAgentic.max_pages_to_open = numeric(draft.agenticMaxPages);
  if (dirty.has("agenticMaxJobs")) nextAgentic.max_discovered_jobs = numeric(draft.agenticMaxJobs);
  const nextAcquisition = { structured_ats: nextAts, agentic_web: nextAgentic };
  if (JSON.stringify(nextAcquisition) !== JSON.stringify(fresh.acquisition)) patch.acquisition = nextAcquisition;

  const nextEvaluation = { ...fresh.evaluation };
  if (dirty.has("maxSemanticCandidates")) nextEvaluation.max_semantic_candidates = numeric(draft.maxSemanticCandidates);
  if (dirty.has("maxFullAnalyses")) nextEvaluation.max_full_analyses = numeric(draft.maxFullAnalyses);
  if (dirty.has("minRelevanceScore")) nextEvaluation.min_relevance_score = numeric(draft.minRelevanceScore);
  if (JSON.stringify(nextEvaluation) !== JSON.stringify(fresh.evaluation)) patch.evaluation = nextEvaluation;
  return patch;
}

function validate(draft: Draft, preserveLegacyMixed: boolean): string | null {
  const name = draft.name.trim();
  if (!name) return "Enter a configuration name.";
  if (name.length > 200) return "Configuration names can be at most 200 characters.";
  if (!trimLines(draft.keywords).length) return "Add at least one prioritisation theme.";
  if (!draft.atsEnabled && !draft.agenticEnabled) return "Choose at least one acquisition channel before saving.";
  if (draft.atsEnabled && draft.atsScopeMode === "filtered" && !trimLines(draft.atsCompanies).length && !draft.atsProviders.length) return "Choose at least one company or provider filter for filtered resolved sources.";
  if (draft.atsEnabled && draft.atsScopeMode === "legacy-mixed" && !preserveLegacyMixed) return "Choose an explicit Structured ATS source-scope mode before changing this legacy/mixed scope.";
  const bounds: Array<[string, number, number, string]> = [
    [draft.atsMaxSources, 1, 100, "Structured ATS maximum sources"], [draft.atsMaxResults, 1, 100, "Structured ATS maximum results"],
    [draft.agenticMaxQueries, 1, 100, "Maximum search queries"], [draft.agenticResultsPerQuery, 1, 50, "Maximum search results per query"],
    [draft.agenticMaxPages, 1, 100, "Maximum pages to open"], [draft.agenticMaxJobs, 1, 100, "Maximum discovered jobs"],
    [draft.maxSemanticCandidates, 1, 100, "Maximum semantic candidates"], [draft.maxFullAnalyses, 1, 30, "Maximum full analyses"],
  ];
  for (const [value, min, max, label] of bounds) if (!Number.isInteger(numeric(value)) || numeric(value) < min || numeric(value) > max) return `${label} must be between ${min} and ${max}.`;
  const relevance = numeric(draft.minRelevanceScore);
  if (!Number.isFinite(relevance) || relevance < 0 || relevance > 1) return "Minimum relevance score must be between 0 and 1.";
  if (!draft.timezone.trim()) return "Enter an IANA timezone.";
  if (!/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?$/.test(draft.localTime)) return "Enter a valid local time in HH:mm, HH:mm:ss, or HH:mm:ss.fraction format.";
  if (draft.cadence === "weekly" && draft.weekdays.length === 0) return "Choose at least one weekday for a weekly schedule.";
  return null;
}

function channelSummary(value: DiscoveryScheduleRead): string {
  const active = [value.acquisition.structured_ats.enabled ? "Structured ATS enabled" : null, value.acquisition.agentic_web.enabled ? "Profile-driven web discovery enabled" : null].filter(Boolean);
  const dormant = [!value.acquisition.structured_ats.enabled && (value.acquisition.structured_ats.companies.length || value.acquisition.structured_ats.providers.length || value.acquisition.structured_ats.all_resolved_sources) ? "Structured ATS configuration stored but disabled" : null, !value.acquisition.agentic_web.enabled ? "Profile-driven web configuration stored but disabled" : null].filter(Boolean);
  return [...active, ...dormant].join(" · ") || "No active acquisition channel";
}

export function JobsSearchesPage() {
  const { api } = useAuth();
  const [list, setList] = useState<ListState>(emptyList);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [createPending, setCreatePending] = useState(false);
  const [pendingSchedules, setPendingSchedules] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const alive = useRef(false);
  const listGeneration = useRef(0);
  const editorGeneration = useRef(0);
  const mutationLocks = useRef(new Set<string>());

  const refreshList = async (): Promise<boolean> => {
    const generation = ++listGeneration.current;
    setList((old) => ({ ...old, phase: old.items ? "loaded" : "loading", error: undefined }));
    try {
      const items = await api.request<DiscoveryScheduleRead[]>("/api/v1/jobs/discovery-schedules");
      if (!alive.current || generation !== listGeneration.current) return false;
      setList({ phase: "loaded", items }); return true;
    } catch {
      if (alive.current && generation === listGeneration.current) setList((old) => ({ phase: "error", items: old.items, error: "Saved discovery configurations are unavailable." }));
      return false;
    }
  };

  useEffect(() => {
    alive.current = true; void refreshList();
    return () => { alive.current = false; listGeneration.current += 1; editorGeneration.current += 1; };
  }, [api]);

  const openCreate = () => { editorGeneration.current += 1; setError(""); setMessage(""); setEditor({ id: null, loading: false, draft: blankDraft(), dirty: new Set() }); };
  const clearStaleSchedule = async (scheduleId: string) => {
    setEditor((current) => current?.id === scheduleId ? null : current);
    setMessage("This saved discovery is no longer available. The saved-configuration list has been refreshed.");
    setError(""); await refreshList();
  };
  const openEdit = async (scheduleId: string) => {
    const generation = ++editorGeneration.current;
    setEditor({ id: scheduleId, loading: true, draft: blankDraft(), dirty: new Set() }); setError(""); setMessage("");
    try {
      const baseline = await api.request<DiscoveryScheduleRead>(`/api/v1/jobs/discovery-schedules/${encodeURIComponent(scheduleId)}`);
      if (alive.current && generation === editorGeneration.current) setEditor({ id: scheduleId, loading: false, baseline, draft: toDraft(baseline), dirty: new Set() });
    } catch (cause) {
      if (!alive.current || generation !== editorGeneration.current) return;
      if (cause instanceof ApiError && cause.status === 404) { await clearStaleSchedule(scheduleId); return; }
      setEditor((current) => current?.id === scheduleId ? { ...current, loading: false, error: "This saved configuration could not be loaded." } : current);
    }
  };
  const discard = async () => {
    if (!editor) return;
    if (editor.id === null) { editorGeneration.current += 1; setEditor(null); setError(""); return; }
    await openEdit(editor.id);
  };
  const change = <K extends DraftKey>(key: K, value: Draft[K]) => {
    setEditor((old) => {
      if (!old || old.loading || old.pending) return old;
      const dirty = new Set(old.dirty); dirty.add(key);
      const draft = { ...old.draft, [key]: value } as Draft;
      if (key === "cadence" && value === "daily") { draft.weekdays = []; dirty.add("weekdays"); }
      if (key === "atsCompanies" && trimLines(String(value)).length && draft.atsScopeMode !== "filtered") { draft.atsScopeMode = "filtered"; dirty.add("atsScopeMode"); }
      if (key === "atsProviders" && (value as string[]).length && draft.atsScopeMode !== "filtered") { draft.atsScopeMode = "filtered"; dirty.add("atsScopeMode"); }
      return { ...old, draft, dirty, error: undefined };
    });
  };
  const changeScope = (mode: "filtered" | "unfiltered") => {
    setEditor((old) => old ? { ...old, draft: { ...old.draft, atsScopeMode: mode, ...(mode === "unfiltered" ? { atsCompanies: "", atsProviders: [] } : {}) }, dirty: new Set([...old.dirty, "atsScopeMode", ...(mode === "unfiltered" ? ["atsCompanies" as const, "atsProviders" as const] : [])]), error: undefined } : old);
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const current = editor;
    if (!current || current.loading || current.pending) return;
    const scopeFields: DraftKey[] = ["atsScopeMode", "atsCompanies", "atsProviders"];
    const preserveLegacyMixed = !!current.baseline && current.draft.atsScopeMode === "legacy-mixed" && !scopeFields.some((key) => current.dirty.has(key));
    const problem = validate(current.draft, preserveLegacyMixed);
    if (problem) { setEditor({ ...current, error: problem }); return; }
    const lock = current.id ? `save:${current.id}` : "create";
    if (mutationLocks.current.has(lock)) return;
    const ownerGeneration = editorGeneration.current;
    const ownsEditor = () => alive.current && ownerGeneration === editorGeneration.current;
    mutationLocks.current.add(lock); setError(""); setMessage("");
    if (current.id === null) {
      setCreatePending(true);
      try {
        await api.request<DiscoveryScheduleRead>("/api/v1/jobs/discovery-schedules", { method: "POST", body: JSON.stringify(buildCreate(current.draft)) });
        if (ownsEditor()) setEditor(null);
        setMessage("Saved discovery created. Refreshing the saved-configuration list…");
        const refreshed = await refreshList();
        setMessage(refreshed ? "Saved discovery created. The saved-configuration list is current." : "Saved discovery was created, but the list refresh could not be confirmed. Previously loaded configurations remain shown.");
      } catch { if (ownsEditor()) setError("Saved discovery could not be created."); }
      finally { mutationLocks.current.delete(lock); setCreatePending(false); }
      return;
    }
    const id = current.id;
    setEditor((old) => old?.id === id ? { ...old, pending: true, error: undefined } : old);
    try {
      const requiresFreshRead = [...current.dirty].some((key) => ["cadence", "timezone", "localTime", "weekdays", "keywords", "locations", "remotePolicy", "excludedCompanies", "excludedTitleTerms", "employmentTypes", "atsEnabled", "atsScopeMode", "atsCompanies", "atsProviders", "atsMaxSources", "atsMaxResults", "agenticEnabled", "agenticMaxQueries", "agenticResultsPerQuery", "agenticMaxPages", "agenticMaxJobs", "maxSemanticCandidates", "maxFullAnalyses", "minRelevanceScore"].includes(key));
      let fresh = current.baseline!;
      if (requiresFreshRead) fresh = await api.request<DiscoveryScheduleRead>(`/api/v1/jobs/discovery-schedules/${encodeURIComponent(id)}`);
      const patch = buildPatch(fresh, current.draft, current.dirty);
      if (Object.keys(patch).length === 0) {
        if (ownsEditor()) setEditor({ id, loading: false, baseline: fresh, draft: toDraft(fresh), dirty: new Set() });
        setMessage("There are no saved changes to apply."); return;
      }
      const updated = await api.request<DiscoveryScheduleRead>(`/api/v1/jobs/discovery-schedules/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) });
      if (ownsEditor()) setEditor({ id, loading: false, baseline: updated, draft: toDraft(updated), dirty: new Set() });
      setMessage("Saved discovery updated. Refreshing the saved-configuration list…");
      const refreshed = await refreshList();
      if (!refreshed) setMessage(ownsEditor()
        ? "Saved discovery was updated, but the list refresh could not be confirmed. The editor shows the saved response."
        : `Saved discovery ${updated.name} was updated, but the list refresh could not be confirmed.`);
      else setMessage(`Saved discovery ${updated.name} updated.`);
    } catch (cause) {
      if (!ownsEditor()) return;
      if (cause instanceof ApiError && cause.status === 404) { await clearStaleSchedule(id); return; }
      setEditor((old) => old?.id === id ? { ...old, pending: false, error: "Changes could not be saved. The persisted configuration was not changed by this form." } : old);
    } finally { mutationLocks.current.delete(lock); if (ownsEditor()) setEditor((old) => old?.id === id ? { ...old, pending: false } : old); }
  };

  const setScheduleEnabled = async (schedule: DiscoveryScheduleRead, enabled: boolean) => {
    const id = schedule.id; const lock = `toggle:${id}`;
    if (editor?.id === id && editor.dirty.size > 0 || mutationLocks.current.has(lock) || pendingSchedules.has(id)) return;
    mutationLocks.current.add(lock); setPendingSchedules((old) => new Set(old).add(id)); setError(""); setMessage("");
    try {
      const updated = await api.request<DiscoveryScheduleRead>(`/api/v1/jobs/discovery-schedules/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
      setMessage(enabled ? `Recurrence resumed for ${updated.name}.` : `Future recurrence paused for ${updated.name}; an already-running execution is not cancelled.`);
      const refreshed = await refreshList();
      if (!refreshed) setMessage(enabled ? `Recurrence resumed for ${updated.name}, but list refresh could not be confirmed.` : `Future recurrence paused for ${updated.name}; an already-running execution is not cancelled. The list refresh could not be confirmed.`);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) { await clearStaleSchedule(id); return; }
      setError("The recurrence setting could not be changed.");
    } finally { mutationLocks.current.delete(lock); setPendingSchedules((old) => { const next = new Set(old); next.delete(id); return next; }); }
  };

  const renderEditor = (value: Editor) => {
    if (value.loading) return <section className="card schedule-editor" aria-label="Saved discovery editor"><p role="status">Loading saved configuration…</p></section>;
    const draft = value.draft;
    const legacyProviders = value.baseline?.acquisition.structured_ats.providers.filter((provider) => !providers.includes(provider as typeof providers[number])) ?? [];
    const updateWeekday = (day: number, checked: boolean) => change("weekdays", checked ? [...new Set([...draft.weekdays, day])].sort((a, b) => a - b) : draft.weekdays.filter((existing) => existing !== day));
    return <section className="card schedule-editor" aria-label={value.id ? "Edit saved discovery" : "Create saved discovery"}>
      <div className="section-heading"><div><h2>{value.id ? "Edit saved discovery" : "New saved discovery"}</h2><p className="muted">This form edits configuration only. It does not start a discovery.</p></div></div>
      {value.error && <p role="alert">{value.error}</p>}
      <form className="form-stack" onSubmit={(event) => void save(event)}>
        <fieldset className="schedule-fieldset"><legend>Saved configuration</legend><label htmlFor="schedule-name">Name</label><input id="schedule-name" value={draft.name} maxLength={200} onChange={(event) => change("name", event.target.value)} disabled={value.pending || createPending} />
          <label className="check-line"><input type="checkbox" checked={draft.enabled} onChange={(event) => change("enabled", event.target.checked)} disabled={value.pending || createPending} /> Recurrence enabled</label>
          <p className="muted">Enabled means recurrence is configured. Automatic due execution requires the Career-trans scheduler operator and required server-side providers to be available. Current local Compose does not continuously run that operator.</p>
          <p className="muted">Saving does not run anything. A later operator run coalesces missed schedule slots rather than replaying each missed occurrence.</p>
        </fieldset>
        <fieldset className="schedule-fieldset"><legend>Recurrence</legend><label htmlFor="schedule-cadence">Cadence</label><select id="schedule-cadence" value={draft.cadence} onChange={(event) => change("cadence", event.target.value as Draft["cadence"])} disabled={value.pending || createPending}><option value="daily">Daily</option><option value="weekly">Weekly</option></select>
          {draft.cadence === "weekly" && <fieldset className="weekday-picker"><legend>Run on (Monday=0 through Sunday=6)</legend>{weekdays.map((day, index) => <label className="check-line" key={day}><input type="checkbox" checked={draft.weekdays.includes(index)} onChange={(event) => updateWeekday(index, event.target.checked)} disabled={value.pending || createPending} />{day}</label>)}</fieldset>}
          <label htmlFor="schedule-time">Configured local time</label><input id="schedule-time" type="text" inputMode="numeric" placeholder="09:30:00" value={draft.localTime} onChange={(event) => change("localTime", event.target.value)} disabled={value.pending || createPending} />
          <label htmlFor="schedule-timezone">IANA timezone</label><input id="schedule-timezone" value={draft.timezone} onChange={(event) => change("timezone", event.target.value)} disabled={value.pending || createPending} />
          <p className="muted">The backend chooses the authoritative next due time. Daylight-saving gaps are skipped; repeated local times use the first occurrence.</p>
        </fieldset>
        <fieldset className="schedule-fieldset"><legend>Search criteria</legend><label htmlFor="schedule-keywords">Prioritisation themes (one per line)</label><textarea id="schedule-keywords" value={draft.keywords} onChange={(event) => change("keywords", event.target.value)} disabled={value.pending || createPending} /><p className="muted">Themes prioritise structured ATS candidates softly; they are not exact web-search terms or eligibility filters. Profile-driven web discovery builds strategy from confirmed candidate context, not from these literal saved themes.</p>
          <label htmlFor="schedule-locations">Eligibility locations (one complete location per line)</label><textarea id="schedule-locations" value={draft.locations} onChange={(event) => change("locations", event.target.value)} disabled={value.pending || createPending} />
          <label htmlFor="schedule-remote">Remote policy</label><select id="schedule-remote" value={draft.remotePolicy} onChange={(event) => change("remotePolicy", event.target.value as Draft["remotePolicy"])} disabled={value.pending || createPending}><option value="any">No remote restriction</option><option value="exclude_remote">Exclude remote jobs</option>{draft.remotePolicy === "legacy_true" && <option value="legacy_true">No remote restriction — legacy stored value preserved</option>}</select>
          <label htmlFor="schedule-excluded-companies">Excluded companies (one per line)</label><textarea id="schedule-excluded-companies" value={draft.excludedCompanies} onChange={(event) => change("excludedCompanies", event.target.value)} disabled={value.pending || createPending} />
          <label htmlFor="schedule-excluded-titles">Excluded title terms (one per line)</label><textarea id="schedule-excluded-titles" value={draft.excludedTitleTerms} onChange={(event) => change("excludedTitleTerms", event.target.value)} disabled={value.pending || createPending} />
          <label htmlFor="schedule-employment-types">Employment types (one per line)</label><textarea id="schedule-employment-types" value={draft.employmentTypes} onChange={(event) => change("employmentTypes", event.target.value)} disabled={value.pending || createPending} />
          <p className="muted">New configurations keep positive query companies empty and use query max results 50. These are separate from channel-specific result limits.</p>
        </fieldset>
        <fieldset className="schedule-fieldset"><legend>Acquisition channels</legend><p className="muted">Choose at least one channel explicitly. Saving is configuration-only and does not check readiness. Running a saved discovery requires confirmed candidate context and the required server-side providers.</p>
          <label className="check-line"><input type="checkbox" checked={draft.atsEnabled} onChange={(event) => change("atsEnabled", event.target.checked)} disabled={value.pending || createPending} /> Structured ATS — already-resolved career sources</label>
          <p className="muted">This channel reads the resolved career-source registry. A company filter does not resolve a source; no matching resolved source can yield zero jobs without proving the employer has no open roles.</p>
          {!draft.atsEnabled && (legacyProviders.length > 0 || draft.atsScopeMode === "legacy-mixed") && <div className="legacy-state"><strong>Legacy Structured ATS configuration is stored but disabled</strong>{draft.atsScopeMode === "legacy-mixed" && <p>Legacy/mixed persisted source scope is preserved until Structured ATS is enabled and you explicitly choose a new mode.</p>}{legacyProviders.length > 0 && <p>Legacy persisted provider filters: {legacyProviders.join(", ")}. They remain stored while this channel is disabled.</p>}</div>}
          {draft.atsEnabled && <div className="nested-controls">
            {draft.atsScopeMode === "legacy-mixed" ? <div className="legacy-state"><strong>Legacy/mixed persisted source scope</strong><p>This stored configuration combines no company/provider filter with filters. It is preserved on unrelated edits. Choose a new mode only to explicitly normalize it.</p><button type="button" className="button-secondary" onClick={() => changeScope("unfiltered")} disabled={value.pending || createPending}>Normalize to unfiltered resolved sources</button><button type="button" className="button-secondary" onClick={() => changeScope("filtered")} disabled={value.pending || createPending}>Normalize to filtered resolved sources</button></div> : <fieldset className="weekday-picker"><legend>Resolved-source scope</legend>
              <label className="check-line"><input type="radio" name="ats-scope" checked={draft.atsScopeMode === "unfiltered"} onChange={() => changeScope("unfiltered")} disabled={value.pending || createPending} /> No company/provider filter — scan up to the configured maximum number of resolved sources.</label>
              <label className="check-line"><input type="radio" name="ats-scope" checked={draft.atsScopeMode === "filtered"} onChange={() => changeScope("filtered")} disabled={value.pending || createPending} /> Filtered resolved sources</label>
            </fieldset>}
            {draft.atsScopeMode !== "unfiltered" && <>
              <label htmlFor="ats-companies">Resolved company filters (one company name per line)</label><textarea id="ats-companies" value={draft.atsCompanies} onChange={(event) => change("atsCompanies", event.target.value)} disabled={value.pending || createPending} />
              <p className="muted">Company values match canonical resolved company names after case/punctuation normalization. They are exact matches, not partial-text searches. Company values OR together; provider values OR together; when both dimensions are used, both must match.</p>
              <fieldset className="weekday-picker"><legend>Provider filters</legend>{providers.map((provider) => <label className="check-line" key={provider}><input type="checkbox" checked={draft.atsProviders.includes(provider)} onChange={(event) => change("atsProviders", event.target.checked ? [...draft.atsProviders.filter((item) => providers.includes(item as typeof providers[number])), provider] : draft.atsProviders.filter((item) => item !== provider))} disabled={value.pending || createPending} />{provider}</label>)}</fieldset>
              {!!legacyProviders.length && <div className="legacy-state"><strong>Legacy persisted provider filters</strong><p>{legacyProviders.join(", ")}</p><p>These unsupported stored values are preserved during unrelated edits. Changing provider filters is an explicit normalization.</p></div>}
            </>}
            <label htmlFor="ats-max-sources">Maximum resolved sources (1–100)</label><input id="ats-max-sources" type="number" min={1} max={100} value={draft.atsMaxSources} onChange={(event) => change("atsMaxSources", event.target.value)} disabled={value.pending || createPending} />
            <label htmlFor="ats-max-results">Maximum ATS results (1–100)</label><input id="ats-max-results" type="number" min={1} max={100} value={draft.atsMaxResults} onChange={(event) => change("atsMaxResults", event.target.value)} disabled={value.pending || createPending} />
          </div>}
          <label className="check-line"><input type="checkbox" checked={draft.agenticEnabled} onChange={(event) => change("agenticEnabled", event.target.checked)} disabled={value.pending || createPending} /> Profile-driven bounded server-side web discovery</label>
          <p className="muted">This is not Codex. Search strategy comes from confirmed candidate context; saved themes do not become literal web queries. Host Codex discovery remains outside the browser and containers.</p>
          {draft.agenticEnabled && <div className="nested-controls"><label htmlFor="agentic-queries">Maximum search queries (1–100)</label><input id="agentic-queries" type="number" min={1} max={100} value={draft.agenticMaxQueries} onChange={(event) => change("agenticMaxQueries", event.target.value)} disabled={value.pending || createPending} />
            <label htmlFor="agentic-results">Maximum search results per query (1–50)</label><input id="agentic-results" type="number" min={1} max={50} value={draft.agenticResultsPerQuery} onChange={(event) => change("agenticResultsPerQuery", event.target.value)} disabled={value.pending || createPending} />
            <label htmlFor="agentic-pages">Maximum pages to open (1–100)</label><input id="agentic-pages" type="number" min={1} max={100} value={draft.agenticMaxPages} onChange={(event) => change("agenticMaxPages", event.target.value)} disabled={value.pending || createPending} />
            <label htmlFor="agentic-jobs">Maximum discovered jobs (1–100)</label><input id="agentic-jobs" type="number" min={1} max={100} value={draft.agenticMaxJobs} onChange={(event) => change("agenticMaxJobs", event.target.value)} disabled={value.pending || createPending} />
          </div>}
        </fieldset>
        <fieldset className="schedule-fieldset"><legend>Evaluation budgets</legend><label htmlFor="max-semantic">Maximum semantic candidates (1–100)</label><input id="max-semantic" type="number" min={1} max={100} value={draft.maxSemanticCandidates} onChange={(event) => change("maxSemanticCandidates", event.target.value)} disabled={value.pending || createPending} />
          <label htmlFor="max-analysis">Maximum full analyses (1–30)</label><input id="max-analysis" type="number" min={1} max={30} value={draft.maxFullAnalyses} onChange={(event) => change("maxFullAnalyses", event.target.value)} disabled={value.pending || createPending} />
          <label htmlFor="min-relevance">Minimum relevance score (0–1)</label><input id="min-relevance" type="number" min={0} max={1} step="0.01" value={draft.minRelevanceScore} onChange={(event) => change("minRelevanceScore", event.target.value)} disabled={value.pending || createPending} />
        </fieldset>
        <div className="card-actions"><button type="submit" disabled={value.pending || createPending}>{value.pending || createPending ? "Saving…" : "Save configuration"}</button><button type="button" className="button-secondary" onClick={() => void discard()} disabled={value.pending || createPending}>Discard changes</button></div>
        <p className="muted">Manual Run now and execution history are not available here yet; they belong to the follow-up V2B2 milestone.</p>
      </form>
    </section>;
  };

  return <main className="jobs-searches-page">
    <header className="workspace-header searches-header"><div><p className="eyebrow">Jobs workspace</p><h1>Saved discovery configurations</h1><p className="muted">Configure saved search criteria and recurrence. This page does not execute discovery.</p><Link to="/jobs">Back to Jobs workspace</Link></div><button type="button" onClick={openCreate}>New saved discovery</button></header>
    {message && <p className="notice" role="status">{message}</p>}{error && <p role="alert">{error}</p>}
    <p className="muted">The saved-configuration endpoint returns the complete list; this view does not paginate it. A saved recurrence setting is not proof that the scheduler operator is running. Automatic due execution requires the server-side operator and required providers; missed slots are coalesced. Broad host Codex discovery remains a separate host-side workflow.</p>
    <section className="schedule-list" aria-labelledby="saved-list-heading"><div className="section-heading"><div><h2 id="saved-list-heading">Your saved discoveries</h2><p className="muted">Configurations are shown in backend order.</p></div><button type="button" className="button-secondary" onClick={() => void refreshList()}>Refresh list</button></div>
      {list.phase === "loading" && !list.items && <p className="muted" role="status">Loading saved discoveries…</p>}
      {list.phase === "error" && !list.items && <div className="section-error"><p role="alert">{list.error}</p><button type="button" className="button-secondary" onClick={() => void refreshList()}>Retry list</button></div>}
      {list.phase === "error" && !!list.items && <p className="notice" role="status">List refresh failed. Previously loaded saved configurations remain shown.</p>}
      {list.phase === "loaded" && list.items?.length === 0 && <p className="muted">No saved discovery configurations yet.</p>}
      {!!list.items?.length && <ul className="saved-schedule-list">{list.items.map((schedule) => {
        const dirty = editor?.id === schedule.id && editor.dirty.size > 0;
        const pending = pendingSchedules.has(schedule.id) || editor?.id === schedule.id && !!editor.pending;
        return <li className="card saved-schedule-card" key={schedule.id}>
          <div className="section-heading"><div><h3>{schedule.name}</h3><p>{schedule.enabled ? "Recurrence configured" : "Paused"} · {schedule.schedule.cadence === "daily" ? "Daily" : `Weekly · ${schedule.schedule.weekdays.map((day) => weekdays[day] ?? `Day ${day}`).join(", ")}`} · {schedule.schedule.local_time} ({schedule.schedule.timezone})</p></div><button type="button" className="button-secondary" onClick={() => void openEdit(schedule.id)} disabled={pending}>Edit</button></div>
          {schedule.enabled && <p>Next due: {schedule.next_run_at ? formatNextRun(schedule.next_run_at, schedule.schedule.timezone) : "Next due time is unavailable."}</p>}
          {schedule.last_execution_at && <p>Last recorded execution completion: {new Date(schedule.last_execution_at).toLocaleString()}</p>}
          <p>Prioritisation themes: {schedule.query.keywords.join(" · ")}</p><p>Eligibility locations: {schedule.query.locations.length ? schedule.query.locations.join(" · ") : "Any"} · Remote policy: {schedule.query.remote_ok === false ? "Exclude remote" : schedule.query.remote_ok === true ? "No remote restriction — legacy stored value preserved" : "No remote restriction"}</p>
          <p>Acquisition: {channelSummary(schedule)}</p><p>Evaluation budgets: {schedule.evaluation.max_semantic_candidates} semantic candidates · {schedule.evaluation.max_full_analyses} full analyses · minimum relevance {schedule.evaluation.min_relevance_score}</p>
          {schedule.enabled
            ? <p className="muted">Recurrence is configured for this saved discovery. Automatic due execution requires the Career-trans scheduler operator and required server-side providers to be available in the deployment.</p>
            : <p className="muted">Automatic recurrence is paused for this saved discovery. Pausing affects future automatic recurrence only and does not cancel an already-running execution.</p>}
          {dirty && <p className="notice">Save or discard changes before pausing or resuming this configuration.</p>}
          <div className="card-actions"><button type="button" className="button-secondary" onClick={() => void setScheduleEnabled(schedule, !schedule.enabled)} disabled={dirty || pending || mutationLocks.current.has(`toggle:${schedule.id}`)}>{pending ? "Updating…" : schedule.enabled ? "Pause recurrence" : "Resume recurrence"}</button></div>
        </li>;
      })}</ul>}
    </section>
    {editor && renderEditor(editor)}
  </main>;
}

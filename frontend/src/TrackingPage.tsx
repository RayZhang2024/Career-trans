import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, type ApplicationTracking, type ApplicationTrackingListItem, type ApplicationTrackingStatus } from "./api";
import { useAuth } from "./auth";
import { createApplicationTracking } from "./applicationTrackingController";

const statuses: ApplicationTrackingStatus[] = ["prepared", "applied", "interview", "rejected", "offer", "withdrawn"];
const statusLabel = (value: ApplicationTrackingStatus) => value[0].toUpperCase() + value.slice(1);
const recordedTime = (value: string) => new Date(value).toLocaleString();
type TrackingState = { phase: "loading" } | { phase: "ready"; value: ApplicationTracking } | { phase: "error"; message: string };
type StartState = { phase: "loading" } | { phase: "untracked" } | { phase: "ready"; value: ApplicationTracking } | { phase: "error"; message: string };

function monotonic(current: ApplicationTracking | undefined, next: ApplicationTracking, expectedId: string): boolean {
  return next.id === expectedId && next.revision >= (current?.id === expectedId ? current.revision : 0);
}

export function PreparationTrackingPanel({ preparationId }: { preparationId: string }) {
  const { api, user } = useAuth();
  const [state, setState] = useState<StartState>({ phase: "loading" });
  const [initialStatus, setInitialStatus] = useState<ApplicationTrackingStatus>("prepared");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const accepted = useRef<ApplicationTracking | undefined>(undefined);

  async function lookup(current: number, ownerId: string | undefined): Promise<ApplicationTracking | undefined> {
    try {
      const value = await api.request<ApplicationTracking>(`/api/v1/application-tracking/by-preparation/${encodeURIComponent(preparationId)}`);
      if (generation.current !== current || ownerId !== user?.id || value.preparation_id !== preparationId) return undefined;
      if (monotonic(accepted.current, value, value.id)) {
        accepted.current = value;
        setState({ phase: "ready", value });
        return value;
      }
      return accepted.current;
    } catch (error) {
      if (generation.current !== current || ownerId !== user?.id || (error as Error)?.name === "AbortError") return undefined;
      if (error instanceof ApiError && error.status === 404) {
        accepted.current = undefined;
        setState({ phase: "untracked" });
        return undefined;
      }
      setState({ phase: "error", message: "Tracking information is temporarily unavailable. Preparation review remains available." });
      return undefined;
    }
  }

  useEffect(() => {
    const current = ++generation.current;
    const ownerId = user?.id;
    accepted.current = undefined;
    setState({ phase: "loading" }); setInitialStatus("prepared"); setPending(false); setNotice("");
    if (ownerId) void lookup(current, ownerId);
    return () => { generation.current += 1; };
  }, [api, preparationId, user?.id]);

  async function reconcile(message: string) {
    const current = generation.current;
    setNotice(message);
    await lookup(current, user?.id);
  }

  async function startTracking() {
    if (pending) return;
    setPending(true); setNotice("");
    const current = generation.current;
    const ownerId = user?.id;
    try {
      const result = await createApplicationTracking(api, preparationId, initialStatus);
      if (result.kind === "locked") return;
      if (result.kind === "error") throw result.error;
      const value = result.value;
      if (generation.current === current && ownerId === user?.id && value.preparation_id === preparationId && monotonic(accepted.current, value, value.id)) {
        accepted.current = value; setState({ phase: "ready", value });
      }
    } catch (error) {
      if (generation.current !== current || ownerId !== user?.id || (error as Error)?.name === "AbortError") return;
      if (error instanceof ApiError && error.status === 409) {
        await reconcile("Tracking may already exist. The currently recorded state is shown if available; the request is not repeated.");
      } else if (!(error instanceof ApiError)) {
        await reconcile("The start request was interrupted. Any currently recorded tracking state is shown without attributing its cause.");
      } else if (error.status === 404) {
        setState({ phase: "error", message: "This preparation is not available to this account." });
      } else {
        setState({ phase: "error", message: "Tracking could not be started. You can try explicitly again." });
      }
    } finally {
      if (generation.current === current && ownerId === user?.id) setPending(false);
    }
  }

  if (state.phase === "loading") return <section className="card application-section" aria-label="Application tracking"><h2>Application tracking</h2><p role="status">Checking tracking…</p></section>;
  if (state.phase === "error") return <section className="card application-section" aria-label="Application tracking"><h2>Application tracking</h2><p role="alert">{state.message}</p><button type="button" className="button-secondary" onClick={() => { const current = ++generation.current; setState({ phase: "loading" }); void lookup(current, user?.id); }}>Retry tracking lookup</button></section>;
  if (state.phase === "ready") return <section className="card application-section" aria-label="Application tracking"><h2>Application tracking</h2><p>Current recorded status: <strong>{statusLabel(state.value.current_status)}</strong></p><Link to={`/tracking/${encodeURIComponent(state.value.id)}`}>Open tracking history</Link>{notice && <p role="status">{notice}</p>}</section>;
  return <section className="card application-section" aria-label="Application tracking"><h2>Start application tracking</h2><p className="muted">Record this application's current status in Career-trans. This does not submit or update anything with the employer.</p><label htmlFor="initial-tracking-status">Initial recorded status</label><select id="initial-tracking-status" value={initialStatus} onChange={(event) => setInitialStatus(event.target.value as ApplicationTrackingStatus)}>{statuses.map((item) => <option key={item} value={item}>{statusLabel(item)}</option>)}</select><button type="button" disabled={pending} onClick={() => void startTracking()}>{pending ? "Starting tracking…" : "Start tracking"}</button>{notice && <p role="status">{notice}</p>}</section>;
}

export function TrackingPage() {
  const { api } = useAuth();
  const [items, setItems] = useState<ApplicationTrackingListItem[] | undefined>(undefined);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const alive = useRef(false);
  const load = async () => {
    const current = ++generation.current;
    setError("");
    try {
      const value = await api.request<ApplicationTrackingListItem[]>("/api/v1/application-tracking");
      if (alive.current && current === generation.current) setItems(value);
    } catch (reason) {
      if (alive.current && current === generation.current && (reason as Error)?.name !== "AbortError") setError("Tracked applications are unavailable.");
    }
  };
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; generation.current += 1; }; }, [api]);
  return <main className="applications-page tracking-page"><header className="workspace-header"><div><p className="eyebrow">Career workspace</p><h1>Tracking</h1><p className="muted">Manually recorded application status. Career-trans does not submit applications or infer employer activity.</p></div><Link to="/applications">Applications</Link></header><div className="section-heading"><h2>Tracked applications</h2><button type="button" className="button-secondary" onClick={() => void load()}>Refresh</button></div>{!items && !error && <p role="status">Loading tracked applications…</p>}{error && <p role="alert">{error}</p>}{items?.length === 0 && <p className="muted">No applications are being tracked yet. Start tracking from a saved preparation.</p>}{!!items?.length && <div className="application-list">{items.map((item) => <article className="card application-summary" key={item.id}><p className="eyebrow">{statusLabel(item.current_status)} · Updated {recordedTime(item.updated_at)}</p><h2><Link to={`/tracking/${encodeURIComponent(item.id)}`}>{item.target.title}</Link></h2><p>{[item.target.company, item.target.location].filter(Boolean).join(" · ") || "Company and location not recorded"}</p><p><Link to={`/tracking/${encodeURIComponent(item.id)}`}>Open tracking history</Link> · <Link to={`/applications/${encodeURIComponent(item.preparation_id)}`}>Open preparation</Link></p></article>)}</div>}</main>;
}

export function TrackingDetailPage() {
  const { trackingId = "" } = useParams();
  const { api, user } = useAuth();
  const [state, setState] = useState<TrackingState>({ phase: "loading" });
  const [selectedStatus, setSelectedStatus] = useState<ApplicationTrackingStatus | "">("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const generation = useRef(0);
  const accepted = useRef<ApplicationTracking | undefined>(undefined);
  const pendingRef = useRef(false);

  function accept(value: ApplicationTracking, requestGeneration: number, ownerId: string | undefined) {
    if (generation.current !== requestGeneration || ownerId !== user?.id || !monotonic(accepted.current, value, trackingId)) return false;
    accepted.current = value; setState({ phase: "ready", value });
    if (selectedStatus === value.current_status) setSelectedStatus("");
    return true;
  }

  async function load(current: number, ownerId: string | undefined) {
    try {
      const value = await api.request<ApplicationTracking>(`/api/v1/application-tracking/${encodeURIComponent(trackingId)}`);
      if (accept(value, current, ownerId)) setError("");
    } catch (reason) {
      if (generation.current !== current || ownerId !== user?.id || (reason as Error)?.name === "AbortError") return;
      if (reason instanceof ApiError && reason.status === 404) { accepted.current = undefined; setState({ phase: "error", message: "This tracking record is not available to this account." }); }
      else setError("Tracking details are temporarily unavailable.");
    }
  }

  useEffect(() => {
    const current = ++generation.current;
    const ownerId = user?.id;
    accepted.current = undefined;
    pendingRef.current = false;
    setState({ phase: "loading" }); setSelectedStatus(""); setPending(false); setError(""); setNotice("");
    if (ownerId) void load(current, ownerId);
    return () => { generation.current += 1; };
  }, [api, trackingId, user?.id]);

  async function reconcile(current: number, ownerId: string | undefined, message: string) {
    setNotice(message);
    await load(current, ownerId);
  }

  function releasePendingUpdate() {
    pendingRef.current = false;
    setPending(false);
  }

  function refresh() {
    if (pendingRef.current) return;
    const current = ++generation.current;
    setError("");
    void load(current, user?.id);
  }

  async function submitStatus() {
    const current = generation.current;
    const ownerId = user?.id;
    const value = state.phase === "ready" ? state.value : undefined;
    if (!value || !selectedStatus || pendingRef.current) return;
    pendingRef.current = true; setPending(true); setNotice(""); setError("");
    try {
      const updated = await api.request<ApplicationTracking>(`/api/v1/application-tracking/${encodeURIComponent(trackingId)}/status-events`, {
        method: "POST", body: JSON.stringify({ status: selectedStatus, expected_revision: value.revision }),
      });
      if (accept(updated, current, ownerId)) { setSelectedStatus(""); }
    } catch (reason) {
      if (generation.current !== current || ownerId !== user?.id || (reason as Error)?.name === "AbortError") return;
      if (reason instanceof ApiError && reason.status === 404) {
        accepted.current = undefined; setState({ phase: "error", message: "This tracking record is not available to this account." });
      } else if (reason instanceof ApiError && reason.status === 409) {
        // The write has completed; only its read-only reconciliation is pending.
        // A newer explicit refresh may supersede that read without leaving the
        // record permanently locked when the older generation settles.
        releasePendingUpdate();
        await reconcile(current, ownerId, "This tracking record changed before your update could be applied. The latest saved state is shown.");
      } else if (!(reason instanceof ApiError)) {
        releasePendingUpdate();
        await reconcile(current, ownerId, "The update request was interrupted. The currently saved state is shown; its cause cannot be confirmed.");
      } else setError("The status update could not be completed.");
    } finally {
      if (generation.current === current && ownerId === user?.id) { pendingRef.current = false; setPending(false); }
    }
  }

  if (state.phase === "loading") return <main className="applications-page"><p role="status">Loading tracking details…</p></main>;
  if (state.phase === "error") return <main className="applications-page"><p role="alert">{state.message}</p><Link to="/tracking">Back to Tracking</Link></main>;
  const value = state.value;
  return <main className="applications-page tracking-detail-page"><header className="workspace-header"><div><p className="eyebrow">Recorded application history</p><h1>{value.target.title}</h1><p className="muted"><Link to="/tracking">Back to Tracking</Link></p></div><button type="button" className="button-secondary" disabled={pending} onClick={refresh}>Refresh tracking</button></header><section className="card application-section"><h2>Historical target</h2><p>{[value.target.company, value.target.location].filter(Boolean).join(" · ") || "Company and location not recorded"}</p>{value.target.public_url && <p><a href={value.target.public_url} target="_blank" rel="noopener noreferrer">Open saved public vacancy</a></p>}<p><Link to={`/applications/${encodeURIComponent(value.preparation_id)}`}>Open saved preparation</Link></p></section><section className="card application-section"><h2>Current recorded status</h2><p><strong>{statusLabel(value.current_status)}</strong> · Revision {value.revision}</p><label htmlFor="tracking-next-status">Record another status</label><select id="tracking-next-status" value={selectedStatus} onChange={(event) => setSelectedStatus(event.target.value as ApplicationTrackingStatus | "")}><option value="">Choose a different status</option>{statuses.filter((item) => item !== value.current_status).map((item) => <option key={item} value={item}>{statusLabel(item)}</option>)}</select><button type="button" disabled={!selectedStatus || pending} onClick={() => void submitStatus()}>{pending ? "Saving status…" : "Save status"}</button>{notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}</section><section className="card application-section"><h2>Status history</h2><p className="muted">Recorded time is when this status was saved in Career-trans; it may differ from the employer event date.</p><ol>{[...value.events].sort((a, b) => a.revision - b.revision).map((event) => <li key={event.revision}><strong>{statusLabel(event.to_status)}</strong><p>Recorded {recordedTime(event.recorded_at)} · Revision {event.revision}</p></li>)}</ol></section></main>;
}

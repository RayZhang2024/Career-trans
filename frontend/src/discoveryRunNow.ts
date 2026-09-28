import { ApiError, type DiscoveryScheduleRead, type ScheduledExecutionRead, type SessionApi } from "./api";

type RunNowApi = Pick<SessionApi, "request">;
type ExecutionConfig = Pick<DiscoveryScheduleRead, "query" | "acquisition" | "evaluation">;
export type DiscoveryRunReconciliation<T> = { kind: "refreshed"; value?: T } | { kind: "failed" } | { kind: "stale" } | { kind: "superseded" } | { kind: "session_stale" };
export type DiscoveryRunNowResult<T> =
  | { kind: "started"; schedule: DiscoveryScheduleRead; execution: ScheduledExecutionRead }
  | { kind: "changed"; schedule: DiscoveryScheduleRead }
  | { kind: "preflight_stale" }
  | { kind: "preflight_rejected"; status: number }
  | { kind: "preflight_unavailable" }
  | { kind: "already_running"; reconciliation: DiscoveryRunReconciliation<T> }
  | { kind: "post_stale" }
  | { kind: "post_rejected"; status: number }
  | { kind: "uncertain"; reconciliation: DiscoveryRunReconciliation<T> };

const executionConfig = (schedule: DiscoveryScheduleRead): ExecutionConfig => ({ query: schedule.query, acquisition: schedule.acquisition, evaluation: schedule.evaluation });
const executionConfigKey = (schedule: DiscoveryScheduleRead) => JSON.stringify(executionConfig(schedule));

export class DiscoveryScheduleChangedError extends Error {
  constructor(public readonly fresh: DiscoveryScheduleRead) { super("The persisted saved discovery changed before Run now."); }
}

export class DiscoveryScheduleStaleError extends Error {
  constructor() { super("The persisted saved discovery is no longer available."); }
}

export async function runSavedDiscoveryNow(api: RunNowApi, displayed: DiscoveryScheduleRead): Promise<{ schedule: DiscoveryScheduleRead; execution: ScheduledExecutionRead }> {
  let fresh: DiscoveryScheduleRead;
  try { fresh = await api.request<DiscoveryScheduleRead>(`/api/v1/jobs/discovery-schedules/${encodeURIComponent(displayed.id)}`); }
  catch (cause) { if (cause instanceof Error && "status" in cause && (cause as { status?: unknown }).status === 404) throw new DiscoveryScheduleStaleError(); throw cause; }
  if (executionConfigKey(fresh) !== executionConfigKey(displayed)) throw new DiscoveryScheduleChangedError(fresh);
  const execution = await api.request<ScheduledExecutionRead>(`/api/v1/jobs/discovery-schedules/${encodeURIComponent(displayed.id)}/run-now`, { method: "POST" });
  return { schedule: fresh, execution };
}

export async function runSavedDiscoveryNowWithReconciliation<T>(
  api: RunNowApi,
  displayed: DiscoveryScheduleRead,
  options: { reconcile: () => Promise<DiscoveryRunReconciliation<T>>; onReconcileStart?: (reason: "already_running" | "uncertain") => void },
): Promise<DiscoveryRunNowResult<T>> {
  let fresh: DiscoveryScheduleRead;
  try {
    fresh = await api.request<DiscoveryScheduleRead>(`/api/v1/jobs/discovery-schedules/${encodeURIComponent(displayed.id)}`);
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return { kind: "preflight_stale" };
    if (cause instanceof ApiError) return { kind: "preflight_rejected", status: cause.status };
    return { kind: "preflight_unavailable" };
  }
  if (executionConfigKey(fresh) !== executionConfigKey(displayed)) return { kind: "changed", schedule: fresh };
  try {
    const execution = await api.request<ScheduledExecutionRead>(`/api/v1/jobs/discovery-schedules/${encodeURIComponent(displayed.id)}/run-now`, { method: "POST" });
    return { kind: "started", schedule: fresh, execution };
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return { kind: "post_stale" };
    if (cause instanceof ApiError && cause.status === 409) {
      options.onReconcileStart?.("already_running");
      return { kind: "already_running", reconciliation: await reconcileSafely(options) };
    }
    if (cause instanceof ApiError) return { kind: "post_rejected", status: cause.status };
    if (cause instanceof DOMException && cause.name === "AbortError") return { kind: "uncertain", reconciliation: { kind: "session_stale" } };
    options.onReconcileStart?.("uncertain");
    return { kind: "uncertain", reconciliation: await reconcileSafely(options) };
  }
}

async function reconcileSafely<T>(options: { reconcile: () => Promise<DiscoveryRunReconciliation<T>> }): Promise<DiscoveryRunReconciliation<T>> {
  try { return await options.reconcile(); } catch { return { kind: "failed" }; }
}

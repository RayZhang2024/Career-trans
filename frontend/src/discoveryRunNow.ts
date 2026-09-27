import type { DiscoveryScheduleRead, ScheduledExecutionRead, SessionApi } from "./api";

type RunNowApi = Pick<SessionApi, "request">;
type ExecutionConfig = Pick<DiscoveryScheduleRead, "query" | "acquisition" | "evaluation">;

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

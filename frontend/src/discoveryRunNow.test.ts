import { describe, expect, it, vi } from "vitest";
import { ApiError, type DiscoveryScheduleRead, type ScheduledExecutionRead } from "./api";
import { DiscoveryScheduleChangedError, DiscoveryScheduleStaleError, runSavedDiscoveryNow, runSavedDiscoveryNowWithReconciliation } from "./discoveryRunNow";

const schedule = (patch: Partial<DiscoveryScheduleRead> = {}): DiscoveryScheduleRead => ({
  id: "s-1", name: "AI roles", enabled: false,
  schedule: { cadence: "daily", timezone: "UTC", local_time: "09:00:00", weekdays: [] },
  query: { keywords: ["AI"], locations: [], remote_ok: true, companies: ["Compatibility Co"], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 73 },
  acquisition: { structured_ats: { enabled: true, companies: [], providers: [], all_resolved_sources: true, max_sources: 20, max_results: 100 }, agentic_web: { enabled: false, country: "gb", max_search_queries: 6, max_search_results_per_query: 10, max_pages_to_open: 12, max_discovered_jobs: 20 } },
  evaluation: { max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 }, next_run_at: null, last_execution_at: null, ...patch,
});
const execution: ScheduledExecutionRead = { id: "e-1", trigger_kind: "manual", scheduled_for: null, status: "completed", config_snapshot: { schedule: schedule().schedule, query: schedule().query, acquisition: schedule().acquisition, evaluation: schedule().evaluation }, discovery_run_id: "run-1", acquisition_summary: {}, failure_summary: {}, started_at: "2026-09-01T09:00:00Z", completed_at: "2026-09-01T09:01:00Z" };

describe("saved discovery Run now controller", () => {
  it("fresh-reads the persisted configuration and preserves compatibility query fields", async () => {
    const displayed = schedule(); const fresh = schedule();
    const api = { request: vi.fn().mockResolvedValueOnce(fresh).mockResolvedValueOnce(execution) };
    const result = await runSavedDiscoveryNow(api, displayed);
    expect(api.request).toHaveBeenNthCalledWith(1, "/api/v1/jobs/discovery-schedules/s-1");
    expect(api.request).toHaveBeenNthCalledWith(2, "/api/v1/jobs/discovery-schedules/s-1/run-now", { method: "POST" });
    expect(result.schedule.query.companies).toEqual(["Compatibility Co"]);
  });

  it("blocks execution when query, acquisition, or evaluation changed", async () => {
    for (const change of [{ query: { ...schedule().query, max_results: 99 } }, { acquisition: { ...schedule().acquisition, structured_ats: { ...schedule().acquisition.structured_ats, max_results: 9 } } }, { evaluation: { ...schedule().evaluation, max_full_analyses: 2 } }]) {
      const displayed = schedule(); const fresh = schedule(change); const api = { request: vi.fn().mockResolvedValueOnce(fresh) };
      await expect(runSavedDiscoveryNow(api, displayed)).rejects.toBeInstanceOf(DiscoveryScheduleChangedError);
      expect(api.request).toHaveBeenCalledTimes(1);
    }
  });

  it("reports deletion before POST and still allows a paused schedule when it exists", async () => {
    const displayed = schedule(); const missing = { request: vi.fn().mockRejectedValueOnce(Object.assign(new Error("missing"), { status: 404 })) };
    await expect(runSavedDiscoveryNow(missing, displayed)).rejects.toBeInstanceOf(DiscoveryScheduleStaleError);
    const paused = schedule({ enabled: false }); const api = { request: vi.fn().mockResolvedValueOnce(paused).mockResolvedValueOnce(execution) };
    await expect(runSavedDiscoveryNow(api, paused)).resolves.toMatchObject({ execution });
    expect(api.request).toHaveBeenCalledTimes(2);
  });

  it("shares already-running, authoritative HTTP, and transport reconciliation outcomes", async () => {
    const displayed = schedule();
    const alreadyRunning = { request: vi.fn().mockResolvedValueOnce(displayed).mockRejectedValueOnce(new ApiError(409, "conflict")) };
    const refreshed = await runSavedDiscoveryNowWithReconciliation(alreadyRunning, displayed, { reconcile: async () => ({ kind: "refreshed" as const }) });
    expect(refreshed).toEqual({ kind: "already_running", reconciliation: { kind: "refreshed" } });

    const rejected = { request: vi.fn().mockResolvedValueOnce(displayed).mockRejectedValueOnce(new ApiError(503, "unavailable")) };
    await expect(runSavedDiscoveryNowWithReconciliation(rejected, displayed, { reconcile: async () => ({ kind: "refreshed" as const }) })).resolves.toEqual({ kind: "rejected", status: 503 });

    const interrupted = { request: vi.fn().mockResolvedValueOnce(displayed).mockRejectedValueOnce(new TypeError("offline")) };
    const uncertain = await runSavedDiscoveryNowWithReconciliation(interrupted, displayed, { reconcile: async () => ({ kind: "failed" as const }) });
    expect(uncertain).toEqual({ kind: "uncertain", reconciliation: { kind: "failed" } });
  });
});

import { ApiError } from "./auth";
import type { ApplicationTracking, ApplicationTrackingStatus, SessionApi } from "./api";

const preparationLocks = new Set<string>();

export type TrackingStartResult =
  | { kind: "locked" }
  | { kind: "confirmed"; value: ApplicationTracking }
  | { kind: "reconciled_existing" }
  | { kind: "reconciliation_failed"; reconciliationError?: unknown }
  | { kind: "uncertain_reconciled" }
  | { kind: "uncertain_unconfirmed"; reconciliationError?: unknown }
  | { kind: "not_found" }
  | { kind: "failed"; error: unknown }
  | { kind: "session_stale" };

export type TrackingControllerOptions = { userId?: string; getUserId?: () => string | undefined; reconcile?: () => Promise<boolean> };

function currentSession(api: SessionApi, startingEpoch: number, startingUserId: string | undefined, getUserId?: () => string | undefined): boolean {
  return api.sessionEpoch() === startingEpoch && (!getUserId || getUserId() === startingUserId);
}

/** Shared tracking-start authority for Application Detail and Workspace Tracking. */
export async function createApplicationTracking(api: SessionApi, preparationId: string, status: ApplicationTrackingStatus, options: TrackingControllerOptions = {}): Promise<TrackingStartResult> {
  const startingEpoch = api.sessionEpoch();
  const startingUserId = options.userId ?? options.getUserId?.();
  const lockKey = `${startingUserId ?? "anonymous"}:${preparationId}`;
  if (preparationLocks.has(lockKey)) return { kind: "locked" };
  preparationLocks.add(lockKey);
  try {
    const value = await api.request<ApplicationTracking>("/api/v1/application-tracking", { method: "POST", body: JSON.stringify({ preparation_id: preparationId, status }) });
    if (!currentSession(api, startingEpoch, startingUserId, options.getUserId)) return { kind: "session_stale" };
    if (value.preparation_id !== preparationId) return { kind: "failed", error: new Error("Tracking response did not match the preparation.") };
    return { kind: "confirmed", value };
  } catch (error) {
    if (!currentSession(api, startingEpoch, startingUserId, options.getUserId) || (error as Error)?.name === "AbortError") return { kind: "session_stale" };
    if (error instanceof ApiError && error.status === 404) return { kind: "not_found" };
    if (error instanceof ApiError && error.status === 409) {
      let confirmed = false;
      let reconciliationError: unknown;
      try { confirmed = options.reconcile ? await options.reconcile() : false; } catch (reason) { reconciliationError = reason; }
      if (!currentSession(api, startingEpoch, startingUserId, options.getUserId)) return { kind: "session_stale" };
      return confirmed ? { kind: "reconciled_existing" } : { kind: "reconciliation_failed", reconciliationError };
    }
    if (!(error instanceof ApiError)) {
      let reconciliationError: unknown;
      let confirmed = false;
      try { confirmed = options.reconcile ? await options.reconcile() : false; } catch (reason) { reconciliationError = reason; }
      if (!currentSession(api, startingEpoch, startingUserId, options.getUserId)) return { kind: "session_stale" };
      return confirmed ? { kind: "uncertain_reconciled" } : { kind: "uncertain_unconfirmed", reconciliationError };
    }
    return { kind: "failed", error };
  } finally {
    preparationLocks.delete(lockKey);
  }
}

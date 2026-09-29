import { ApiError } from "./auth";
import type { ApplicationTracking, ApplicationTrackingStatus, SessionApi } from "./api";

const preparationLocks = new Set<string>();

export type TrackingStartResult =
  | { kind: "locked" }
  | { kind: "confirmed"; value: ApplicationTracking }
  | { kind: "reconciled_existing"; workspaceRefreshConfirmed?: boolean }
  | { kind: "reconciliation_failed"; reconciliationError?: unknown; workspaceRefreshConfirmed?: boolean }
  | { kind: "uncertain_reconciled"; workspaceRefreshConfirmed?: boolean }
  | { kind: "uncertain_unconfirmed"; reconciliationError?: unknown; workspaceRefreshConfirmed?: boolean }
  | { kind: "not_found" }
  | { kind: "failed"; error: unknown }
  | { kind: "session_stale" };

export type TrackingControllerOptions = {
  userId?: string;
  getUserId?: () => string | undefined;
  reconcileByPreparation?: () => Promise<ApplicationTracking | null>;
  reconcileWorkspace?: () => Promise<boolean>;
};

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
      const reconciliation = await reconcileExactPreparation(api, preparationId, startingEpoch, startingUserId, options);
      if (reconciliation.kind === "session_stale") return reconciliation;
      return reconciliation.matching
        ? { kind: "reconciled_existing", workspaceRefreshConfirmed: reconciliation.workspaceRefreshConfirmed }
        : { kind: "reconciliation_failed", reconciliationError: reconciliation.error, workspaceRefreshConfirmed: reconciliation.workspaceRefreshConfirmed };
    }
    if (!(error instanceof ApiError)) {
      const reconciliation = await reconcileExactPreparation(api, preparationId, startingEpoch, startingUserId, options);
      if (reconciliation.kind === "session_stale") return reconciliation;
      return reconciliation.matching
        ? { kind: "uncertain_reconciled", workspaceRefreshConfirmed: reconciliation.workspaceRefreshConfirmed }
        : { kind: "uncertain_unconfirmed", reconciliationError: reconciliation.error, workspaceRefreshConfirmed: reconciliation.workspaceRefreshConfirmed };
    }
    return { kind: "failed", error };
  } finally {
    preparationLocks.delete(lockKey);
  }
}

async function reconcileExactPreparation(
  api: SessionApi,
  preparationId: string,
  startingEpoch: number,
  startingUserId: string | undefined,
  options: TrackingControllerOptions,
): Promise<{ kind: "matched"; matching: true; workspaceRefreshConfirmed?: boolean } | { kind: "unmatched"; matching: false; error?: unknown; workspaceRefreshConfirmed?: boolean } | { kind: "session_stale" }> {
  let matching: ApplicationTracking | null = null;
  let error: unknown;
  try {
    matching = options.reconcileByPreparation ? await options.reconcileByPreparation() : null;
    if (matching && matching.preparation_id !== preparationId) {
      error = new Error("Tracking reconciliation response did not match the preparation.");
      matching = null;
    }
  } catch (reason) {
    error = reason;
  }
  if (!currentSession(api, startingEpoch, startingUserId, options.getUserId)) return { kind: "session_stale" };
  let workspaceRefreshConfirmed: boolean | undefined;
  if (options.reconcileWorkspace) {
    try { workspaceRefreshConfirmed = await options.reconcileWorkspace(); } catch (reason) { workspaceRefreshConfirmed = false; error ??= reason; }
    if (!currentSession(api, startingEpoch, startingUserId, options.getUserId)) return { kind: "session_stale" };
  }
  return matching ? { kind: "matched", matching: true, workspaceRefreshConfirmed } : { kind: "unmatched", matching: false, error, workspaceRefreshConfirmed };
}

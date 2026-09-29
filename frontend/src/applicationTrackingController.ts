import type { ApplicationTracking, ApplicationTrackingStatus, SessionApi } from "./api";

const preparationLocks = new Set<string>();

export type TrackingCreateResult =
  | { kind: "locked" }
  | { kind: "created"; value: ApplicationTracking }
  | { kind: "error"; error: unknown };

/** Shared provider-free tracking-start mutation with a synchronous per-preparation lock. */
export async function createApplicationTracking(
  api: SessionApi,
  preparationId: string,
  status: ApplicationTrackingStatus,
): Promise<TrackingCreateResult> {
  if (preparationLocks.has(preparationId)) return { kind: "locked" };
  preparationLocks.add(preparationId);
  try {
    return {
      kind: "created",
      value: await api.request<ApplicationTracking>("/api/v1/application-tracking", {
        method: "POST",
        body: JSON.stringify({ preparation_id: preparationId, status }),
      }),
    };
  } catch (error) {
    return { kind: "error", error };
  } finally {
    preparationLocks.delete(preparationId);
  }
}

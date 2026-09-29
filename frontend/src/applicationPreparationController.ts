import type { ApplicationPreparation, ApplicationPrepareRequest, SessionApi } from "./api";

const preparationLocks = new Set<string>();

export type PreparationCreateResult =
  | { kind: "locked" }
  | { kind: "created"; value: ApplicationPreparation }
  | { kind: "error"; error: unknown };

/** Shared preparation mutation boundary used by job-entry preparation surfaces. */
export async function createApplicationPreparation(
  api: SessionApi,
  payload: ApplicationPrepareRequest,
): Promise<PreparationCreateResult> {
  const lockKey = payload.target.discovered_job_id ?? "external";
  if (preparationLocks.has(lockKey)) return { kind: "locked" };
  preparationLocks.add(lockKey);
  try {
    return {
      kind: "created",
      value: await api.request<ApplicationPreparation>("/api/v1/applications/prepare", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    };
  } catch (error) {
    return { kind: "error", error };
  } finally {
    preparationLocks.delete(lockKey);
  }
}

import { ApiError } from "./auth";
import type { ApplicationPreparation, ApplicationPrepareRequest, OnboardingStatus, Profile, SessionApi } from "./api";

const preparationLocks = new Set<string>();

export type PreparationPrerequisites = {
  state: "ready" | "candidate_not_ready" | "profile_missing" | "unavailable";
  onboarding?: OnboardingStatus;
  profile?: Profile;
};

export type PreparationCreateResult =
  | { kind: "locked" }
  | { kind: "confirmed"; value: ApplicationPreparation }
  | { kind: "target_unavailable"; error: ApiError }
  | { kind: "prerequisite_conflict"; prerequisites: PreparationPrerequisites }
  | { kind: "uncertain"; error: unknown }
  | { kind: "failed"; error: unknown }
  | { kind: "session_stale" };

export type PreparationControllerOptions = { userId?: string; getUserId?: () => string | undefined };

function currentSession(api: SessionApi, startingEpoch: number, startingUserId: string | undefined, getUserId?: () => string | undefined): boolean {
  return api.sessionEpoch() === startingEpoch && (!getUserId || getUserId() === startingUserId);
}

/** Shared readiness authority used by preparation surfaces and 409 reconciliation. */
export async function readPreparationPrerequisites(api: SessionApi, options: PreparationControllerOptions = {}): Promise<PreparationPrerequisites | { state: "session_stale" }> {
  const startingEpoch = api.sessionEpoch();
  const startingUserId = options.userId ?? options.getUserId?.();
  const [onboardingResult, profileResult] = await Promise.allSettled([
    api.request<OnboardingStatus>("/api/v1/onboarding/status"),
    api.request<Profile>("/api/v1/profile"),
  ]);
  if (!currentSession(api, startingEpoch, startingUserId, options.getUserId)) return { state: "session_stale" };
  if (onboardingResult.status !== "fulfilled") return { state: "unavailable" };
  if (!onboardingResult.value.candidate_context_ready) return { state: "candidate_not_ready", onboarding: onboardingResult.value };
  if (profileResult.status !== "fulfilled") return profileResult.reason instanceof ApiError && profileResult.reason.status === 404 ? { state: "profile_missing", onboarding: onboardingResult.value } : { state: "unavailable", onboarding: onboardingResult.value };
  return profileResult.value.display_name?.trim() ? { state: "ready", onboarding: onboardingResult.value, profile: profileResult.value } : { state: "profile_missing", onboarding: onboardingResult.value, profile: profileResult.value };
}

/** Shared preparation mutation and outcome classification for Recommended and Workspace. */
export async function createApplicationPreparation(api: SessionApi, payload: ApplicationPrepareRequest, options: PreparationControllerOptions = {}): Promise<PreparationCreateResult> {
  const startingEpoch = api.sessionEpoch();
  const startingUserId = options.userId ?? options.getUserId?.();
  const lockKey = `${startingUserId ?? "anonymous"}:${payload.target.discovered_job_id ?? "external"}`;
  if (preparationLocks.has(lockKey)) return { kind: "locked" };
  preparationLocks.add(lockKey);
  try {
    const value = await api.request<ApplicationPreparation>("/api/v1/applications/prepare", { method: "POST", body: JSON.stringify(payload) });
    if (!currentSession(api, startingEpoch, startingUserId, options.getUserId)) return { kind: "session_stale" };
    return { kind: "confirmed", value };
  } catch (error) {
    if (!currentSession(api, startingEpoch, startingUserId, options.getUserId) || (error as Error)?.name === "AbortError") return { kind: "session_stale" };
    if (error instanceof ApiError && error.status === 404 && payload.target.discovered_job_id) return { kind: "target_unavailable", error };
    if (error instanceof ApiError && error.status === 409) {
      const prerequisites = await readPreparationPrerequisites(api, { userId: startingUserId, getUserId: options.getUserId });
      if (prerequisites.state === "session_stale") return { kind: "session_stale" };
      return { kind: "prerequisite_conflict", prerequisites };
    }
    if (!(error instanceof ApiError)) return { kind: "uncertain", error };
    return { kind: "failed", error };
  } finally {
    preparationLocks.delete(lockKey);
  }
}

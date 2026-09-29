import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, useAuth } from "./auth";
import type { UserJobDecision, UserJobDecisionValue } from "./api";

export type DecisionMutationResult = {
  kind: "confirmed" | "reconciled" | "uncertain" | "session_stale" | "duplicate_blocked";
  decision: UserJobDecision | null;
  confirmed: boolean;
  mutated: boolean;
  uncertain: boolean;
};

export const undecidedDecision = (discovered_job_id: string): UserJobDecision => ({ discovered_job_id, decision: "undecided", revision: null, created_at: null, updated_at: null });

export function useJobDecisionMutator() {
  const { api, user } = useAuth();
  const locks = useRef(new Map<string, symbol>());
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [notices, setNotices] = useState<Record<string, string>>({});
  const sessionEpoch = api.sessionEpoch();

  const sessionIsCurrent = useCallback((userId: string | null, epoch: number) => user?.id === userId && api.sessionEpoch() === epoch, [api, user?.id]);

  useEffect(() => {
    locks.current.clear();
    setPending(new Set());
    setNotices({});
  }, [user?.id, sessionEpoch]);

  const mutate = useCallback(async (current: UserJobDecision, target: UserJobDecisionValue): Promise<DecisionMutationResult> => {
    const jobId = current.discovered_job_id;
    if (locks.current.has(jobId)) return { kind: "duplicate_blocked", decision: current, confirmed: false, mutated: false, uncertain: false };
    const lockToken = Symbol(jobId);
    locks.current.set(jobId, lockToken);
    const startingUserId = user?.id ?? null;
    const startingEpoch = api.sessionEpoch();
    setPending((previous) => new Set(previous).add(jobId));
    setNotices((previous) => { const next = { ...previous }; delete next[jobId]; return next; });
    try {
      const updated = await api.request<UserJobDecision>(`/api/v1/jobs/decisions/${encodeURIComponent(jobId)}`, {
        method: "PUT",
        body: JSON.stringify({ decision: target, expected_revision: current.revision }),
      });
      if (!sessionIsCurrent(startingUserId, startingEpoch)) return { kind: "session_stale", decision: null, confirmed: false, mutated: false, uncertain: true };
      return { kind: "confirmed", decision: updated, confirmed: true, mutated: true, uncertain: false };
    } catch (error) {
      if (!sessionIsCurrent(startingUserId, startingEpoch) || (error as Error)?.name === "AbortError") return { kind: "session_stale", decision: null, confirmed: false, mutated: false, uncertain: true };
      let refreshed: UserJobDecision;
      try {
        refreshed = await api.request<UserJobDecision>(`/api/v1/jobs/decisions/${encodeURIComponent(jobId)}`);
      } catch {
        if (!sessionIsCurrent(startingUserId, startingEpoch)) return { kind: "session_stale", decision: null, confirmed: false, mutated: false, uncertain: true };
        setNotices((previous) => ({ ...previous, [jobId]: "This decision could not be confirmed. Try again before continuing." }));
        return { kind: "uncertain", decision: null, confirmed: false, mutated: false, uncertain: true };
      }
      if (!sessionIsCurrent(startingUserId, startingEpoch)) return { kind: "session_stale", decision: null, confirmed: false, mutated: false, uncertain: true };
      setNotices((previous) => ({
        ...previous,
        [jobId]: error instanceof ApiError && error.status === 409
          ? "This decision changed elsewhere. The current decision is shown; choose an action again."
          : "The update was interrupted. The current decision is confirmed below; choose an action again if needed.",
      }));
      return { kind: "reconciled", decision: refreshed, confirmed: true, mutated: false, uncertain: false };
    } finally {
      if (locks.current.get(jobId) === lockToken) locks.current.delete(jobId);
      setPending((previous) => { const next = new Set(previous); next.delete(jobId); return next; });
    }
  }, [api, sessionIsCurrent, user?.id]);

  const clearNotice = useCallback((jobId: string) => setNotices((previous) => { const next = { ...previous }; delete next[jobId]; return next; }), []);
  return { mutate, pending, notices, clearNotice };
}

export function DecisionControls({ decision: input, onMutate, disabled = false, context }: { decision?: UserJobDecision; onMutate: (target: UserJobDecisionValue) => void; disabled?: boolean; context?: string }) {
  const decision = input ?? undecidedDecision("");
  const busy = disabled;
  const actionLabel = (label: string) => context ? `${label} ${context}` : label;
  return <div className="card-actions" aria-label="Your decision">
    <strong>Your decision:</strong>
    {decision.decision === "undecided" && <><button type="button" className="button-secondary" aria-label={actionLabel("Shortlist")} disabled={busy} onClick={() => onMutate("shortlisted")}>Shortlist</button><button type="button" className="button-secondary" aria-label={actionLabel("Dismiss")} disabled={busy} onClick={() => onMutate("dismissed")}>Dismiss</button></>}
    {decision.decision === "shortlisted" && <><span aria-label="Shortlisted">Shortlisted</span><button type="button" className="button-secondary" aria-label={actionLabel("Remove from shortlist")} disabled={busy} onClick={() => onMutate("undecided")}>Remove from shortlist</button><button type="button" className="button-secondary" aria-label={actionLabel("Dismiss")} disabled={busy} onClick={() => onMutate("dismissed")}>Dismiss</button></>}
    {decision.decision === "dismissed" && <><span aria-label="Dismissed">Dismissed</span><button type="button" className="button-secondary" aria-label={actionLabel("Undo dismissal")} disabled={busy} onClick={() => onMutate("undecided")}>Undo dismissal</button><button type="button" className="button-secondary" aria-label={actionLabel("Shortlist")} disabled={busy} onClick={() => onMutate("shortlisted")}>Shortlist</button></>}
  </div>;
}

import { useCallback, useEffect, useState } from "react";
import { ApiError, useAuth } from "./auth";
import type { UserJobDecision, UserJobDecisionValue } from "./api";

export type DecisionMutationResult = {
  decision: UserJobDecision | null;
  confirmed: boolean;
  mutated: boolean;
  uncertain: boolean;
};

export const undecidedDecision = (discovered_job_id: string): UserJobDecision => ({ discovered_job_id, decision: "undecided", revision: null, created_at: null, updated_at: null });

export function useJobDecisionMutator() {
  const { api, user } = useAuth();
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [notices, setNotices] = useState<Record<string, string>>({});

  useEffect(() => {
    setPending(new Set());
    setNotices({});
  }, [user?.id]);

  const mutate = useCallback(async (current: UserJobDecision, target: UserJobDecisionValue): Promise<DecisionMutationResult> => {
    const jobId = current.discovered_job_id;
    if (pending.has(jobId)) return { decision: current, confirmed: false, mutated: false, uncertain: false };
    setPending((previous) => new Set(previous).add(jobId));
    setNotices((previous) => { const next = { ...previous }; delete next[jobId]; return next; });
    try {
      const updated = await api.request<UserJobDecision>(`/api/v1/jobs/decisions/${encodeURIComponent(jobId)}`, {
        method: "PUT",
        body: JSON.stringify({ decision: target, expected_revision: current.revision }),
      });
      return { decision: updated, confirmed: true, mutated: true, uncertain: false };
    } catch (error) {
      let refreshed: UserJobDecision;
      try {
        refreshed = await api.request<UserJobDecision>(`/api/v1/jobs/decisions/${encodeURIComponent(jobId)}`);
      } catch {
        setNotices((previous) => ({ ...previous, [jobId]: "This decision could not be confirmed. Try again before continuing." }));
        return { decision: null, confirmed: false, mutated: false, uncertain: true };
      }
      setNotices((previous) => ({
        ...previous,
        [jobId]: error instanceof ApiError && error.status === 409
          ? "This decision changed elsewhere. The current decision is shown; choose an action again."
          : "The update was interrupted. The current decision is confirmed below; choose an action again if needed.",
      }));
      return { decision: refreshed, confirmed: true, mutated: false, uncertain: false };
    } finally {
      setPending((previous) => { const next = new Set(previous); next.delete(jobId); return next; });
    }
  }, [api, pending]);

  const clearNotice = useCallback((jobId: string) => setNotices((previous) => { const next = { ...previous }; delete next[jobId]; return next; }), []);
  return { mutate, pending, notices, clearNotice };
}

export function DecisionControls({ decision: input, onMutate, disabled = false }: { decision?: UserJobDecision; onMutate: (target: UserJobDecisionValue) => void; disabled?: boolean }) {
  const decision = input ?? undecidedDecision("");
  const busy = disabled;
  return <div className="card-actions" aria-label="Your decision">
    <strong>Your decision:</strong>
    {decision.decision === "undecided" && <><button type="button" className="button-secondary" disabled={busy} onClick={() => onMutate("shortlisted")}>Shortlist</button><button type="button" className="button-secondary" disabled={busy} onClick={() => onMutate("dismissed")}>Dismiss</button></>}
    {decision.decision === "shortlisted" && <><span aria-label="Shortlisted">Shortlisted</span><button type="button" className="button-secondary" disabled={busy} onClick={() => onMutate("undecided")}>Remove from shortlist</button><button type="button" className="button-secondary" disabled={busy} onClick={() => onMutate("dismissed")}>Dismiss</button></>}
    {decision.decision === "dismissed" && <><span aria-label="Dismissed">Dismissed</span><button type="button" className="button-secondary" disabled={busy} onClick={() => onMutate("undecided")}>Undo dismissal</button><button type="button" className="button-secondary" disabled={busy} onClick={() => onMutate("shortlisted")}>Shortlist</button></>}
  </div>;
}

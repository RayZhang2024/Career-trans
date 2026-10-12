import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { CVWritingDraft, TailoringPreflight, TailoringSession, TailoringStrategyChoice } from "./api";
import { useAuth } from "./auth";

function newRequestId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
      const random = Math.random() * 16 | 0;
      return (char === "x" ? random : (random & 0x3 | 0x8)).toString(16);
    });
}

function titleFor(session: TailoringSession) {
  return `${session.target.title}${session.target.company ? ` · ${session.target.company}` : ""}`;
}

function actionLabel(action: string) {
  return ({ feature: "Feature", reorder: "Reorder", condense: "Condense", omit: "Omit" } as Record<string, string>)[action] ?? action;
}

function choiceLabel(decision: string) {
  return ({ keep: "Keep and feature", reorder: "Reorder", deemphasise: "Condense", skip: "Omit" } as Record<string, string>)[decision] ?? decision;
}

function activeSession(session: TailoringSession | undefined) {
  return !!session && !["approved", "stale", "strategy_failed", "strategy_invalid", "draft_failed", "draft_invalid", "strategy_rejected"].includes(session.status);
}

export function ApplicationCVTailoring({ discoveredJobId, actionable, onPreparationApproved }: { discoveredJobId: string; actionable: boolean; onPreparationApproved?: () => void }) {
  const { api, user } = useAuth();
  const [preflight, setPreflight] = useState<TailoringPreflight | null>(null);
  const [session, setSession] = useState<TailoringSession | null>(null);
  const [pages, setPages] = useState<1 | 2 | 3>(2);
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uncertainRequestId, setUncertainRequestId] = useState<string | null>(null);
  const generation = useRef(0);
  const sessionIdRef = useRef<string | undefined>(undefined);
  const uncertainRequestIdRef = useRef<string | null>(null);
  sessionIdRef.current = session?.id;
  uncertainRequestIdRef.current = uncertainRequestId;
  const basePath = "/api/v1/applications/tailoring";
  const preflightPath = `${basePath}/preflight?discovered_job_id=${encodeURIComponent(discoveredJobId)}`;

  const refresh = useCallback(async (preferredId?: string) => {
    const current = ++generation.current;
    const owner = user?.id;
    const epoch = api.sessionEpoch();
    setLoading(true);
    try {
      const value = await api.request<TailoringPreflight>(preflightPath);
      if (generation.current !== current || owner !== user?.id || api.sessionEpoch() !== epoch) return false;
      setPreflight(value);
      const next = value.sessions.find((item) => item.id === preferredId)
        ?? value.sessions.find((item) => item.id === sessionIdRef.current)
        ?? value.sessions[0]
        ?? null;
      setSession(next);
      if (next?.client_request_id === uncertainRequestIdRef.current) setUncertainRequestId(null);
      setError("");
      return true;
    } catch {
      if (generation.current === current && owner === user?.id && api.sessionEpoch() === epoch) {
        setError("Tailored CV status could not be loaded. Existing application preparations remain available below.");
      }
      return false;
    } finally {
      if (generation.current === current && owner === user?.id && api.sessionEpoch() === epoch) setLoading(false);
    }
  }, [api, preflightPath, user?.id]);

  useEffect(() => {
    setPreflight(null); setSession(null); setError(""); setNotice(""); setUncertainRequestId(null);
    void refresh();
    return () => { generation.current += 1; };
  }, [refresh]);

  const acceptResponse = (value: TailoringSession, owner: string | undefined, epoch: number, current: number) => {
    if (generation.current !== current || owner !== user?.id || api.sessionEpoch() !== epoch) return false;
    setSession(value);
    setUncertainRequestId(null);
    return true;
  };

  const reconcile = async (requestId: string) => {
    const owner = user?.id;
    const epoch = api.sessionEpoch();
    const current = ++generation.current;
    try {
      const value = await api.request<TailoringSession>(`${basePath}/by-request/${encodeURIComponent(requestId)}`);
      if (acceptResponse(value, owner, epoch, current)) setNotice("The saved tailoring request has been reconciled. No model request was repeated.");
    } catch {
      if (owner === user?.id && api.sessionEpoch() === epoch) {
        setUncertainRequestId(requestId);
        setError("The tailoring request may have been saved, but its outcome is still uncertain. Refresh status before starting another request; no model request will be replayed automatically.");
      }
    }
  };

  const start = async () => {
    if (pending || loading || !preflight || preflight.state !== "ready" || !actionable || uncertainRequestId || activeSession(session ?? undefined)) return;
    const requestId = newRequestId();
    const owner = user?.id;
    const epoch = api.sessionEpoch();
    const current = ++generation.current;
    setPending(true); setError(""); setNotice(""); setUncertainRequestId(requestId);
    try {
      const value = await api.request<TailoringSession>(basePath, {
        method: "POST", body: JSON.stringify({ client_request_id: requestId, discovered_job_id: discoveredJobId, target_pages: pages }),
      });
      if (acceptResponse(value, owner, epoch, current)) setNotice("Strategy ready for your review. Confirm its evidence-backed choices before drafting the CV.");
    } catch {
      if (owner === user?.id && api.sessionEpoch() === epoch) {
        setError("The strategy request response was interrupted. Checking its saved status without repeating the model request.");
        await reconcile(requestId);
      }
    } finally {
      if (owner === user?.id && api.sessionEpoch() === epoch) setPending(false);
    }
  };

  const mutate = async (path: string, method: "POST" | "PATCH", body: unknown, success: (value: TailoringSession) => string) => {
    if (!session || pending) return;
    const owner = user?.id; const epoch = api.sessionEpoch(); const current = ++generation.current;
    setPending(true); setError(""); setNotice("");
    try {
      const value = await api.request<TailoringSession>(`${basePath}/${encodeURIComponent(session.id)}${path}`, { method, body: JSON.stringify(body) });
      if (acceptResponse(value, owner, epoch, current)) setNotice(success(value));
    } catch (caught) {
      if (owner === user?.id && api.sessionEpoch() === epoch) {
        const status = caught instanceof Error && "status" in caught ? (caught as { status?: number }).status : undefined;
        if (status === 422 || status === 400) {
          setError("This edit was rejected because it contains unsupported facts, citations or wording. Your unsaved text is still here; revise it and try again.");
        } else if (status === 409) {
          setError("This tailoring review changed in another tab. The latest saved revision is being loaded.");
          await refresh(session.id);
        } else {
          setError("The change could not be confirmed. Refreshing the saved tailoring status; no model request will be replayed.");
          await refresh(session.id);
        }
      }
    } finally {
      if (owner === user?.id && api.sessionEpoch() === epoch) setPending(false);
    }
  };

  const refreshStatus = async () => {
    if (uncertainRequestId) { await reconcile(uncertainRequestId); return; }
    await refresh(session?.id);
  };

  const saveStrategy = (accepted: boolean | null) => {
    if (!session?.strategy) return;
    const choices: TailoringStrategyChoice[] = session.strategy.recommendations.map((item, index) => {
      const found = session.strategy_choices.find((choice) => choice.recommendation_id === item.recommendation_id);
      return found ?? { recommendation_id: item.recommendation_id, decision: item.action === "omit" ? "skip" : item.action === "reorder" ? "reorder" : item.action === "condense" ? "deemphasise" : "keep", order: index };
    });
    void mutate("/strategy", "POST", {
      expected_revision: session.strategy_revision, accepted,
      positioning_override: session.strategy_positioning_override,
      choices,
    }, () => accepted === true ? "Strategy accepted. You can now request one tailored CV draft." : "Strategy review saved.");
  };

  const generateDraft = () => {
    if (!session) return;
    void mutate("/draft", "POST", {
      client_request_id: newRequestId(), expected_strategy_revision: session.strategy_revision,
    }, () => "Draft ready. Review the wording and evidence before approving it.");
  };

  const saveDraft = (draft: CVWritingDraft) => {
    void mutate("/draft", "PATCH", { expected_revision: session?.draft_revision, draft }, () => "Draft edits saved as a new review revision.");
  };

  const approve = () => {
    if (!session) return;
    void mutate("/approve", "POST", {
      expected_strategy_revision: session.strategy_revision, expected_draft_revision: session.draft_revision,
    }, (value) => {
      if (value.approved_preparation_id) onPreparationApproved?.();
      return value.approved_preparation_id ? "Approved preparation saved. Open it to download your CV." : "Preparation approved.";
    });
  };

  const updateChoice = (recommendationId: string, decision: TailoringStrategyChoice["decision"]) => {
    if (!session) return;
    const existing = session.strategy_choices;
    const recommendation = session.strategy?.recommendations.find((item) => item.recommendation_id === recommendationId);
    const choices = (session.strategy?.recommendations ?? []).map((item, index) => ({
      recommendation_id: item.recommendation_id,
      decision: item.recommendation_id === recommendationId ? decision : existing.find((choice) => choice.recommendation_id === item.recommendation_id)?.decision
        ?? (item.action === "omit" ? "skip" : item.action === "reorder" ? "reorder" : item.action === "condense" ? "deemphasise" : "keep"),
      order: existing.find((choice) => choice.recommendation_id === item.recommendation_id)?.order ?? index,
    }));
    if (recommendation && session.strategy_revision > 0) {
      setSession({ ...session, strategy_choices: choices as TailoringStrategyChoice[] });
    }
  };

  const updateOrder = (recommendationId: string, order: number) => {
    if (!session?.strategy) return;
    const choices = session.strategy.recommendations.map((item, index) => {
      const existing = session.strategy_choices.find((choice) => choice.recommendation_id === item.recommendation_id);
      return existing ?? {
        recommendation_id: item.recommendation_id,
        decision: item.action === "omit" ? "skip" : item.action === "reorder" ? "reorder" : item.action === "condense" ? "deemphasise" : "keep",
        order: index,
      };
    });
    const from = choices.findIndex((choice) => choice.recommendation_id === recommendationId);
    if (from < 0) return;
    const [moved] = choices.splice(from, 1);
    choices.splice(Math.max(0, Math.min(order, choices.length)), 0, moved);
    setSession({ ...session, strategy_choices: choices.map((choice, index) => ({ ...choice, order: index })) as TailoringStrategyChoice[] });
  };

  const updateDraft = (update: (draft: CVWritingDraft) => CVWritingDraft) => {
    if (session?.draft) setSession({ ...session, draft: update(session.draft) });
  };

  return <section className="card application-section cv-tailoring" aria-labelledby="workspace-tailoring-heading" aria-busy={pending}>
    <div className="section-heading"><div><p className="eyebrow">Profile-first, review before approval</p><h3 id="workspace-tailoring-heading">Prepare a tailored CV</h3></div><button type="button" className="button-secondary" onClick={() => void refreshStatus()} disabled={loading || pending}>Refresh status</button></div>
    <p>Uses your confirmed Profile and current job evaluation. Selecting an original CV or completing CV visibility assessment is optional.</p>
    {loading && <p role="status">Checking current Profile and job readiness…</p>}
    {pending && <p role="status">Saving your review or making the explicitly requested tailoring request…</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!loading && preflight && <>
      <p className="muted">When you request a strategy or draft, your confirmed Profile evidence and this job’s details are sent to the configured AI service. It may charge you. Each action makes at most one request, with no automatic retries.</p>
      <details><summary>Technical request limits</summary><p className="muted">Configured service: {preflight.provider} · {preflight.model}. Up to {preflight.max_requirements} requirements and {preflight.max_input_characters.toLocaleString()} input characters; {preflight.strategy_output_token_limit.toLocaleString()} strategy output tokens or {preflight.draft_output_token_limit.toLocaleString()} draft output tokens; {preflight.timeout_seconds}-second timeout.</p></details>
      {preflight.state === "candidate_not_ready" && <p role="status">A complete confirmed Profile and evidence set is required. <Link to="/profile">Review your Profile</Link>.</p>}
      {preflight.state === "identity_required" && <p role="status">Add your name and email to the canonical Profile before preparing a CV. <Link to="/profile">Update your Profile</Link>.</p>}
      {preflight.state === "current_evaluation_required" && <p role="status">Run a current job evaluation before tailoring a CV from these requirements. <Link to={`/jobs/${encodeURIComponent(discoveredJobId)}/fit`}>Open this job’s Fit</Link>.</p>}
      {preflight.state === "input_too_large" && <p role="status">This job's current requirements exceed the bounded tailoring limit. No provider request was made.</p>}
      {preflight.state === "job_not_actionable" && <p role="status">This vacancy is no longer actionable. You can still open previous preparations below.</p>}
      {preflight.state === "ready" && !activeSession(session ?? undefined) && session?.status !== "stale" && actionable && <div className="cv-tailoring-launch">
        <label htmlFor="tailoring-target-pages">Target CV pages</label><select id="tailoring-target-pages" value={pages} onChange={(event) => setPages(Number(event.target.value) as 1 | 2 | 3)} disabled={pending}><option value={1}>1 page</option><option value={2}>2 pages</option><option value={3}>3 pages</option></select>
        <button type="button" disabled={pending || !!uncertainRequestId} onClick={() => void start()}>{pending ? "Preparing strategy…" : "Prepare tailored CV"}</button>
      </div>}
      {uncertainRequestId && <p role="status">Request outcome uncertain. Reconcile saved status before starting another request; request ID is retained and no model call will be replayed.</p>}
      {session && <div className="cv-tailoring-session" aria-labelledby="tailoring-session-heading">
        <h4 id="tailoring-session-heading">{titleFor(session)}</h4><p>Status: {session.status.replaceAll("_", " ")}{session.is_current ? " · current inputs" : " · inputs changed"}</p>
        {!session.is_current && <p role="alert">This review is stale because its Profile, job evaluation or AI configuration changed. Start again from current inputs.</p>}
        {session.status === "strategy_in_progress" && <p role="status">The strategy request is still recorded as in progress. Refresh status later; it will not be sent again automatically.</p>}
        {session.status === "strategy_uncertain" && <p role="status">The strategy response was interrupted. Its saved outcome is uncertain; refresh status to reconcile without making another provider request.</p>}
        {session.status === "strategy_failed" && <p role="alert">The strategy request failed before a result was saved. You may start a new explicit request.</p>}
        {session.status === "strategy_invalid" && <p role="alert">The strategy response did not meet the evidence contract. No CV draft was created. Start a new explicit request after reviewing your Profile.</p>}
        {session.status === "strategy_rejected" && <p role="status">Strategy rejected. No CV draft was generated. You can prepare a new strategy from the current job.</p>}
        {session.status === "strategy_review" && session.strategy && <>
          <h5>Top experience to highlight</h5><p><strong>Positioning:</strong> {session.strategy.positioning}</p><p><strong>Summary emphasis:</strong> {session.strategy.summary_emphasis}</p>
          <label htmlFor="tailoring-positioning">Your positioning adjustment (optional)</label><textarea id="tailoring-positioning" value={session.strategy_positioning_override ?? ""} onChange={(event) => setSession({ ...session, strategy_positioning_override: event.target.value || null })} disabled={pending} />
          <ul className="tailoring-recommendations">{[...session.strategy.recommendations].sort((left, right) => {
            const leftOrder = session.strategy_choices.find((choice) => choice.recommendation_id === left.recommendation_id)?.order ?? session.strategy.recommendations.indexOf(left);
            const rightOrder = session.strategy_choices.find((choice) => choice.recommendation_id === right.recommendation_id)?.order ?? session.strategy.recommendations.indexOf(right);
            return leftOrder - rightOrder;
          }).map((item) => {
            const match = session.target.requirement_matches.find((value) => value.requirement_index === item.requirement_index);
            const evidence = match?.evidence_refs?.filter((ref) => item.source_refs.some((selected) => selected.source_type === ref.source_type && selected.source_ref === ref.source_ref)) ?? [];
            const order = session.strategy_choices.find((choice) => choice.recommendation_id === item.recommendation_id)?.order ?? session.strategy.recommendations.indexOf(item);
            return <li key={item.recommendation_id}>
            <strong>{actionLabel(item.action)}</strong><p>{item.rationale}</p>{item.suggested_emphasis && <p>Suggested emphasis: {item.suggested_emphasis}</p>}
            <details><summary>Why this?</summary><p><strong>Job requirement:</strong> {match?.requirement.text ?? "Current job requirement"}</p><p><strong>Current match:</strong> {match?.match_type.replaceAll("_", " ") ?? "Not available"}</p><p><strong>Confirmed Profile evidence:</strong> {evidence.map((ref) => ref.value ?? "Confirmed Profile source").join(" · ") || "No relevant confirmed evidence"}</p></details>
            <label htmlFor={`tailoring-order-${item.recommendation_id}`}>Order in draft for requirement {item.requirement_index + 1}</label><select id={`tailoring-order-${item.recommendation_id}`} value={order} onChange={(event) => updateOrder(item.recommendation_id, Number(event.target.value))} disabled={pending}>
              {session.strategy.recommendations.map((_, index) => <option key={index} value={index}>{index + 1}</option>)}
            </select>
            <label htmlFor={`tailoring-choice-${item.recommendation_id}`}>Your choice</label><select id={`tailoring-choice-${item.recommendation_id}`} value={session.strategy_choices.find((choice) => choice.recommendation_id === item.recommendation_id)?.decision ?? (item.action === "omit" ? "skip" : item.action === "reorder" ? "reorder" : item.action === "condense" ? "deemphasise" : "keep")} onChange={(event) => updateChoice(item.recommendation_id, event.target.value as TailoringStrategyChoice["decision"])} disabled={pending}>
              {["keep", "reorder", "deemphasise", "skip"].map((value) => <option key={value} value={value}>{choiceLabel(value)}</option>)}
            </select>
          </li>; })}</ul>
          {session.strategy.gaps_to_clarify.length > 0 && <div><h5>Evidence gaps to keep clear</h5><ul>{session.strategy.gaps_to_clarify.map((gap) => <li key={gap.requirement_index}>{gap.reason}</li>)}</ul></div>}
          {session.strategy.not_to_claim.length > 0 && <div><h5>Do not claim</h5><ul>{session.strategy.not_to_claim.map((item) => <li key={item}>{item}</li>)}</ul></div>}
          <div className="card-actions"><button type="button" className="button-secondary" disabled={pending} onClick={() => saveStrategy(null)}>{pending ? "Saving review…" : "Save strategy review"}</button><button type="button" className="button-secondary" disabled={pending} onClick={() => saveStrategy(false)}>{pending ? "Saving…" : "Reject strategy"}</button><button type="button" disabled={pending || !session.is_current} onClick={() => saveStrategy(true)}>{pending ? "Saving…" : "Accept strategy and continue"}</button></div>
        </>}
        {session.status === "strategy_accepted" && <><p>Strategy accepted at revision {session.strategy_revision}. Draft generation uses one separate explicit request.</p><button type="button" disabled={pending || !session.is_current} onClick={generateDraft}>{pending ? "Drafting CV…" : "Draft tailored CV"}</button></>}
        {(session.status === "draft_in_progress" || session.status === "draft_uncertain") && <p role="status">Draft generation is recorded as {session.status.replaceAll("_", " ")}. Refresh saved status; a paid request is never replayed automatically.</p>}
        {(session.status === "draft_failed" || session.status === "draft_invalid") && <p role="alert">The draft was not saved as a reviewable CV. The strategy remains available; start a new tailoring request to try again.</p>}
        {session.status === "draft_review" && session.draft && <>
          <p>Review every statement and its Profile evidence before saving or approving.</p>
          <label htmlFor="tailoring-summary">Professional summary</label><textarea id="tailoring-summary" value={session.draft.professional_summary} onChange={(event) => updateDraft((draft) => ({ ...draft, professional_summary: event.target.value }))} disabled={pending} />
          <p className="muted">Summary sources: {session.draft.summary_source_refs.map((ref) => ref.value ?? "Confirmed Profile evidence").join(" · ") || "None"}</p>
          <label htmlFor="tailoring-skills">Key skills (one per line)</label><textarea id="tailoring-skills" value={session.draft.key_skills.join("\n")} onChange={(event) => updateDraft((draft) => ({ ...draft, key_skills: event.target.value.split(/\r?\n/).filter(Boolean) }))} disabled={pending} />
          {session.draft.role_drafts.map((role, roleIndex) => { const outline = session.profile_outline.employment.find((item) => item.employment_index === role.employment_index); return <fieldset key={role.employment_index}><legend>{outline ? `${outline.title} — ${outline.employer}` : `Role ${role.employment_index + 1}`}</legend>{role.bullets.map((bullet, bulletIndex) => <div key={`${role.employment_index}-${bulletIndex}`}><label htmlFor={`tailoring-role-${role.employment_index}-bullet-${bulletIndex}`}>Bullet {bulletIndex + 1}</label><textarea id={`tailoring-role-${role.employment_index}-bullet-${bulletIndex}`} value={bullet.text} onChange={(event) => updateDraft((draft) => ({ ...draft, role_drafts: draft.role_drafts.map((item, index) => index !== roleIndex ? item : { ...item, bullets: item.bullets.map((value, index) => index === bulletIndex ? { ...value, text: event.target.value } : value) }) }))} disabled={pending} /><p className="muted">Evidence: {bullet.source_refs.map((ref) => ref.value ?? "Confirmed Profile evidence").join(" · ")}</p></div>)}</fieldset>; })}
          {session.draft.project_drafts.map((project, projectIndex) => <div key={project.project_index}><label htmlFor={`tailoring-project-${project.project_index}`}>Project: {session.profile_outline.projects.find((item) => item.project_index === project.project_index)?.name ?? project.project_index + 1}</label><textarea id={`tailoring-project-${project.project_index}`} value={project.text} onChange={(event) => updateDraft((draft) => ({ ...draft, project_drafts: draft.project_drafts.map((item, index) => index === projectIndex ? { ...item, text: event.target.value } : item) }))} disabled={pending} /><p className="muted">Evidence: {project.source_refs.map((ref) => ref.value ?? "Confirmed Profile evidence").join(" · ")}</p></div>)}
          {session.profile_outline.education.length > 0 && <section><h5>Education (from Profile)</h5><ul>{session.profile_outline.education.map((item) => <li key={item}>{item}</li>)}</ul></section>}
          {session.profile_outline.credentials.length > 0 && <section><h5>Credentials (from Profile)</h5><ul>{session.profile_outline.credentials.map((item) => <li key={item}>{item}</li>)}</ul></section>}
          <div className="card-actions"><button type="button" className="button-secondary" disabled={pending || !session.is_current} onClick={() => saveDraft(session.draft!)}>{pending ? "Saving…" : "Save draft edits"}</button><button type="button" disabled={pending || !session.is_current} onClick={approve}>{pending ? "Approving…" : "Approve and prepare documents"}</button></div>
        </>}
        {session.status === "approved" && session.approved_preparation_id && <p role="status">Approved preparation saved. <Link to={`/applications/${encodeURIComponent(session.approved_preparation_id)}`}>Open preparation and download CV</Link>.</p>}
        {session.status === "stale" && <><p role="alert">The saved review no longer matches current inputs. Its history is retained; it cannot be approved.</p>{preflight.state === "ready" && <button type="button" disabled={pending || !!uncertainRequestId} onClick={() => { setSession(null); void start(); }}>Start a new current review</button>}</>}
      </div>}
    </>}
  </section>;
}

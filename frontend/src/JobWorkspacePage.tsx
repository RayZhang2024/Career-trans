import { useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import type { ApplicationPreparation, ApplicationPrepareRequest, ApplicationTracking, ApplicationTrackingStatus, JobWorkspace, JobWorkspaceApplication, JobWorkspaceEvaluation, UserJobDecisionValue } from "./api";
import { ApiError, useAuth } from "./auth";
import { RuntimeAttributionPanel } from "./RuntimeAttributionPanel";
import { OpportunityDetail } from "./JobsPage";
import { DecisionControls, undecidedDecision, useJobDecisionMutator } from "./jobDecisions";
import { createApplicationPreparation, readPreparationPrerequisites, type PreparationPrerequisites } from "./applicationPreparationController";
import { createApplicationTracking } from "./applicationTrackingController";

export type JobWorkspaceSection = "overview" | "fit" | "application" | "tracking";
const APPLICATION_WINDOW_STEP = 20;
const APPLICATION_WINDOW_MAX = 100;
const statuses: ApplicationTrackingStatus[] = ["prepared", "applied", "interview", "rejected", "offer", "withdrawn"];
const statusLabel = (value: ApplicationTrackingStatus) => value[0].toUpperCase() + value.slice(1);
const dateLabel = (value: string) => new Date(/(?:Z|[+-]\d\d:\d\d)$/i.test(value) ? value : `${value}Z`).toLocaleString();
const applicationContextLabel = (item: JobWorkspaceApplication) => [item.target.title, item.target.company, item.target.location, `created ${dateLabel(item.created_at)}`].filter(Boolean).join(" · ");

function sectionPath(discoveredJobId: string, section: JobWorkspaceSection) {
  return section === "overview" ? `/jobs/${encodeURIComponent(discoveredJobId)}` : `/jobs/${encodeURIComponent(discoveredJobId)}/${section}`;
}

function EvaluationHistory({ evaluations }: { evaluations: JobWorkspaceEvaluation[] }) {
  if (!evaluations.length) return <p className="muted">No persisted evaluation snapshots are available for this job.</p>;
  return <section className="workspace-evaluation-history" aria-labelledby="workspace-evaluation-history-heading"><h3 id="workspace-evaluation-history-heading">Evaluation history</h3><ol className="run-list">{evaluations.map((evaluation) => <li className="card" key={evaluation.id}><div className="section-heading"><div><h4>{evaluation.applicability === "current" ? "Current evaluation" : evaluation.applicability === "historical" ? "Historical evaluation" : "Applicability unavailable"}</h4><p>{new Date(evaluation.created_at).toLocaleString()}</p></div></div>{evaluation.applicability === "unknown" && <p className="notice">Currentness could not be confirmed, so this snapshot is shown without a current or historical claim.</p>}<OpportunityDetail opportunity={evaluation.opportunity} mode={evaluation.applicability} /><RuntimeAttributionPanel attribution={evaluation.runtime_attribution} boundary="evaluation" /></li>)}</ol></section>;
}

function WorkspaceOverview({ workspace }: { workspace: JobWorkspace }) {
  const { job, provenance } = workspace;
  return <section className="card" aria-labelledby="workspace-facts-heading"><h2 id="workspace-facts-heading">Job details</h2><dl className="detail-grid"><div><dt>Title</dt><dd>{job.title}</dd></div><div><dt>Company</dt><dd>{job.company ?? "Not provided"}</dd></div><div><dt>Location</dt><dd>{job.location ?? "Not provided"}</dd></div><div><dt>Work arrangement</dt><dd>{job.work_arrangement ?? "Not provided"}</dd></div><div><dt>Employment type</dt><dd>{job.employment_type ?? "Not provided"}</dd></div><div><dt>First found</dt><dd>{dateLabel(job.first_seen_at)}</dd></div><div><dt>Posted</dt><dd>{job.posted_at ? dateLabel(job.posted_at) : "Not provided"}</dd></div></dl>{job.description && <><h3>Description</h3><p className="profile-prose">{job.description}</p></>}<div className="card-actions"><a href={job.url} target="_blank" rel="noopener noreferrer">Open vacancy</a></div><details><summary>Source and technical details</summary><dl className="detail-grid"><div><dt>Actionable</dt><dd>{job.actionable ? "Yes" : "No"}</dd></div><div><dt>Lifecycle</dt><dd>{job.state}</dd></div><div><dt>Verification</dt><dd>{job.verification_status}{job.verification_reason ? ` · ${job.verification_reason}` : ""}</dd></div><div><dt>Last seen</dt><dd>{dateLabel(job.last_seen_at)}</dd></div><div><dt>Last changed</dt><dd>{dateLabel(job.last_changed_at)}</dd></div></dl><h3>Sources</h3>{provenance.items.length ? <ol>{provenance.items.map((item) => <li key={item.id}><strong>{item.runtime}</strong>{item.discovered_via ? ` · ${item.discovered_via}` : ""} · imported {dateLabel(item.imported_at)}{item.source_ref ? <> · <span>{item.source_ref}</span></> : null}</li>)}</ol> : <p className="muted">No provenance records are available.</p>}<p className="muted">Total provenance records: {provenance.count}</p>{provenance.truncated && <p className="muted">Showing the first {provenance.limit} provenance records.</p>}</details></section>;
}

function CurrentFit({ workspace }: { workspace: JobWorkspace }) {
  const fit = workspace.current_fit;
  return <><section className="card" aria-labelledby="workspace-current-fit-heading"><h2 id="workspace-current-fit-heading">Current Fit</h2>{fit.status === "current" && fit.evaluation ? <><p><strong>Current evaluation confirmed.</strong> This matches the current job content, candidate context, and evaluation contract.</p><dl className="detail-grid"><div><dt>Evaluation</dt><dd>{fit.evaluation.id}</dd></div><div><dt>Evaluated</dt><dd>{new Date(fit.evaluation.created_at).toLocaleString()}</dd></div></dl><OpportunityDetail opportunity={fit.evaluation.opportunity} /><RuntimeAttributionPanel attribution={fit.evaluation.runtime_attribution} boundary="evaluation" /></> : fit.status === "none" ? <p>{fit.reason === "job_not_actionable" ? "This vacancy is not currently actionable, so no current Fit is claimed." : "No analysis is current for the present job, candidate and evaluation configuration. Persisted evaluation history remains below when available."}</p> : <p>Current Fit is temporarily unavailable because {fit.reason === "candidate_evidence_incomplete" ? "candidate evidence is not fully materialised" : fit.reason === "candidate_not_ready" ? "the confirmed candidate context is not ready" : "the current evaluation configuration could not be confirmed"}. Persisted history remains available below.</p>}</section><EvaluationHistory evaluations={workspace.evaluations.items} /></>;
}

function emptyApplications(): JobWorkspace["applications"] { return { items: [], limit: 20, truncated: false }; }

function ApplicationSummary({ item }: { item: JobWorkspaceApplication }) {
  const tracking = item.tracking;
  const context = applicationContextLabel(item);
  return <article className="card application-summary"><p className="eyebrow">Created {dateLabel(item.created_at)}</p><h3>{item.target.title}</h3><p>{[item.target.company, item.target.location].filter(Boolean).join(" · ") || "Company and location not recorded"}</p><dl className="metric-grid"><div><dt>Job snapshot</dt><dd>{item.snapshot_status === "current_job_content" ? "Current job content" : "Historical job content"}</dd></div>{item.result_summary && <><div><dt>CV pages</dt><dd>{item.result_summary.actual_pdf_pages} / {item.result_summary.target_pages} · {item.result_summary.layout_status === "fit" ? "Fits target" : "Over target"}</dd></div><div><dt>Cover letter</dt><dd>{item.result_summary.has_cover_letter ? "Included" : "Not included"}</dd></div><div><dt>Answers</dt><dd>{item.result_summary.answer_count}</dd></div></>}{tracking && <div><dt>Tracking</dt><dd>{statusLabel(tracking.current_status)} · Revision {tracking.revision}</dd></div>}</dl>{!tracking && <p className="muted">Not tracked</p>}<div className="card-actions"><Link aria-label={`Open preparation for ${context}`} to={`/applications/${encodeURIComponent(item.preparation_id)}`}>Open preparation</Link>{tracking && <Link aria-label={`Open tracking history for ${context} · ${statusLabel(tracking.current_status)} · updated ${dateLabel(tracking.updated_at)}`} to={`/tracking/${encodeURIComponent(tracking.id)}`}>Open tracking history</Link>}</div></article>;
}

type WorkspacePreparationReadiness = "checking" | PreparationPrerequisites["state"];

function useWorkspacePreparationPrerequisites() {
  const { api, user } = useAuth();
  const [readiness, setReadiness] = useState<WorkspacePreparationReadiness>("checking");
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    setReadiness("checking");
    if (!user?.id) return () => { generation.current += 1; };
    void readPreparationPrerequisites(api, { userId: user.id, getUserId: () => user?.id }).then((result) => {
      if (generation.current !== current || result.state === "session_stale") return;
      setReadiness(result.state);
    }).catch(() => {
      if (generation.current === current) setReadiness("unavailable");
    });
    return () => { generation.current += 1; };
  }, [api, attempt, user?.id]);
  return {
    readiness,
    retry: () => setAttempt((value) => value + 1),
    apply: (result: PreparationPrerequisites) => setReadiness(result.state),
  };
}

function WorkspaceApplications({ workspace, applications, applicationLimit, onRefresh, onShowMore, onTargetUnavailable }: { workspace: JobWorkspace; applications: JobWorkspace["applications"]; applicationLimit: number; onRefresh: () => Promise<boolean>; onShowMore: () => Promise<boolean>; onTargetUnavailable: () => Promise<boolean> }) {
  const { api, user } = useAuth();
  const preparationReadiness = useWorkspacePreparationPrerequisites();
  const { readiness } = preparationReadiness;
  const [pages, setPages] = useState<1 | 2 | 3>(2); const [includeCoverLetter, setIncludeCoverLetter] = useState(true); const [questions, setQuestions] = useState("");
  const [pending, setPending] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const [created, setCreated] = useState<ApplicationPreparation | null>(null);
  const refreshRef = useRef(onRefresh); refreshRef.current = onRefresh;
  const applyPrerequisites = (prerequisites: PreparationPrerequisites) => {
    preparationReadiness.apply(prerequisites);
    setError("");
  };
  const create = async () => {
    if (pending || readiness !== "ready" || !workspace.job.actionable) return;
    setPending(true); setError(""); setNotice(""); setCreated(null);
    const payload: ApplicationPrepareRequest = { target: { discovered_job_id: workspace.job.id }, target_pages: pages, include_cover_letter: includeCoverLetter, application_questions: questions.split(/\r?\n/).map((value) => value.trim()).filter(Boolean).slice(0, 8) };
    try {
      const result = await createApplicationPreparation(api, payload, { userId: user?.id, getUserId: () => user?.id });
      if (result.kind === "session_stale") return;
      if (result.kind === "locked") setError("A preparation request for this job is already in progress. No new preparation was created.");
      else if (result.kind === "confirmed") { setCreated(result.value); setNotice("Preparation saved. The workspace history is refreshing."); refreshRef.current(); }
      else if (result.kind === "target_unavailable") {
        const refreshed = await onTargetUnavailable();
        if (refreshed) setError("This opportunity is no longer available. The current workspace was refreshed.");
        else setError("This opportunity is no longer available. The workspace refresh could not be confirmed.");
      } else if (result.kind === "prerequisite_conflict") applyPrerequisites(result.prerequisites);
      else if (result.kind === "uncertain") { refreshRef.current(); setError("The preparation request was interrupted. The current saved history is being reconciled; no new preparation is attributed to this request."); }
      else { refreshRef.current(); setError(result.error instanceof ApiError && result.error.status === 503 ? "Application preparation is temporarily unavailable. Your saved application history remains available." : "The preparation could not be created. Saved application history remains available."); }
    } finally { setPending(false); }
  };
  return <><section className="card" aria-labelledby="workspace-application-history-heading"><div className="section-heading"><h3 id="workspace-application-history-heading">Preparation history</h3><button type="button" className="button-secondary" onClick={onRefresh}>Refresh applications</button></div>{applications.items.length === 0 && <p className="muted">No preparations have been saved for this canonical job yet.</p>}{!!applications.items.length && <div className="application-list">{applications.items.map((item) => <ApplicationSummary key={item.preparation_id} item={item} />)}</div>}{applications.truncated && applicationLimit < APPLICATION_WINDOW_MAX && <button type="button" className="button-secondary" onClick={onShowMore}>Show more</button>}{applications.truncated && applicationLimit >= APPLICATION_WINDOW_MAX && <p className="muted">Showing only the first 100 preparations.</p>}</section><section className="card application-section" aria-labelledby="workspace-create-preparation-heading"><h3 id="workspace-create-preparation-heading">Create preparation</h3>{readiness === "checking" && <p role="status">Checking candidate readiness and application display name…</p>}{readiness === "candidate_not_ready" && <p role="alert">A confirmed candidate CV is required before preparing an application. <Link to="/profile/cv">Continue CV onboarding</Link>.</p>}{readiness === "profile_missing" && <p role="alert">An application display name is required before preparing an application. <Link to="/profile">Update your profile</Link>.</p>}{readiness === "unavailable" && <p role="alert">Preparation readiness is unavailable. <button type="button" className="button-secondary" onClick={preparationReadiness.retry} disabled={pending}>Retry readiness</button> Saved history remains readable.</p>}{!workspace.job.actionable && <p role="alert">This vacancy is no longer actionable. Saved preparations remain visible, but new preparation creation is disabled.</p>}{readiness === "ready" && workspace.job.actionable && <><label htmlFor="workspace-target-pages">Target CV pages</label><select id="workspace-target-pages" value={pages} onChange={(event) => setPages(Number(event.target.value) as 1 | 2 | 3)} disabled={pending}><option value={1}>1</option><option value={2}>2</option><option value={3}>3</option></select><label><input type="checkbox" checked={includeCoverLetter} onChange={(event) => setIncludeCoverLetter(event.target.checked)} disabled={pending} /> Include a cover letter</label><label htmlFor="workspace-application-questions">Application questions (one per line)</label><textarea id="workspace-application-questions" value={questions} onChange={(event) => setQuestions(event.target.value)} disabled={pending} /><button type="button" disabled={pending} onClick={() => void create()}>{pending ? "Preparing…" : "Create preparation"}</button>{notice && <p role="status">{notice} {created && <Link to={`/applications/${encodeURIComponent(created.id)}`}>Open preparation</Link>}</p>}{error && <p role="alert">{error}</p>}</>}</section></>;
}

function WorkspaceTracking({ applications, applicationLimit, onRefresh, onShowMore }: { applications: JobWorkspace["applications"]; applicationLimit: number; onRefresh: () => Promise<boolean>; onShowMore: () => Promise<boolean> }) {
  const { api, user } = useAuth(); const [pendingIds, setPendingIds] = useState<Set<string>>(new Set()); const [notice, setNotice] = useState(""); const [error, setError] = useState("");
  const start = async (preparationId: string) => {
    if (pendingIds.has(preparationId)) return;
    setPendingIds((current) => new Set(current).add(preparationId)); setNotice(""); setError("");
    try {
      const result = await createApplicationTracking(api, preparationId, "prepared", {
        userId: user?.id,
        getUserId: () => user?.id,
        reconcileByPreparation: async () => {
          try {
            const value = await api.request<ApplicationTracking>(`/api/v1/application-tracking/by-preparation/${encodeURIComponent(preparationId)}`);
            return value.preparation_id === preparationId ? value : null;
          } catch (reason) {
            if (reason instanceof ApiError && reason.status === 404) return null;
            throw reason;
          }
        },
        reconcileWorkspace: () => onRefresh(),
      });
      if (result.kind === "session_stale") return;
      if (result.kind === "locked") setNotice("A tracking request for this preparation is already in progress. No duplicate was created.");
      else if (result.kind === "confirmed") { setNotice("Tracking saved. The workspace projection is refreshing."); onRefresh(); }
      else if (result.kind === "reconciled_existing") setNotice(result.workspaceRefreshConfirmed === false ? "Saved tracking was confirmed, but the current bounded Workspace projection could not be refreshed." : "Saved tracking was confirmed. The bounded Workspace projection was refreshed separately.");
      else if (result.kind === "reconciliation_failed") setError("Tracking already exists or changed, but Career-trans could not confirm saved tracking for this preparation. Refresh and try again.");
      else if (result.kind === "uncertain_reconciled") setNotice(result.workspaceRefreshConfirmed === false ? "Saved tracking was confirmed, but the current bounded Workspace projection could not be refreshed. The interrupted request itself cannot be identified as its cause." : "Saved tracking was confirmed. The interrupted request itself cannot be identified as its cause.");
      else if (result.kind === "uncertain_unconfirmed") setError("The request outcome is uncertain and current saved tracking could not be confirmed.");
      else if (result.kind === "not_found") setError("This preparation is not available to this account.");
      else setError("Tracking could not be started. The current saved state is being reconciled.");
    } finally { setPendingIds((current) => { const next = new Set(current); next.delete(preparationId); return next; }); }
  };
  return <section className="card" aria-labelledby="workspace-tracking-heading"><div className="section-heading"><h3 id="workspace-tracking-heading">Application tracking</h3><button type="button" className="button-secondary" onClick={onRefresh}>Refresh applications</button></div>{!applications.items.length && <p className="muted">No preparations are available for this canonical job yet.</p>}{!!applications.items.length && <div className="application-list">{applications.items.map((item) => { const context = applicationContextLabel(item); const isPending = pendingIds.has(item.preparation_id); return item.tracking ? <article className="card application-summary" key={item.preparation_id}><p className="eyebrow">{statusLabel(item.tracking.current_status)} · Updated {dateLabel(item.tracking.updated_at)}</p><h3>{item.target.title}</h3><p>{[item.target.company, item.target.location].filter(Boolean).join(" · ") || "Company and location not recorded"}</p><p>Revision {item.tracking.revision} · Preparation {dateLabel(item.created_at)}</p><div className="card-actions"><Link aria-label={`Open tracking history for ${context} · ${statusLabel(item.tracking.current_status)} · updated ${dateLabel(item.tracking.updated_at)}`} to={`/tracking/${encodeURIComponent(item.tracking.id)}`}>Open tracking history</Link><Link aria-label={`Open preparation for ${context}`} to={`/applications/${encodeURIComponent(item.preparation_id)}`}>Open preparation</Link></div></article> : <article className="card application-summary" key={item.preparation_id}><p className="eyebrow">Not tracked</p><h3>{item.target.title}</h3><p>{[item.target.company, item.target.location].filter(Boolean).join(" · ") || "Company and location not recorded"}</p><div className="card-actions"><button type="button" aria-label={`${isPending ? "Starting tracking…" : "Start tracking"} for ${context}`} disabled={isPending} onClick={() => void start(item.preparation_id)}>{isPending ? "Starting tracking…" : "Start tracking"}</button><Link aria-label={`Open preparation for ${context}`} to={`/applications/${encodeURIComponent(item.preparation_id)}`}>Open preparation</Link></div></article>; })}</div>}{notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}{applications.truncated && applicationLimit < APPLICATION_WINDOW_MAX && <button type="button" className="button-secondary" onClick={onShowMore}>Show more</button>}{applications.truncated && applicationLimit >= APPLICATION_WINDOW_MAX && <p className="muted">Showing only the first 100 preparations.</p>}</section>;
}

function JobWorkspacePage({ discoveredJobId, section }: { discoveredJobId: string; section: JobWorkspaceSection }) {
  const location = useLocation();
  const navigationState = location.state as { returnTo?: unknown } | null;
  const candidateReturnTo = navigationState?.returnTo;
  const returnTo = typeof candidateReturnTo === "string" && candidateReturnTo.startsWith("/jobs") && !candidateReturnTo.startsWith("//")
    ? candidateReturnTo
    : "/jobs/results?scope=all";
  const { api, user } = useAuth(); const [workspace, setWorkspace] = useState<JobWorkspace | null>(null); const [applications, setApplications] = useState<JobWorkspace["applications"]>(emptyApplications()); const [applicationLimit, setApplicationLimit] = useState(APPLICATION_WINDOW_STEP); const [state, setState] = useState<"loading" | "ready" | "error" | "not_found">("loading"); const applicationGeneration = useRef(0); const workspaceGeneration = useRef(0); const targetAvailabilityGeneration = useRef(0); const decisionMutator = useJobDecisionMutator(); const [decision, setDecision] = useState<JobWorkspace["decision"] | null>(null);
  const refreshWorkspace = useCallback(async (requestedLimit = APPLICATION_WINDOW_STEP): Promise<boolean> => {
    const current = ++workspaceGeneration.current; applicationGeneration.current += 1; const owner = user?.id; const epoch = api.sessionEpoch();
    try {
      const data = await api.request<JobWorkspace>(`/api/v1/jobs/workspaces/${encodeURIComponent(discoveredJobId)}?application_limit=${requestedLimit}`);
      if (current !== workspaceGeneration.current || api.sessionEpoch() !== epoch || owner !== user?.id) return false;
      setWorkspace(data); setApplications(data.applications ?? emptyApplications()); setApplicationLimit(data.applications?.limit ?? requestedLimit); setDecision(data.decision ?? undecidedDecision(data.job.id)); setState("ready");
      return true;
    } catch (error) {
      if (current === workspaceGeneration.current && api.sessionEpoch() === epoch && owner === user?.id) { setWorkspace(null); setDecision(null); setState(error instanceof ApiError && error.status === 404 ? "not_found" : "error"); }
      return false;
    }
  }, [api, discoveredJobId, user?.id]);
  useEffect(() => { setState("loading"); setWorkspace(null); setApplications(emptyApplications()); setDecision(null); void refreshWorkspace(APPLICATION_WINDOW_STEP); return () => { workspaceGeneration.current += 1; targetAvailabilityGeneration.current += 1; applicationGeneration.current += 1; }; }, [refreshWorkspace]);
  const refreshApplications = (requestedLimit = applicationLimit): Promise<boolean> => {
    const current = ++applicationGeneration.current; const owner = user?.id; const epoch = api.sessionEpoch();
    return api.request<JobWorkspace>(`/api/v1/jobs/workspaces/${encodeURIComponent(discoveredJobId)}?application_limit=${requestedLimit}`).then((data) => {
      if (current !== applicationGeneration.current || api.sessionEpoch() !== epoch || owner !== user?.id) return false;
      setApplications(data.applications ?? emptyApplications()); setApplicationLimit(data.applications?.limit ?? requestedLimit); return true;
    }).catch(() => false);
  };
  const refreshWorkspaceForTargetAvailability = (requestedLimit = applicationLimit): Promise<boolean> => {
    const current = ++targetAvailabilityGeneration.current; const applicationGenerationAtStart = ++applicationGeneration.current; const owner = user?.id; const epoch = api.sessionEpoch();
    return api.request<JobWorkspace>(`/api/v1/jobs/workspaces/${encodeURIComponent(discoveredJobId)}?application_limit=${requestedLimit}`).then((data) => {
      if (current !== targetAvailabilityGeneration.current || api.sessionEpoch() !== epoch || owner !== user?.id) return false;
      setWorkspace((previous) => previous ? { ...previous, job: data.job, current_fit: data.current_fit } : previous);
      if (applicationGeneration.current === applicationGenerationAtStart) { setApplications(data.applications ?? emptyApplications()); setApplicationLimit(data.applications?.limit ?? requestedLimit); }
      return true;
    }).catch((error) => {
      if (current === targetAvailabilityGeneration.current && applicationGeneration.current === applicationGenerationAtStart && api.sessionEpoch() === epoch && owner === user?.id) {
        setWorkspace(null); setDecision(null); setState(error instanceof ApiError && error.status === 404 ? "not_found" : "error");
      }
      return false;
    });
  };
  const changeDecision = async (target: UserJobDecisionValue) => { if (!decision) return; const result = await decisionMutator.mutate(decision, target); if (result.decision) setDecision(result.decision); };
  if (state === "loading") return <section className="jobs-section" aria-busy="true"><p role="status">Loading job workspace…</p></section>;
  if (state === "not_found") return <section className="jobs-section"><div className="card"><h2>Job Workspace not found</h2><p>This canonical job is no longer available through the workspace.</p><Link className="button-secondary" to={returnTo}>Return to Results</Link></div></section>;
  if (state === "error" || !workspace) return <section className="jobs-section"><div className="card"><h2>Job Workspace unavailable</h2><p role="alert">The job workspace could not be loaded. Try again from the Job Search sections.</p><Link className="button-secondary" to="/jobs/find">Return to Find jobs</Link></div></section>;
  return <section className="jobs-section job-workspace" aria-labelledby="job-workspace-heading"><header className="section-heading"><div><p className="eyebrow">Canonical job workspace</p><h2 id="job-workspace-heading">{workspace.job.title}</h2><p className="muted">{[workspace.job.company, workspace.job.location].filter(Boolean).join(" · ") || "Job details"}</p></div></header><div className="card-actions"><Link className="button-secondary" to={returnTo}>Back to Results</Link></div>{decision && <><DecisionControls decision={decision} disabled={decisionMutator.pending.has(decision.discovered_job_id)} onMutate={(target) => void changeDecision(target)} />{decisionMutator.notices[decision.discovered_job_id] && <p className="notice" role="status">{decisionMutator.notices[decision.discovered_job_id]}</p>}</>}<nav className="jobs-tabs" aria-label="Job workspace sections">{(["overview", "fit", "application", "tracking"] as JobWorkspaceSection[]).map((item) => <NavLink key={item} to={sectionPath(discoveredJobId, item)} state={{ returnTo }} aria-current={section === item ? "page" : undefined} className={section === item ? "active" : undefined}>{item === "overview" ? "Overview" : item === "fit" ? "Fit" : item === "application" ? "Application" : "Tracking"}</NavLink>)}</nav>{section === "overview" && <WorkspaceOverview workspace={workspace} />}{section === "fit" && <CurrentFit workspace={workspace} />}{section === "application" && <WorkspaceApplications workspace={workspace} applications={applications} applicationLimit={applicationLimit} onRefresh={() => refreshApplications()} onShowMore={() => refreshApplications(Math.min(applicationLimit + APPLICATION_WINDOW_STEP, APPLICATION_WINDOW_MAX))} onTargetUnavailable={() => refreshWorkspaceForTargetAvailability(applicationLimit)} />}{section === "tracking" && <WorkspaceTracking applications={applications} applicationLimit={applicationLimit} onRefresh={() => refreshApplications()} onShowMore={() => refreshApplications(Math.min(applicationLimit + APPLICATION_WINDOW_STEP, APPLICATION_WINDOW_MAX))} />}</section>;
}

export default JobWorkspacePage;

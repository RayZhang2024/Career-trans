import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import type { JobWorkspace, JobWorkspaceEvaluation } from "./api";
import { ApiError, useAuth } from "./auth";
import { RuntimeAttributionPanel } from "./RuntimeAttributionPanel";
import { OpportunityDetail } from "./JobsPage";

export type JobWorkspaceSection = "overview" | "fit" | "application" | "tracking";

function sectionPath(discoveredJobId: string, section: JobWorkspaceSection) {
  return section === "overview" ? `/jobs/${encodeURIComponent(discoveredJobId)}` : `/jobs/${encodeURIComponent(discoveredJobId)}/${section}`;
}

function EvaluationHistory({ evaluations }: { evaluations: JobWorkspaceEvaluation[] }) {
  if (!evaluations.length) return <p className="muted">No persisted evaluation snapshots are available for this job.</p>;
  return <section className="workspace-evaluation-history" aria-labelledby="workspace-evaluation-history-heading"><h3 id="workspace-evaluation-history-heading">Evaluation history</h3><ol className="run-list">{evaluations.map((evaluation) => <li className="card" key={evaluation.id}><div className="section-heading"><div><h4>{evaluation.applicability === "current" ? "Current evaluation" : evaluation.applicability === "historical" ? "Historical evaluation" : "Applicability unavailable"}</h4><p>{new Date(evaluation.created_at).toLocaleString()}</p></div></div>{evaluation.applicability === "unknown" && <p className="notice">Currentness could not be confirmed, so this snapshot is shown without a current or historical claim.</p>}<OpportunityDetail opportunity={evaluation.opportunity} historical={evaluation.applicability !== "current"} /><RuntimeAttributionPanel attribution={evaluation.runtime_attribution} boundary="evaluation" /></li>)}</ol></section>;
}

function WorkspaceOverview({ workspace }: { workspace: JobWorkspace }) {
  const { job, provenance } = workspace;
  return <><section className="card" aria-labelledby="workspace-facts-heading"><h2 id="workspace-facts-heading">Job facts</h2><dl className="detail-grid"><div><dt>Title</dt><dd>{job.title}</dd></div><div><dt>Company</dt><dd>{job.company ?? "Not provided"}</dd></div><div><dt>Location</dt><dd>{job.location ?? "Not provided"}</dd></div><div><dt>Work arrangement</dt><dd>{job.work_arrangement ?? "Not provided"}</dd></div><div><dt>Employment type</dt><dd>{job.employment_type ?? "Not provided"}</dd></div><div><dt>Lifecycle</dt><dd>{job.state}</dd></div><div><dt>Verification</dt><dd>{job.verification_status}{job.verification_reason ? ` · ${job.verification_reason}` : ""}</dd></div><div><dt>First seen</dt><dd>{new Date(job.first_seen_at).toLocaleString()}</dd></div><div><dt>Last seen</dt><dd>{new Date(job.last_seen_at).toLocaleString()}</dd></div></dl>{job.description && <><h3>Description</h3><p className="profile-prose">{job.description}</p></>}<div className="card-actions"><a href={job.url} target="_blank" rel="noopener noreferrer">Open vacancy</a><Link className="button-secondary" to={sectionPath(job.id, "fit")}>Review Fit</Link></div></section><section className="card" aria-labelledby="workspace-provenance-heading"><h2 id="workspace-provenance-heading">Provenance</h2>{provenance.items.length ? <ol>{provenance.items.map((item) => <li key={item.id}><strong>{item.runtime}</strong>{item.discovered_via ? ` · ${item.discovered_via}` : ""} · imported {new Date(item.imported_at).toLocaleString()}{item.source_ref ? <> · <span>{item.source_ref}</span></> : null}</li>)}</ol> : <p className="muted">No provenance records are available.</p>}{provenance.truncated && <p className="muted">Showing the first {provenance.limit} provenance records.</p>}</section></>;
}

function CurrentFit({ workspace }: { workspace: JobWorkspace }) {
  const fit = workspace.current_fit;
  return <><section className="card" aria-labelledby="workspace-current-fit-heading"><h2 id="workspace-current-fit-heading">Current Fit</h2>{fit.status === "current" && fit.evaluation ? <><p><strong>Current evaluation confirmed.</strong> This matches the current job content, candidate context, and evaluation contract.</p><OpportunityDetail opportunity={fit.evaluation.opportunity} /></> : fit.status === "none" ? <p>{fit.reason === "job_not_actionable" ? "This vacancy is not currently actionable, so no current Fit is claimed." : "No current Fit is available for this job. Persisted evaluation history remains below when available."}</p> : <p>Current Fit is temporarily unavailable because {fit.reason === "candidate_evidence_incomplete" ? "candidate evidence is not fully materialised" : fit.reason === "candidate_not_ready" ? "the confirmed candidate context is not ready" : "the current evaluation configuration could not be confirmed"}. Persisted history remains available below.</p>}</section><EvaluationHistory evaluations={workspace.evaluations.items} /></>;
}

function JobWorkspacePage({ discoveredJobId, section }: { discoveredJobId: string; section: JobWorkspaceSection }) {
  const { api } = useAuth();
  const [workspace, setWorkspace] = useState<JobWorkspace | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error" | "not_found">("loading");
  useEffect(() => { let active = true; setState("loading"); void api.request<JobWorkspace>(`/api/v1/jobs/workspaces/${encodeURIComponent(discoveredJobId)}`).then((data) => { if (active) { setWorkspace(data); setState("ready"); } }).catch((error) => { if (active) { setWorkspace(null); setState(error instanceof ApiError && error.status === 404 ? "not_found" : "error"); } }); return () => { active = false; }; }, [api, discoveredJobId]);
  if (state === "loading") return <section className="jobs-section" aria-busy="true"><p role="status">Loading job workspace…</p></section>;
  if (state === "not_found") return <section className="jobs-section"><div className="card"><h2>Job Workspace not found</h2><p>This canonical job is no longer available through the workspace.</p><Link className="button-secondary" to="/jobs/inbox">Return to Inbox</Link></div></section>;
  if (state === "error" || !workspace) return <section className="jobs-section"><div className="card"><h2>Job Workspace unavailable</h2><p role="alert">The job workspace could not be loaded. Try again from the Job Search sections.</p><Link className="button-secondary" to="/jobs/find">Return to Find jobs</Link></div></section>;
  return <section className="jobs-section job-workspace" aria-labelledby="job-workspace-heading"><header className="section-heading"><div><p className="eyebrow">Canonical job workspace</p><h2 id="job-workspace-heading">{workspace.job.title}</h2><p className="muted">{[workspace.job.company, workspace.job.location].filter(Boolean).join(" · ") || "Job details"}</p></div></header><nav className="jobs-tabs" aria-label="Job workspace sections">{(["overview", "fit", "application", "tracking"] as JobWorkspaceSection[]).map((item) => <NavLink key={item} to={sectionPath(discoveredJobId, item)} aria-current={section === item ? "page" : undefined} className={section === item ? "active" : undefined}>{item === "overview" ? "Overview" : item === "fit" ? "Fit" : item === "application" ? "Application" : "Tracking"}</NavLink>)}</nav>{section === "overview" && <WorkspaceOverview workspace={workspace} />}{section === "fit" && <CurrentFit workspace={workspace} />}{section === "application" && <section className="card"><h3>Application</h3><p>Application preparation is not part of this Job Workspace yet.</p><Link className="button-secondary" to="/applications">Open Applications</Link></section>}{section === "tracking" && <section className="card"><h3>Tracking</h3><p>Application tracking is not part of this Job Workspace yet.</p><Link className="button-secondary" to="/tracking">Open Tracking</Link></section>}</section>;
}

export default JobWorkspacePage;

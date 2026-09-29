import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import type { ApplicationPreparation, ApplicationPrepareRequest, BoundedResponse, CreateDiscoveryRun, DiscoveryRunCreated, DiscoveryRunDetail, DiscoveryRunSummary, DiscoveryScheduleRead, HistoricalRunJobDetail, InboxSummary, OnboardingStatus, Profile, RankedJobOpportunity, ScheduledExecutionRead, UserJobDecision, UserJobDecisionListItem, UserJobDecisionValue, UserOpportunitySummary } from "./api";
import { ApiError, useAuth } from "./auth";
import { RuntimeAttributionPanel } from "./RuntimeAttributionPanel";
import JobWorkspacePage, { type JobWorkspaceSection } from "./JobWorkspacePage";
import { JobsSearchesPage } from "./JobsSearchesPage";
import { SearchIntentEditor } from "./SearchIntentEditor";
import { emptySearchIntent, searchIntentEquals, searchIntentFromQuery, searchIntentToQuery, type SearchIntent } from "./SearchIntent";
import { runSavedDiscoveryNowWithReconciliation, type DiscoveryRunReconciliation } from "./discoveryRunNow";
import { DecisionControls, undecidedDecision, useJobDecisionMutator } from "./jobDecisions";
import { createApplicationPreparation, type PreparationPrerequisites } from "./applicationPreparationController";

type SectionState<T> = { phase: "loading" | "loaded" | "error"; data?: T; error?: string };
type InboxDismissalNotice = { decision: UserJobDecision; title: string; message: string };
const WINDOW = 20;
const MAX_WINDOW = 100;
const emptyPage = <T,>(): SectionState<T> => ({ phase: "loading" });
const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const nextWindow = (current: number) => Math.min(MAX_WINDOW, current + WINDOW);
const numberLabel = (value: number) => Number.isInteger(value) ? String(value) : value.toFixed(1);
const dateLabel = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : "time unavailable";
const jobContextLabel = (title: string, company?: string | null, location?: string | null, discriminator?: string) => [title, company, location, discriminator].filter(Boolean).join(" · ") || "this job";
const runContextLabel = (run: Pick<DiscoveryRunSummary, "status" | "started_at">) => `${run.status === "running" ? "evaluation in progress" : titleCase(run.status)} · started ${dateLabel(run.started_at)}`;

const outcomeLabels: Record<DiscoveryRunDetail["jobs"][number]["outcome"], string> = {
  newly_evaluated: "Newly evaluated", reused_evaluation: "Reused evaluation", not_actionable: "Not actionable",
  presemantic_filtered: "Presemantic filtered", outside_semantic_budget: "Outside semantic budget",
  semantic_rejected: "Semantic rejected", outside_deep_analysis_budget: "Outside deep-analysis budget",
  analysis_failed: "Analysis failed",
};
type FindRunOutcome =
  | { kind: "execution"; scheduleName: string; status: ScheduledExecutionRead["status"]; inboxRefresh?: boolean }
  | { kind: "changed"; scheduleName: string }
  | { kind: "preflight_stale"; scheduleName: string }
  | { kind: "post_stale"; scheduleName: string }
  | { kind: "reconciliation_stale"; scheduleName: string; source: "already_running" | "uncertain" }
  | { kind: "preflight_rejected"; scheduleName: string; status: number }
  | { kind: "preflight_unavailable"; scheduleName: string }
  | { kind: "already_running"; scheduleName: string; reconciliation: DiscoveryRunReconciliation<ScheduledExecutionRead[]> }
  | { kind: "uncertain"; scheduleName: string; reconciliation: DiscoveryRunReconciliation<ScheduledExecutionRead[]> }
  | { kind: "post_rejected"; scheduleName: string; status: number };

type JobSearchView = "find" | "saved" | "inbox" | "recommended" | "shortlisted" | "history" | "unknown";
type JobSearchRoute = { kind: "search"; view: JobSearchView } | { kind: "workspace"; discoveredJobId: string; section: JobWorkspaceSection } | { kind: "unknown" };
const RESERVED_JOB_ROUTE_SEGMENTS = new Set(["find", "inbox", "history", "opportunities", "searches"]);

function safeDecodeRouteSegment(value: string): string | null {
  try { return decodeURIComponent(value); } catch { return null; }
}

function parseJobSearchRoute(pathname: string): JobSearchRoute {
  if (pathname === "/jobs/find" || pathname === "/jobs") return { kind: "search", view: "find" };
  if (pathname === "/jobs/find/saved") return { kind: "search", view: "saved" };
  if (pathname === "/jobs/inbox") return { kind: "search", view: "inbox" };
  if (pathname === "/jobs/opportunities/recommended") return { kind: "search", view: "recommended" };
  if (pathname === "/jobs/opportunities/shortlisted") return { kind: "search", view: "shortlisted" };
  if (pathname === "/jobs/history") return { kind: "search", view: "history" };
  const match = pathname.match(/^\/jobs\/([^/]+)(?:\/(fit|application|tracking))?$/);
  if (match) {
    const discoveredJobId = safeDecodeRouteSegment(match[1]);
    if (discoveredJobId === null || RESERVED_JOB_ROUTE_SEGMENTS.has(discoveredJobId.trim().toLowerCase())) return { kind: "unknown" };
    return { kind: "workspace", discoveredJobId, section: (match[2] ?? "overview") as JobWorkspaceSection };
  }
  return { kind: "unknown" };
}

function JobSearchNavigation({ view }: { view: JobSearchView }) {
  const items = [
    ["/jobs/find", "Find jobs", view === "find" || view === "saved"],
    ["/jobs/inbox", "Inbox", view === "inbox"],
    ["/jobs/opportunities/recommended", "My opportunities", view === "recommended" || view === "shortlisted"],
    ["/jobs/history", "Search history", view === "history"],
  ] as const;
  return <nav className="jobs-tabs" aria-label="Job Search sections">{items.map(([to, label, active]) => <NavLink key={to} to={to} aria-current={active ? "page" : undefined} className={active ? "active" : undefined}>{label}</NavLink>)}</nav>;
}

function OpportunitiesNavigation({ view }: { view: JobSearchView }) {
  return <nav className="jobs-tabs" aria-label="My opportunities sections"><NavLink to="/jobs/opportunities/shortlisted" aria-current={view === "shortlisted" ? "page" : undefined} className={view === "shortlisted" ? "active" : undefined}>Shortlisted</NavLink><NavLink to="/jobs/opportunities/recommended" aria-current={view === "recommended" ? "page" : undefined} className={view === "recommended" ? "active" : undefined}>Recommended / Current analyses</NavLink></nav>;
}

function StateMessage<T>({ state, empty, children, onRetry }: { state: SectionState<T>; empty: boolean; children: React.ReactNode; onRetry: () => void }) {
  if (state.phase === "loading" && !state.data) return <p className="muted" role="status">Loading…</p>;
  if (state.phase === "error" && !state.data) return <div className="section-error"><p role="alert">{state.error ?? "This section is unavailable."}</p><button type="button" className="button-secondary" onClick={onRetry}>Retry this section</button></div>;
  if (state.phase === "loaded" && empty) return <p className="muted">No items to show.</p>;
  return <>{state.phase === "error" && <p className="notice" role="status">Refresh failed. Previously loaded information is still shown.</p>}{children}</>;
}

function TextList({ items, label }: { items: string[]; label: string }) {
  if (!items.length) return null;
  return <section><h4>{label}</h4><ul>{items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></section>;
}

function IndexedRequirements({ title, indexes, requirements }: { title: string; indexes: number[]; requirements: NonNullable<RankedJobOpportunity["job_profile"]>["requirements"] }) {
  if (!indexes.length) return null;
  return <section><h4>{title}</h4><ul>{indexes.map((index, position) => <li key={`${position}-${index}`}>{Number.isInteger(index) && index >= 0 && index < requirements.length ? requirements[index].text : <span className="muted">Requirement reference unavailable.</span>}</li>)}</ul></section>;
}

export type OpportunityDetailMode = "current" | "historical" | "unknown";

export function OpportunityDetail({ opportunity, historical = false, mode }: { opportunity: RankedJobOpportunity; historical?: boolean; mode?: OpportunityDetailMode }) {
  const presentation = mode ?? (historical ? "historical" : "current");
  const isHistorical = presentation === "historical";
  const isUnknown = presentation === "unknown";
  const profile = opportunity.job_profile;
  const requirements = profile?.requirements ?? [];
  return <article className="job-detail" aria-label={isHistorical ? "Historical evaluation detail" : isUnknown ? "Evaluation applicability unavailable" : "Current opportunity detail"}>
    <h3>{isHistorical ? "Historical evaluation snapshot" : isUnknown ? "Persisted evaluation snapshot" : "Opportunity detail"}</h3>
    {isHistorical && <p className="notice">This is historical evaluation state. It does not describe the vacancy or recommendation as current.</p>}
    {isUnknown && <p className="notice">Applicability unavailable. Currentness could not be established, so this snapshot is not claimed as current or historical.</p>}
    <section><h4>Recommendation</h4><p><strong>{opportunity.recommendation_assessment.recommendation.toUpperCase()}</strong> — {opportunity.recommendation_assessment.reasoning}</p><TextList label="Key strengths" items={opportunity.recommendation_assessment.key_strengths} /><TextList label="Trade-offs" items={opportunity.recommendation_assessment.key_tradeoffs} /><IndexedRequirements title="Recommendation hard blockers" indexes={opportunity.recommendation_assessment.hard_blockers} requirements={requirements} /></section>
    <section><h4>Fit assessment</h4><p>Fit score: {numberLabel(opportunity.fit_assessment.fit_score)}</p><IndexedRequirements title="Fit strengths" indexes={opportunity.fit_assessment.strengths} requirements={requirements} /><IndexedRequirements title="Hard blockers" indexes={opportunity.fit_assessment.hard_blockers} requirements={requirements} />
      {!!opportunity.fit_assessment.gaps.length && <><h4>Gaps</h4><ul>{opportunity.fit_assessment.gaps.map((gap, index) => <li key={`${gap.requirement_index}-${index}`}><strong>{titleCase(gap.gap_type)} · {titleCase(gap.severity)}</strong>: {Number.isInteger(gap.requirement_index) && gap.requirement_index >= 0 && gap.requirement_index < requirements.length ? requirements[gap.requirement_index].text : <span className="muted">Requirement reference unavailable.</span>} — {gap.reason}</li>)}</ul></>}
    </section>
    <section><h4>Career alignment</h4><p>Score: {numberLabel(opportunity.career_assessment.career_alignment_score)} · confidence: {titleCase(opportunity.career_assessment.confidence)}</p><p>{opportunity.career_assessment.reasoning}</p><TextList label="Strategic strengths" items={opportunity.career_assessment.strategic_strengths} /><TextList label="Strategic trade-offs" items={opportunity.career_assessment.strategic_tradeoffs} />{opportunity.career_assessment.dimensions.length > 0 && <ul>{opportunity.career_assessment.dimensions.map((dimension) => <li key={dimension.dimension}><strong>{titleCase(dimension.dimension)}:</strong> {numberLabel(dimension.score * 100)} — {dimension.reasoning}</li>)}</ul>}</section>
    {profile && <section><h4>Job profile</h4><dl className="detail-grid">{(["title", "company", "location", "work_arrangement", "seniority", "salary", "employment_type", "application_deadline"] as const).map((field) => profile[field] ? <div key={field}><dt>{titleCase(field)}</dt><dd>{profile[field]}</dd></div> : null)}</dl><TextList label="Responsibilities" items={profile.responsibilities} /><TextList label="Technical skills" items={profile.technical_skills} /><TextList label="Domain knowledge" items={profile.domain_knowledge} /><TextList label="Security requirements" items={profile.security_requirements} /><TextList label="Work authorization requirements" items={profile.work_authorization_requirements} /></section>}
    <section><h4>Extracted requirements and matches</h4>{!profile || !profile.requirements.length ? <p className="muted">No extracted requirements are available.</p> : <ul>{profile.requirements.map((requirement, index) => {
      const match = opportunity.requirement_matches.find((item) => item.requirement_index === index);
      return <li key={`${index}-${requirement.text}`}><strong>{requirement.text}</strong> <span className="muted">({titleCase(requirement.importance)} · {titleCase(requirement.category)})</span>{match ? <p>{titleCase(match.match_type)} · {numberLabel(match.score * 100)} — {match.reasoning}</p> : <p className="muted">No match detail available.</p>}</li>;
    })}</ul>}{opportunity.requirement_matches.some((item) => !Number.isInteger(item.requirement_index) || item.requirement_index < 0 || item.requirement_index >= requirements.length) && <p className="muted">Requirement reference unavailable.</p>}</section>
    <section><h4>{isHistorical ? "Historical posting recency signal" : isUnknown ? "Persisted posting recency signal" : "Posting recency signal"}</h4><p>{titleCase(opportunity.legitimacy.legitimacy)} — {opportunity.legitimacy.reasoning}</p></section>
  </article>;
}

function OpportunityPreparation({ opportunity, ready, context, onUnavailable, onReadinessRefresh }: { opportunity: UserOpportunitySummary; ready: boolean | undefined; context: string; onUnavailable: () => Promise<boolean>; onReadinessRefresh: (status: OnboardingStatus | undefined) => void }) {
  const { api, user } = useAuth();
  const [open, setOpen] = useState(false);
  const [profileState, setProfileState] = useState<"unchecked" | "checking" | "ready" | "missing" | "error">("unchecked");
  const [pages, setPages] = useState<1 | 2 | 3>(2);
  const [includeLetter, setIncludeLetter] = useState(true);
  const [questions, setQuestions] = useState<string[]>([""]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<React.ReactNode>(null);
  const [created, setCreated] = useState<ApplicationPreparation | null>(null);
  const [reconciled, setReconciled] = useState<ApplicationPreparation[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [prerequisitesUnconfirmed, setPrerequisitesUnconfirmed] = useState(false);
  const [decision, setDecision] = useState(opportunity.decision ?? undecidedDecision(opportunity.discovered_job_id));
  const decisionMutator = useJobDecisionMutator();
  const alive = useRef(true);
  const generation = useRef(0);
  const lock = useRef(false);
  const previousReady = useRef(ready);
  const previousProfileState = useRef(profileState);
  useEffect(() => { alive.current = true; return () => { alive.current = false; generation.current += 1; }; }, []);
  useEffect(() => {
    const readinessRecovered = ready === true && previousReady.current !== true;
    const profileRecovered = profileState === "ready" && previousProfileState.current !== "ready";
    previousReady.current = ready;
    previousProfileState.current = profileState;
    if (ready === true && profileState === "ready" && (readinessRecovered || profileRecovered)) setPrerequisitesUnconfirmed(false);
  }, [ready, profileState]);

  const canSubmit = ready === true && profileState === "ready" && !unavailable && !prerequisitesUnconfirmed;
  useEffect(() => { setDecision(opportunity.decision ?? undecidedDecision(opportunity.discovered_job_id)); }, [opportunity.decision, opportunity.discovered_job_id]);
  const changeDecision = async (target: UserJobDecisionValue) => {
    const result = await decisionMutator.mutate(decision, target);
    if (result.decision) {
      setDecision(result.decision);
      if (result.kind === "confirmed") window.dispatchEvent(new CustomEvent("career-trans-job-decision-changed"));
    }
  };

  const checkDisplayName = async () => {
    if (ready !== true) return;
    setProfileState("checking"); setError(null);
    try {
      const profile = await api.request<Profile>("/api/v1/profile");
      if (alive.current) setProfileState(profile.display_name?.trim() ? "ready" : "missing");
    } catch (reason) {
      if (!alive.current) return;
      setProfileState(reason instanceof ApiError && reason.status === 404 ? "missing" : "error");
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (lock.current || pending || !canSubmit) return;
    const cleanedQuestions = questions.map((question) => question.trim()).filter(Boolean);
    if (cleanedQuestions.length > 8) return;
    const requestGeneration = ++generation.current;
    lock.current = true; setPending(true); setError(null); setCreated(null); setReconciled([]);
    const payload: ApplicationPrepareRequest = { target: { discovered_job_id: opportunity.discovered_job_id }, target_pages: pages, include_cover_letter: includeLetter, application_questions: cleanedQuestions };
    try {
      const mutation = await createApplicationPreparation(api, payload, { userId: user?.id, getUserId: () => user?.id });
      if (!alive.current || requestGeneration !== generation.current || mutation.kind === "session_stale") return;
      if (mutation.kind === "locked") {
        setError("A preparation request for this job is already in progress. No new preparation was created.");
      } else if (mutation.kind === "confirmed") {
        setCreated(mutation.value);
      } else if (mutation.kind === "target_unavailable") {
        setUnavailable(true);
        const refreshed = await onUnavailable();
        if (alive.current && requestGeneration === generation.current) setError(refreshed ? "This opportunity is no longer available. The current opportunity view was refreshed." : "This opportunity is no longer available. The opportunity refresh could not be confirmed.");
      } else if (mutation.kind === "prerequisite_conflict") {
        applyPreparationPrerequisites(mutation.prerequisites);
      } else if (mutation.kind === "uncertain") {
        try {
          const history = await api.request<ApplicationPreparation[]>("/api/v1/applications");
          if (alive.current && requestGeneration === generation.current) {
            setReconciled(history.filter((item) => item.target.canonical_discovered_job_id === opportunity.discovered_job_id));
            setError("The preparation request was interrupted. Career-trans cannot confirm from this response whether a preparation was created. Saved application history was refreshed; any matching items are shown as history only.");
          }
        } catch (historyError) {
          if (alive.current && requestGeneration === generation.current && (historyError as Error)?.name !== "AbortError") setError("The preparation request was interrupted. Career-trans cannot confirm from this response whether a preparation was created. Saved application history could not be confirmed as refreshed.");
        }
      } else if (mutation.kind === "failed") {
        const reason = mutation.error;
        if (reason instanceof ApiError && reason.status === 422) setError("Career-trans could not prepare this application because the target or request did not contain sufficient usable information.");
        else if (reason instanceof ApiError && reason.status === 503) setError("Application preparation is temporarily unavailable. Your saved application history remains available.");
        else setError("Career-trans could not prepare this application. No preparation success was confirmed.");
      }
    } finally { lock.current = false; if (alive.current) setPending(false); }
  };

  const applyPreparationPrerequisites = (prerequisites: PreparationPrerequisites) => {
    if (!alive.current) return;
    onReadinessRefresh(prerequisites.onboarding);
    setPrerequisitesUnconfirmed(prerequisites.state !== "ready");
    setProfileState(prerequisites.state === "ready" ? "ready" : prerequisites.state === "profile_missing" ? "missing" : profileState);
    if (prerequisites.state === "candidate_not_ready") setError(<>A confirmed candidate CV is required before preparing an application. <Link to="/profile/cv">Continue CV onboarding</Link>.</>);
    else if (prerequisites.state === "profile_missing") setError(<>An application display name is required. <Link to="/profile">Update your profile</Link>.</>);
    else if (prerequisites.state === "unavailable") setError("Career-trans could not confirm the current preparation prerequisites. Review your CV and profile, then try again.");
    else setError("Career-trans could not prepare this application because the current candidate or application data conflicts with the request.");
  };

  return <section className="preparation-panel" aria-label={`Prepare application for ${opportunity.title}`}>
    <DecisionControls decision={decision} context={context} disabled={decisionMutator.pending.has(decision.discovered_job_id)} onMutate={(target) => void changeDecision(target)} />
    {decisionMutator.notices[decision.discovered_job_id] && <p className="notice" role="status">{decisionMutator.notices[decision.discovered_job_id]}</p>}
    <button type="button" className="button-secondary" aria-label={`${open ? "Close preparation options" : "Prepare application"} for ${context}`} aria-expanded={open} disabled={!open && (ready !== true || unavailable)} onClick={() => { setOpen((value) => !value); if (!open && profileState === "unchecked") void checkDisplayName(); }}>{open ? "Close preparation options" : "Prepare application"}</button>
    {ready !== true && <p className="muted">{ready === false ? <>Confirm a CV before preparing an application. <Link to="/profile/cv">CV onboarding for application preparation</Link>.</> : "Candidate readiness is being confirmed. Preparation is unavailable until it can be verified."}</p>}
    {open && <form className="card preparation-form" aria-label={`Prepare application for ${opportunity.title}`} onSubmit={(event) => void submit(event)}>
      <h4>Prepare for {opportunity.title}</h4>
      {ready !== true && !error && <p role="alert">{ready === false ? <>A confirmed candidate CV is required before preparing an application. <Link to="/profile/cv">Continue CV onboarding</Link>.</> : "Candidate readiness could not be confirmed. New preparation is unavailable until it can be verified."}</p>}
      {profileState === "checking" && <p role="status">Checking application identity…</p>}
      {profileState === "missing" && !error && <p role="alert">An application display name is required. <Link to="/profile">Update your profile</Link>.</p>}
      {profileState === "error" && <p role="alert">Profile readiness could not be confirmed. {ready === true && <button type="button" className="button-secondary" onClick={() => void checkDisplayName()}>Retry profile check</button>}</p>}
      <label htmlFor={`pages-${opportunity.discovered_job_id}`}>Target CV pages</label><select id={`pages-${opportunity.discovered_job_id}`} value={pages} onChange={(event) => setPages(Number(event.target.value) as 1 | 2 | 3)} disabled={pending}><option value={1}>1</option><option value={2}>2</option><option value={3}>3</option></select>
      <label><input type="checkbox" checked={includeLetter} onChange={(event) => setIncludeLetter(event.target.checked)} disabled={pending} /> Include a cover letter</label>
      <fieldset disabled={pending}><legend>Application questions (up to 8)</legend>{questions.map((question, index) => <div className="question-input" key={index}><label htmlFor={`question-${opportunity.discovered_job_id}-${index}`}>Question {index + 1}</label><textarea id={`question-${opportunity.discovered_job_id}-${index}`} value={question} onChange={(event) => setQuestions((old) => old.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} />{questions.length > 1 && <button type="button" className="button-secondary" onClick={() => setQuestions((old) => old.filter((_, itemIndex) => itemIndex !== index))}>Remove question</button>}</div>)}{questions.length < 8 && <button type="button" className="button-secondary" onClick={() => setQuestions((old) => [...old, ""])}>Add question</button>}</fieldset>
      {profileState === "ready" && <button type="submit" disabled={pending || !canSubmit}>{pending ? "Preparing…" : "Create preparation"}</button>}
      {pending && <p role="status"><strong>{opportunity.title}:</strong> Preparing application… this may take several minutes.</p>}
      {error && <p role="alert">{error}</p>}
      {created && <p role="status">Preparation saved. <Link to={`/applications/${encodeURIComponent(created.id)}`}>Review this preparation</Link>.</p>}
      {reconciled.length > 0 && <div className="notice"><p>Matching saved preparations are shown as history only; they cannot be attributed to the interrupted request.</p><ul>{reconciled.map((item) => <li key={item.id}><Link to={`/applications/${encodeURIComponent(item.id)}`}>{item.target.title} · {new Date(item.created_at).toLocaleString()}</Link></li>)}</ul></div>}
    </form>}
  </section>;
}

function ExactEvaluationResult({ run, fallbackQuery }: { run: DiscoveryRunCreated; fallbackQuery?: CreateDiscoveryRun["query"] }) {
  const query = (run.run_input?.query && typeof run.run_input.query === "object" ? run.run_input.query : fallbackQuery ?? {}) as Record<string, unknown>;
  const jobs = run.jobs ?? [];
  const listValue = (key: string) => Array.isArray(query[key]) ? query[key].map(String).join(", ") || "Any" : "Any";
  return <section className="card evaluation-result" aria-label="Analysis just completed"><h2>Analysis just completed</h2><p><strong>Run:</strong> {run.id} · <strong>Status:</strong> {titleCase(run.status ?? "completed")}</p><p>Started {run.started_at ? new Date(run.started_at).toLocaleString() : "Time unavailable"}{run.completed_at ? ` · completed ${new Date(run.completed_at).toLocaleString()}` : ""}</p><p>Search themes: {listValue("keywords")} · locations: {listValue("locations")} · remote policy: {query.remote_ok === false ? "Exclude remote jobs" : "No remote restriction"}</p><h3>Exact returned results</h3><ol>{jobs.map((row, index) => { const context = row.opportunity ? jobContextLabel(row.opportunity.job.title, row.opportunity.job.company, row.opportunity.job.location) : `returned result ${index + 1}`; return <li key={row.discovered_job_id}><p><strong>Outcome:</strong> {row.outcome ? outcomeLabels[row.outcome] : "Returned result"}</p>{row.evaluation_id && <p><strong>Evaluation:</strong> {row.evaluation_id}</p>}{row.opportunity ? <><p><strong>Recommendation:</strong> {row.opportunity.recommendation_assessment.recommendation.toUpperCase()}</p><p>{row.opportunity.job.title} · {row.opportunity.job.company ?? "Company not provided"}</p></> : <p className="muted">No successful evaluation result was returned for this row.</p>}<div className="card-actions"><Link className="button-secondary" aria-label={`Open workspace for ${context}`} to={`/jobs/${encodeURIComponent(row.discovered_job_id)}`}>Open workspace</Link><Link className="button-secondary" aria-label={`Open exact run result for ${context}`} to={`/jobs/history?run=${encodeURIComponent(run.id)}&job=${encodeURIComponent(row.discovered_job_id)}`}>Open exact run result</Link>{row.opportunity && <><Link className="button-secondary" aria-label={`Open Fit for ${context}`} to={`/jobs/${encodeURIComponent(row.discovered_job_id)}/fit`}>Open Fit</Link><a aria-label={`Open vacancy for ${context}`} href={row.opportunity.job.url} target="_blank" rel="noopener noreferrer">Open vacancy</a></>}</div></li>; })}</ol></section>;
}


export function JobsPage() {
  const { api, user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const route = parseJobSearchRoute(location.pathname);
  const view: JobSearchView = route.kind === "search" ? route.view : route.kind === "workspace" ? "unknown" : "unknown";
  const [onboarding, setOnboarding] = useState<SectionState<OnboardingStatus>>({ phase: "loading" });
  const [opportunities, setOpportunities] = useState<SectionState<BoundedResponse<UserOpportunitySummary>>>({ phase: "loading" });
  const [runs, setRuns] = useState<SectionState<BoundedResponse<DiscoveryRunSummary>>>({ phase: "loading" });
  const [inbox, setInbox] = useState<SectionState<BoundedResponse<InboxSummary>>>({ phase: "loading" });
  const [shortlisted, setShortlisted] = useState<SectionState<BoundedResponse<UserJobDecisionListItem>>>({ phase: "loading" });
  const [dismissed, setDismissed] = useState<SectionState<BoundedResponse<UserJobDecisionListItem>>>({ phase: "loading" });
  const [savedSchedules, setSavedSchedules] = useState<SectionState<DiscoveryScheduleRead[]>>({ phase: "loading" });
  const [opportunityLimit, setOpportunityLimit] = useState(WINDOW);
  const [runLimit, setRunLimit] = useState(WINDOW);
  const [inboxLimit, setInboxLimit] = useState(WINDOW);
  const [shortlistedLimit, setShortlistedLimit] = useState(WINDOW);
  const [dismissedLimit, setDismissedLimit] = useState(WINDOW);
  const [showDismissed, setShowDismissed] = useState(false);
  const [lastConfirmedInboxDismissal, setLastConfirmedInboxDismissal] = useState<InboxDismissalNotice | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<SectionState<DiscoveryRunDetail>>({ phase: "loading" });
  const [selectedHistorical, setSelectedHistorical] = useState<string | null>(null);
  const [historicalDetail, setHistoricalDetail] = useState<SectionState<HistoricalRunJobDetail>>({ phase: "loading" });
  const [selectedCurrent, setSelectedCurrent] = useState<string | null>(null);
  const [currentDetail, setCurrentDetail] = useState<SectionState<RankedJobOpportunity>>({ phase: "loading" });
  const [staleNotice, setStaleNotice] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [searchIntent, setSearchIntent] = useState<SearchIntent>(emptySearchIntent());
  const [selectedScheduleId, setSelectedScheduleId] = useState<string | null>(null);
  const [findMessage, setFindMessage] = useState("");
  const [findError, setFindError] = useState("");
  const [findRunOutcome, setFindRunOutcome] = useState<FindRunOutcome | null>(null);
  const [findRunning, setFindRunning] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [evaluationSnapshot, setEvaluationSnapshot] = useState<{ titles: string[]; query: CreateDiscoveryRun["query"] } | null>(null);
  const [exactEvaluation, setExactEvaluation] = useState<DiscoveryRunCreated | null>(null);
  const [evaluationMessage, setEvaluationMessage] = useState("");
  const [evaluationError, setEvaluationError] = useState("");
  const decisionMutator = useJobDecisionMutator();
  const submitLock = useRef(false);
  const alive = useRef(false);
  const onboardingRequest = useRef<number | null>(null);
  const opportunitiesRequest = useRef<number | null>(null);
  const runsRequest = useRef<number | null>(null);
  const generations = useRef({ onboarding: 0, opportunities: 0, runs: 0, inbox: 0, shortlisted: 0, dismissed: 0, savedSchedules: 0, scheduleHistory: 0, runDetail: 0, historicalDetail: 0, currentDetail: 0 });
  const userKey = user?.id ?? "";
  const sessionEpoch = api.sessionEpoch();
  const previousUserKey = useRef(userKey);
  const previousSessionEpoch = useRef(sessionEpoch);

  useEffect(() => {
    if (previousUserKey.current && (previousUserKey.current !== userKey || previousSessionEpoch.current !== sessionEpoch)) {
      setOnboarding(emptyPage()); setOpportunities(emptyPage()); setRuns(emptyPage()); setInbox(emptyPage()); setShortlisted(emptyPage()); setDismissed(emptyPage()); setSavedSchedules(emptyPage());
      setSelectedIds(new Set()); setSearchIntent(emptySearchIntent()); setSelectedScheduleId(null); setSelectedRun(null); setSelectedHistorical(null); setSelectedCurrent(null);
      setRunDetail(emptyPage()); setHistoricalDetail(emptyPage()); setCurrentDetail(emptyPage()); setEvaluationSnapshot(null); setExactEvaluation(null); setEvaluationMessage(""); setEvaluationError(""); setFindMessage(""); setFindError(""); setFindRunOutcome(null);
      setLastConfirmedInboxDismissal(null);
      setShortlistedLimit(WINDOW); setDismissedLimit(WINDOW); setShowDismissed(false);
    }
    generations.current.shortlisted += 1;
    generations.current.dismissed += 1;
    previousUserKey.current = userKey;
    previousSessionEpoch.current = sessionEpoch;
  }, [userKey, sessionEpoch]);

  const loadOnboarding = async () => {
    if (onboardingRequest.current !== null) return;
    const request = ++generations.current.onboarding;
    onboardingRequest.current = request;
    setOnboarding((previous) => ({ ...previous, phase: previous.data ? "loaded" : "loading", error: undefined }));
    try {
      const data = await api.request<OnboardingStatus>("/api/v1/onboarding/status");
      if (alive.current && request === generations.current.onboarding) setOnboarding({ phase: "loaded", data });
    } catch {
      if (alive.current && request === generations.current.onboarding) setOnboarding((previous) => ({ phase: previous.data ? "error" : "error", data: previous.data, error: "Candidate readiness is unavailable." }));
    } finally {
      if (onboardingRequest.current === request) onboardingRequest.current = null;
    }
  };
  const acceptOnboardingAuthority = (data: OnboardingStatus | undefined) => {
    generations.current.onboarding += 1;
    setOnboarding(data ? { phase: "loaded", data } : { phase: "error", error: "Candidate readiness is unavailable." });
  };
  const loadOpportunities = async (limit = opportunityLimit, clearExisting = false): Promise<boolean> => {
    if (opportunitiesRequest.current !== null && !clearExisting) return false;
    const request = ++generations.current.opportunities;
    opportunitiesRequest.current = request;
    setOpportunities((previous) => ({ ...previous, phase: previous.data && !clearExisting ? "loaded" : "loading", data: clearExisting ? undefined : previous.data, error: undefined }));
    try {
      const data = await api.request<BoundedResponse<UserOpportunitySummary>>(`/api/v1/jobs/opportunities?limit=${limit}`);
      if (alive.current && request === generations.current.opportunities) { setOpportunities({ phase: "loaded", data }); return true; }
      return false;
    } catch {
      if (alive.current && request === generations.current.opportunities) setOpportunities((previous) => ({ phase: "error", data: previous.data, error: "Current opportunities are unavailable." }));
      return false;
    } finally {
      if (opportunitiesRequest.current === request) opportunitiesRequest.current = null;
    }
  };
  const loadRuns = async (limit = runLimit): Promise<boolean> => {
    if (runsRequest.current !== null) return false;
    const request = ++generations.current.runs;
    runsRequest.current = request;
    setRuns((previous) => ({ ...previous, phase: previous.data ? "loaded" : "loading", error: undefined }));
    try {
      const data = await api.request<BoundedResponse<DiscoveryRunSummary>>(`/api/v1/jobs/discovery-runs?limit=${limit}`);
      if (alive.current && request === generations.current.runs) { setRuns({ phase: "loaded", data }); return true; }
      return false;
    } catch {
      if (alive.current && request === generations.current.runs) setRuns((previous) => ({ phase: "error", data: previous.data, error: "Discovery run history is unavailable." }));
      return false;
    } finally {
      if (runsRequest.current === request) runsRequest.current = null;
    }
  };
  const loadInbox = async (limit = inboxLimit): Promise<boolean> => {
    const request = ++generations.current.inbox;
    setInbox((previous) => ({ ...previous, phase: previous.data ? "loaded" : "loading", error: undefined }));
    try {
      const data = await api.request<BoundedResponse<InboxSummary>>(`/api/v1/jobs/inbox?limit=${limit}`);
      if (alive.current && request === generations.current.inbox) {
        setInbox({ phase: "loaded", data });
        const actionableIds = new Set(data.items.filter((item) => item.actionable).map((item) => item.discovered_job_id));
        setSelectedIds((current) => new Set(Array.from(current).filter((id) => actionableIds.has(id))));
        return true;
      }
      return false;
    } catch {
      if (alive.current && request === generations.current.inbox) setInbox((previous) => ({ phase: "error", data: previous.data, error: "Recent imported vacancies are unavailable." }));
      return false;
    }
  };
  const loadDecisionList = async (kind: "shortlisted" | "dismissed", limit: number, clearExisting = false): Promise<boolean> => {
    const request = ++generations.current[kind];
    const setter = kind === "shortlisted" ? setShortlisted : setDismissed;
    setter((previous) => ({ ...previous, phase: previous.data && !clearExisting ? "loaded" : "loading", data: clearExisting ? undefined : previous.data, error: undefined }));
    try {
      const data = await api.request<BoundedResponse<UserJobDecisionListItem>>(`/api/v1/jobs/decisions?decision=${kind}&limit=${limit}`);
      if (alive.current && request === generations.current[kind]) { setter({ phase: "loaded", data }); return true; }
    } catch {
      if (alive.current && request === generations.current[kind]) setter((previous) => ({ phase: "error", data: previous.data, error: `${kind === "shortlisted" ? "Shortlisted" : "Dismissed jobs"} are unavailable.` }));
    }
    return false;
  };
  const loadShortlisted = (limit = shortlistedLimit, clearExisting = false) => loadDecisionList("shortlisted", limit, clearExisting);
  const loadDismissed = (limit = dismissedLimit, clearExisting = false) => loadDecisionList("dismissed", limit, clearExisting);
  const mutateDecision = async (current: UserJobDecision | undefined, target: UserJobDecisionValue, surface: "inbox" | "recommended" | "shortlisted" | "dismissed") => {
    if (!current) return;
    const result = await decisionMutator.mutate(current, target);
    if (!result.confirmed || !result.decision || (result.kind !== "confirmed" && result.kind !== "reconciled")) return;
    const authoritative = result.decision;
    const reconciled = result.kind === "reconciled";
    if (surface === "inbox") {
      const item = inbox.data?.items.find((candidate) => candidate.discovered_job_id === current.discovered_job_id);
      const title = item?.title ?? lastConfirmedInboxDismissal?.title ?? "Job";
      if (authoritative.decision === "dismissed") {
        setSelectedIds((selected) => { const next = new Set(selected); next.delete(current.discovered_job_id); return next; });
        setInbox((previous) => previous.data ? { ...previous, data: { ...previous.data, items: previous.data.items.filter((candidate) => candidate.discovered_job_id !== current.discovered_job_id) } } : previous);
        setLastConfirmedInboxDismissal({ decision: authoritative, title, message: reconciled ? "Current decision is Dismissed." : "Job dismissed." });
        void loadInbox(inboxLimit);
      } else if (reconciled || target === "dismissed" || current.decision === "dismissed") {
        setLastConfirmedInboxDismissal(null);
        void loadInbox(inboxLimit);
      } else setInbox((previous) => previous.data ? { ...previous, data: { ...previous.data, items: previous.data.items.map((item) => item.discovered_job_id === current.discovered_job_id ? { ...item, decision: authoritative } : item) } } : previous);
    } else if (surface === "recommended") {
      if (authoritative.decision === "dismissed") void loadOpportunities(opportunityLimit, true);
      else setOpportunities((previous) => previous.data ? { ...previous, data: { ...previous.data, items: previous.data.items.map((item) => item.discovered_job_id === current.discovered_job_id ? { ...item, decision: authoritative } : item) } } : previous);
    } else if (surface === "shortlisted") {
      if (reconciled && authoritative.decision === "shortlisted") setShortlisted((previous) => previous.data ? { ...previous, data: { ...previous.data, items: previous.data.items.map((item) => item.discovered_job_id === current.discovered_job_id ? { ...item, decision: authoritative.decision, revision: authoritative.revision ?? item.revision, created_at: authoritative.created_at ?? item.created_at, updated_at: authoritative.updated_at ?? item.updated_at } : item) } } : previous);
      else {
        void loadShortlisted(shortlistedLimit, true);
        if (showDismissed || authoritative.decision === "dismissed") void loadDismissed(dismissedLimit, true);
      }
    } else {
      if (reconciled && authoritative.decision === "dismissed") setDismissed((previous) => previous.data ? { ...previous, data: { ...previous.data, items: previous.data.items.map((item) => item.discovered_job_id === current.discovered_job_id ? { ...item, decision: authoritative.decision, revision: authoritative.revision ?? item.revision, created_at: authoritative.created_at ?? item.created_at, updated_at: authoritative.updated_at ?? item.updated_at } : item) } } : previous);
      else {
        void loadDismissed(dismissedLimit, true);
        if (authoritative.decision === "shortlisted") void loadShortlisted(shortlistedLimit, true);
      }
    }
  };
  const undoInboxDismissal = async () => {
    const dismissal = lastConfirmedInboxDismissal;
    if (!dismissal) return;
    const result = await decisionMutator.mutate(dismissal.decision, "undecided");
    if (!result.confirmed || !result.decision || (result.kind !== "confirmed" && result.kind !== "reconciled")) return;
    if (result.decision.decision === "dismissed") setLastConfirmedInboxDismissal({ decision: result.decision, title: dismissal.title, message: "Current decision is Dismissed." });
    else setLastConfirmedInboxDismissal(null);
    void loadInbox(inboxLimit);
  };
  const reconcileFindHistory = async (scheduleId: string): Promise<DiscoveryRunReconciliation<ScheduledExecutionRead[]>> => {
    const request = ++generations.current.scheduleHistory;
    try {
      const data = await api.request<ScheduledExecutionRead[]>(`/api/v1/jobs/discovery-schedules/${encodeURIComponent(scheduleId)}/executions`);
      if (!alive.current) return { kind: "session_stale" };
      if (request !== generations.current.scheduleHistory) return { kind: "superseded" };
      return { kind: "refreshed", value: data };
    } catch (error) {
      if (!alive.current) return { kind: "session_stale" };
      if (request !== generations.current.scheduleHistory) return { kind: "superseded" };
      if (error instanceof ApiError && error.status === 404) return { kind: "stale" };
      return { kind: "failed" };
    }
  };
  const loadSavedSchedules = async (): Promise<boolean> => {
    const request = ++generations.current.savedSchedules;
    setSavedSchedules((previous) => ({ ...previous, phase: previous.data ? "loaded" : "loading", error: undefined }));
    try {
      const data = await api.request<DiscoveryScheduleRead[]>("/api/v1/jobs/discovery-schedules");
      if (alive.current && request === generations.current.savedSchedules) {
        setSavedSchedules({ phase: "loaded", data });
        if (selectedScheduleId && !data.some((item) => item.id === selectedScheduleId)) setSelectedScheduleId(null);
        return true;
      }
      return false;
    } catch {
      if (alive.current && request === generations.current.savedSchedules) setSavedSchedules((previous) => ({ phase: "error", data: previous.data, error: "Saved discovery configurations are unavailable." }));
      return false;
    }
  };
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; for (const key of Object.keys(generations.current) as Array<keyof typeof generations.current>) generations.current[key] += 1; };
  }, [api, userKey]);

  useEffect(() => {
    if (view === "find") { if (!onboarding.data) void loadOnboarding(); void loadSavedSchedules(); }
    else if (view === "inbox") { if (!onboarding.data) void loadOnboarding(); void loadInbox(WINDOW); }
    else if (view === "recommended") { if (!onboarding.data) void loadOnboarding(); void loadOpportunities(WINDOW); }
    else if (view === "shortlisted") void loadShortlisted(WINDOW);
    else if (view === "history") void loadRuns(WINDOW);
  }, [view, userKey, sessionEpoch]);
  useEffect(() => { if (view === "shortlisted" && showDismissed) void loadDismissed(dismissedLimit); }, [view, showDismissed]);
  useEffect(() => {
    const refresh = () => {
      if (view === "recommended") void loadOpportunities(opportunityLimit, true);
      if (view === "inbox") void loadInbox(inboxLimit);
    };
    window.addEventListener("career-trans-job-decision-changed", refresh);
    return () => window.removeEventListener("career-trans-job-decision-changed", refresh);
  }, [view, inboxLimit, opportunityLimit]);

  const openCurrent = async (evaluationId: string) => {
    const request = ++generations.current.currentDetail;
    setSelectedCurrent(evaluationId); setCurrentDetail({ phase: "loading" }); setStaleNotice("");
    try {
      const data = await api.request<RankedJobOpportunity>(`/api/v1/jobs/opportunities/${encodeURIComponent(evaluationId)}`);
      if (alive.current && request === generations.current.currentDetail) setCurrentDetail({ phase: "loaded", data });
    } catch (error) {
      if (!alive.current || request !== generations.current.currentDetail) return;
      if (error instanceof ApiError && error.status === 404) {
        setSelectedCurrent(null); setCurrentDetail({ phase: "error", error: "This opportunity is no longer current. The current opportunity view has been refreshed." });
        setStaleNotice("This opportunity is no longer current. The current opportunity view has been refreshed.");
        void loadOpportunities(WINDOW); setOpportunityLimit(WINDOW);
      } else setCurrentDetail({ phase: "error", error: "Opportunity detail is unavailable." });
    }
  };
  const openRun = async (runId: string, syncUrl = true) => {
    if (syncUrl) navigate(`/jobs/history?run=${encodeURIComponent(runId)}`);
    const request = ++generations.current.runDetail;
    setSelectedRun(runId); setRunDetail({ phase: "loading" }); setSelectedHistorical(null); setHistoricalDetail({ phase: "loading" });
    try {
      const data = await api.request<DiscoveryRunDetail>(`/api/v1/jobs/discovery-runs/${encodeURIComponent(runId)}`);
      if (alive.current && request === generations.current.runDetail) setRunDetail({ phase: "loaded", data });
    } catch {
      if (alive.current && request === generations.current.runDetail) setRunDetail({ phase: "error", error: "Run detail is unavailable." });
    }
  };
  const openHistorical = async (runId: string, jobId: string, syncUrl = true) => {
    if (syncUrl) navigate(`/jobs/history?run=${encodeURIComponent(runId)}&job=${encodeURIComponent(jobId)}`);
    const request = ++generations.current.historicalDetail;
    const key = `${runId}:${jobId}`;
    setSelectedHistorical(key); setHistoricalDetail({ phase: "loading" });
    try {
      const data = await api.request<HistoricalRunJobDetail>(`/api/v1/jobs/discovery-runs/${encodeURIComponent(runId)}/jobs/${encodeURIComponent(jobId)}`);
      if (alive.current && request === generations.current.historicalDetail) setHistoricalDetail({ phase: "loaded", data });
    } catch {
      if (alive.current && request === generations.current.historicalDetail) setHistoricalDetail({ phase: "error", error: "Historical detail is unavailable." });
    }
  };
  const closeSelectedRun = () => {
    generations.current.runDetail += 1;
    generations.current.historicalDetail += 1;
    setSelectedRun(null);
    setSelectedHistorical(null);
    setRunDetail({ phase: "loading" });
    setHistoricalDetail({ phase: "loading" });
    navigate("/jobs/history", { replace: true });
  };

  useEffect(() => {
    if (view !== "history") return;
    const params = new URLSearchParams(location.search);
    const runId = params.get("run");
    const jobId = params.get("job");
    if (!runId && jobId) {
      navigate("/jobs/history", { replace: true });
      setSelectedRun(null); setSelectedHistorical(null); setRunDetail({ phase: "loading" }); setHistoricalDetail({ phase: "loading" });
      return;
    }
    if (!runId) {
      if (selectedRun) { generations.current.runDetail += 1; generations.current.historicalDetail += 1; setSelectedRun(null); setSelectedHistorical(null); }
      return;
    }
    if (selectedRun !== runId) void openRun(runId, false);
    if (!jobId && selectedHistorical) { generations.current.historicalDetail += 1; setSelectedHistorical(null); setHistoricalDetail({ phase: "loading" }); }
    if (jobId && selectedHistorical !== `${runId}:${jobId}`) void openHistorical(runId, jobId, false);
  }, [view, location.search, userKey]);
  const toggleJob = (item: InboxSummary) => {
    if (!item.actionable) return;
    setSelectedIds((current) => { const next = new Set(current); if (next.has(item.discovered_job_id)) next.delete(item.discovered_job_id); else next.add(item.discovered_job_id); return next; });
  };
  const selectedSchedule = savedSchedules.data?.find((schedule) => schedule.id === selectedScheduleId);
  const selectedScheduleIntent = selectedSchedule ? searchIntentFromQuery(selectedSchedule.query) : null;
  const selectedScheduleDirty = !!selectedScheduleIntent && !searchIntentEquals(searchIntent, selectedScheduleIntent);
  const handoffSearchIntent = () => navigate("/jobs/find/saved", { state: { searchIntent, ...(selectedSchedule ? { scheduleId: selectedSchedule.id } : {}) } });
  const runSelectedSchedule = async () => {
    const schedule = selectedSchedule;
    if (!schedule || !ready || selectedScheduleDirty || findRunning) return;
    setFindRunning(true); setFindMessage(""); setFindError(""); setFindRunOutcome(null);
    try {
      const result = await runSavedDiscoveryNowWithReconciliation(api, schedule, { reconcile: () => reconcileFindHistory(schedule.id) });
      if (!alive.current) return;
      if (result.kind === "changed") {
        setSavedSchedules((old) => old.data ? { ...old, phase: "loaded", data: old.data.map((item) => item.id === result.schedule.id ? result.schedule : item) } : old);
        setSearchIntent(searchIntentFromQuery(result.schedule.query));
        setFindRunOutcome({ kind: "changed", scheduleName: schedule.name });
        setFindMessage("The saved configuration changed before Run now started. The SearchIntent and persisted channels were refreshed; review them and explicitly run again. No execution was submitted.");
      } else if (result.kind === "preflight_stale" || result.kind === "post_stale") {
        const refreshed = await loadSavedSchedules();
        setFindRunOutcome({ kind: result.kind, scheduleName: schedule.name });
        setFindError(refreshed ? "The selected saved configuration is no longer available. Choose another configuration." : "The selected saved configuration is no longer available, and its refresh could not be confirmed.");
        setSelectedScheduleId(null);
      } else if (result.kind === "preflight_rejected") {
        setFindRunOutcome({ kind: "preflight_rejected", scheduleName: schedule.name, status: result.status });
        setFindError(`Could not verify the current saved configuration (HTTP ${result.status}). Run now was not submitted.`);
      } else if (result.kind === "preflight_unavailable") {
        setFindRunOutcome({ kind: "preflight_unavailable", scheduleName: schedule.name });
        setFindError("Could not confirm the current saved configuration. Run now was not submitted.");
      } else if (result.kind === "post_rejected") {
        setFindRunOutcome({ kind: "post_rejected", scheduleName: schedule.name, status: result.status });
        setFindError(`Run now was rejected by the server (HTTP ${result.status}). No execution success was confirmed.`);
      } else if (result.kind === "already_running" || result.kind === "uncertain") {
        if (result.reconciliation.kind === "stale") {
          const refreshed = await loadSavedSchedules();
          setSelectedScheduleId(null);
          setFindRunOutcome({ kind: "reconciliation_stale", scheduleName: schedule.name, source: result.kind });
          setFindError(refreshed ? "The selected saved configuration is no longer available. Choose another configuration." : "The selected saved configuration is no longer available, and its refresh could not be confirmed.");
        } else setFindRunOutcome({ kind: result.kind, scheduleName: schedule.name, reconciliation: result.reconciliation });
      } else {
        setSavedSchedules((old) => old.data ? { ...old, phase: "loaded", data: old.data.map((item) => item.id === result.schedule.id ? result.schedule : item) } : old);
        let inboxRefresh: boolean | undefined;
        if (result.execution.status === "completed" || result.execution.status === "partial_failed") {
          const [refreshedInbox] = await Promise.all([loadInbox(WINDOW), loadRuns(runLimit)]);
          inboxRefresh = refreshedInbox;
        }
        setFindRunOutcome({ kind: "execution", scheduleName: schedule.name, status: result.execution.status, inboxRefresh });
      }
    } finally { if (alive.current) setFindRunning(false); }
  };
  const submitEvaluation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitLock.current || submitting || !onboarding.data?.candidate_context_ready || selectedIds.size === 0 || searchIntent.themes.length === 0) return;
    const query = searchIntentToQuery(searchIntent);
    const ids = Array.from(selectedIds);
    const titles = inbox.data?.items.filter((item) => selectedIds.has(item.discovered_job_id)).map((item) => item.title) ?? [];
    const payload: CreateDiscoveryRun = { query, discovered_job_ids: ids, max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 };
    submitLock.current = true; setSubmitting(true); setEvaluationSnapshot({ titles, query }); setExactEvaluation(null); setEvaluationMessage(""); setEvaluationError("");
    try {
      const result = await api.request<DiscoveryRunCreated>("/api/v1/jobs/discovery-runs", { method: "POST", body: JSON.stringify(payload) });
      setExactEvaluation(result);
      setEvaluationMessage("Evaluation completed. Refreshing recent runs and current opportunities…");
      setSelectedIds(new Set()); setOpportunityLimit(WINDOW);
      const [runsRefreshed, opportunitiesRefreshed] = await Promise.all([loadRuns(runLimit), loadOpportunities(WINDOW, true)]);
      setEvaluationMessage(`Evaluation completed. Recent runs ${runsRefreshed ? "were refreshed" : "could not be confirmed as refreshed"}; current opportunities ${opportunitiesRefreshed ? "were refreshed" : "could not be confirmed as refreshed"}.`);
    } catch (error) {
      const runsRefreshed = await loadRuns(runLimit);
      setEvaluationError(error instanceof ApiError
        ? `Career-trans returned an error while creating the evaluation. ${runsRefreshed ? "Recent run history has been refreshed." : "Recent run history could not be confirmed as refreshed."}`
        : `The evaluation request was interrupted. Career-trans cannot confirm from this response whether the run started. ${runsRefreshed ? "Recent run history has been refreshed." : "Recent run history could not be confirmed as refreshed."}`);
    } finally { submitLock.current = false; if (alive.current) setSubmitting(false); }
  };

  const ready = onboarding.data?.candidate_context_ready === true;
  const noRuns = runs.data?.items.length === 0 && runs.phase === "loaded";
  const confirmedWindow = opportunities.data?.items ?? [];

  if (route.kind === "unknown") { navigate("/jobs/find", { replace: true }); return null; }
  return <main className="jobs-page">
    <header className="workspace-header jobs-header"><div><p className="eyebrow">Career workspace</p><h1>Job Search</h1><p className="muted">Find, evaluate, and review job discovery information in one route family.</p>{view !== "saved" && <Link to="/jobs/find/saved">Manage saved discovery configurations</Link>}</div></header>
    <JobSearchNavigation view={view} />
    {route.kind === "workspace" ? <JobWorkspacePage discoveredJobId={route.discoveredJobId} section={route.section} /> : <>
    {view === "saved" && <JobsSearchesPage />}
    {view !== "saved" && <>
    {(view === "find" || view === "inbox" || view === "recommended") && <section className="jobs-prerequisite card" aria-label="Candidate readiness">
      {onboarding.phase === "loading" && !onboarding.data ? <p role="status">Checking candidate readiness…</p> : onboarding.data ? <>
        {!ready && <><h2>Complete your Profile first</h2><p>Confirm structured career information in Profile or confirm a CV to create the candidate context used for evaluation.</p><Link to="/profile">Review Profile</Link></>}
        {ready && onboarding.data.latest_cv_draft && onboarding.data.latest_cv_draft.state !== "confirmed" && <p className="notice">Evaluations use your current confirmed candidate profile. A newer CV update is awaiting review.</p>}
        {ready && onboarding.data.adviser.assessment_status !== "confirmed" && <p className="muted">Career Adviser completion is optional. Saved Adviser intake may already inform your evaluation context.</p>}
      </> : <div><p role="alert">Candidate readiness is unavailable. Evaluation controls remain hidden until it can be confirmed.</p><button type="button" className="button-secondary" onClick={() => void loadOnboarding()}>Retry readiness</button></div>}
    </section>}
    {evaluationMessage && <p className="notice" role="status">{evaluationMessage}</p>}{evaluationError && <p className="notice" role="status">{evaluationError}</p>}{staleNotice && <p className="notice" role="status">{staleNotice}</p>}
    {evaluationSnapshot && submitting && <section className="card evaluation-snapshot"><h2>Evaluation in progress</h2><p role="status">Evaluating selected jobs… this may take several minutes.</p><p>Selected jobs: {evaluationSnapshot.titles.join(", ") || "Selection submitted"}</p><p>Search themes: {evaluationSnapshot.query.keywords.join(", ")} · locations: {evaluationSnapshot.query.locations.join(", ") || "Any"} · remote policy: {evaluationSnapshot.query.remote_ok === false ? "Exclude remote jobs" : "No remote restriction"}</p></section>}
    {exactEvaluation && <ExactEvaluationResult run={exactEvaluation} fallbackQuery={evaluationSnapshot?.query} />}
    {evaluationSnapshot && !submitting && <section className="card evaluation-snapshot"><h2>Submitted evaluation</h2><p>Selected jobs: {evaluationSnapshot.titles.join(", ") || "Selection submitted"}</p><p>Search themes: {evaluationSnapshot.query.keywords.join(", ")} · locations: {evaluationSnapshot.query.locations.join(", ") || "Any"} · remote policy: {evaluationSnapshot.query.remote_ok === false ? "Exclude remote jobs" : "No remote restriction"}</p></section>}
    <div className="jobs-content">
      {view === "find" && <section aria-labelledby="find-jobs-heading" className="jobs-section"><div className="section-heading"><div><h2 id="find-jobs-heading">Find jobs</h2><p className="muted">Build a SearchIntent for discovery and prioritisation. Search context is not eligibility, evidence, or proof of fit.</p></div></div>
        {savedSchedules.phase === "loading" && !savedSchedules.data && <p className="muted" role="status">Loading saved configurations…</p>}
        {savedSchedules.phase === "error" && !savedSchedules.data && <p className="notice" role="status">Saved configurations are unavailable. You can still prepare a transient SearchIntent.</p>}
        <label htmlFor="saved-search-selection">Saved search configuration</label><select id="saved-search-selection" aria-label="Saved search configuration" value={selectedScheduleId ?? ""} onChange={(event) => { const id = event.target.value || null; setSelectedScheduleId(id); const schedule = savedSchedules.data?.find((item) => item.id === id); if (schedule) setSearchIntent(searchIntentFromQuery(schedule.query)); setFindMessage(""); setFindError(""); }}><option value="">Use a transient SearchIntent</option>{savedSchedules.data?.map((schedule) => <option value={schedule.id} key={schedule.id}>{schedule.name}</option>)}</select>
        <SearchIntentEditor intent={searchIntent} onChange={(next) => { setSearchIntent(next); setFindMessage(""); setFindError(""); }} idPrefix="find-search-intent" disabled={findRunning || submitting} />
        {selectedSchedule && <div className="card"><p><strong>Persisted channels:</strong> {selectedSchedule.acquisition.structured_ats.enabled ? "Structured ATS" : ""}{selectedSchedule.acquisition.structured_ats.enabled && selectedSchedule.acquisition.agentic_web.enabled ? " · " : ""}{selectedSchedule.acquisition.agentic_web.enabled ? "Profile-driven bounded server-side web discovery" : ""}</p><p><strong>Evaluation:</strong> {selectedSchedule.evaluation.max_semantic_candidates} semantic candidates · {selectedSchedule.evaluation.max_full_analyses} full analyses · minimum relevance {selectedSchedule.evaluation.min_relevance_score}</p><p className="muted">Candidate readiness controls whether the persisted schedule can run. Semantic configuration is advisory and is not provider readiness.</p>{selectedScheduleDirty && <p className="notice">This SearchIntent differs from the persisted saved configuration. Hand it off to Saved searches to review and save; it is explicitly dirty.</p>}</div>}
        {findMessage && <p className="notice" role="status">{findMessage}</p>}{findError && <p className="notice" role="status">{findError}</p>}
        {findRunOutcome && <section className="card" aria-label="Run now result"><h3>Run now result</h3>
          {findRunOutcome.kind === "execution" && <><p role="status"><strong>Saved discovery:</strong> {findRunOutcome.scheduleName} · <strong>Status:</strong> {titleCase(findRunOutcome.status)}</p>{(findRunOutcome.status === "completed" || findRunOutcome.status === "partial_failed") && <><p>Recent vacancies refresh: {findRunOutcome.inboxRefresh ? "refreshed" : "could not be confirmed as refreshed"}.</p><p className="muted">Recent vacancies is a shared persisted slice, not an exact execution result set.</p><Link className="button-secondary" to="/jobs/inbox">Review recent vacancies</Link></>}</>}
          {findRunOutcome.kind === "changed" && <p role="status">The saved configuration changed before execution started. No execution was submitted.</p>}
          {findRunOutcome.kind === "preflight_stale" && <p role="status">The saved configuration is no longer available. Run now was not submitted.</p>}
          {findRunOutcome.kind === "post_stale" && <p role="status">The saved configuration became unavailable after Run now was submitted. Career-trans cannot confirm the resulting state.</p>}
          {findRunOutcome.kind === "reconciliation_stale" && <><p role="status">The saved configuration became unavailable while reconciling this run. Career-trans cannot use its saved execution history to confirm the resulting state.</p>{findRunOutcome.source === "already_running" && <p>The server had reported that an execution was already running before the saved configuration became unavailable during reconciliation.</p>}{findRunOutcome.source === "uncertain" && <p>Career-trans cannot confirm whether a new execution was created.</p>}</>}
          {findRunOutcome.kind === "preflight_rejected" && <p role="status">Could not verify the current saved configuration (HTTP {findRunOutcome.status}). Run now was not submitted.</p>}
          {findRunOutcome.kind === "preflight_unavailable" && <p role="status">Could not confirm the current saved configuration. Run now was not submitted.</p>}
          {findRunOutcome.kind === "post_rejected" && <p role="status">The server rejected Run now with HTTP {findRunOutcome.status}. No execution success was confirmed.</p>}
          {(findRunOutcome.kind === "already_running" || findRunOutcome.kind === "uncertain") && <><p role="status"><strong>Saved discovery:</strong> {findRunOutcome.scheduleName} · <strong>Status:</strong> {findRunOutcome.kind === "already_running" ? "Already running" : "Uncertain"}</p><p>Execution history reconciliation: {findRunOutcome.reconciliation.kind === "refreshed" ? "refreshed" : findRunOutcome.reconciliation.kind === "stale" ? "saved configuration was no longer available" : findRunOutcome.reconciliation.kind === "superseded" ? "superseded by a newer refresh" : findRunOutcome.reconciliation.kind === "session_stale" ? "session became stale" : "could not be confirmed as refreshed"}.</p>{findRunOutcome.kind === "uncertain" && <p className="muted">Career-trans cannot confirm from the interrupted response whether a new execution was created.</p>}</>}
        </section>}
        <div className="card-actions"><button type="button" onClick={handoffSearchIntent}>{selectedSchedule ? "Review or save configuration" : "Save or configure search"}</button>{selectedSchedule && <button type="button" className="button-secondary" onClick={() => void runSelectedSchedule()} disabled={!ready || selectedScheduleDirty || findRunning}>{findRunning ? "Running…" : "Run now"}</button>}</div>
        {!ready && <p className="muted">Run now requires confirmed candidate context. A transient SearchIntent can still be reviewed or saved.</p>}
        <section className="card" aria-label="Host Codex guidance"><h3>Run with Codex on this device</h3><p>SearchIntent is not automatically transferred to local Codex. Codex runs locally outside the browser and the current CLI supports only a subset of this search context.</p><p>Excluded companies, excluded title terms, and employment types are not claimed to be applied by that local workflow. Imported bounded results may appear in shared Recent vacancies; a run not completed here is not a completed run with zero results.</p></section>
      </section>}
      {view === "recommended" && <section aria-labelledby="opportunities-heading" className="jobs-section"><OpportunitiesNavigation view={view} /><div className="section-heading"><div><h2 id="opportunities-heading">Recommended / Current analyses</h2><p className="muted">Ordered by current backend analyses. Posting recency is a signal, not proof the vacancy is live.</p></div><button type="button" className="button-secondary" onClick={() => void loadOpportunities()}>Refresh</button></div>
        <StateMessage state={opportunities} empty={false} onRetry={() => void loadOpportunities()}>
          {!confirmedWindow.length && opportunities.phase === "loaded" && <><p className="muted">No current evaluated opportunities.</p><Link to="/jobs/history">View Search history</Link></>}
          {!!confirmedWindow.length && <ol className="opportunity-list">{confirmedWindow.map((item, index) => { const context = jobContextLabel(item.title, item.company, item.location, `result ${index + 1}`); return <li className="card opportunity-card" key={item.discovered_job_id}><div className="opportunity-top"><div><p className="recommendation-label">{item.recommendation.toUpperCase()}</p><h3>{item.title}</h3><p>{[item.company, item.location, item.work_arrangement].filter(Boolean).join(" · ")}</p></div><span className="ordinal">{index + 1}</span></div><dl className="metric-grid"><div><dt>Fit</dt><dd>{numberLabel(item.fit_score)}</dd></div><div><dt>Career alignment</dt><dd>{numberLabel(item.career_alignment_score)} · {titleCase(item.career_alignment_confidence)}</dd></div><div><dt>Relevance</dt><dd>{numberLabel(item.relevance_score * 100)}%</dd></div><div><dt>Role archetype</dt><dd>{titleCase(item.archetype)}</dd></div></dl><p><strong>Posting recency signal:</strong> {titleCase(item.posting_recency.legitimacy)} — {item.posting_recency.reasoning}</p><div className="card-actions"><Link className="button-secondary" aria-label={`Open workspace for ${context}`} to={`/jobs/${encodeURIComponent(item.discovered_job_id)}`}>Open workspace</Link><Link className="button-secondary" aria-label={`View Fit for ${context}`} to={`/jobs/${encodeURIComponent(item.discovered_job_id)}/fit`}>View Fit</Link><a aria-label={`Open vacancy for ${context}`} href={item.url} target="_blank" rel="noopener noreferrer">Open vacancy</a><button type="button" className="button-secondary" aria-label={`${selectedCurrent === item.evaluation_id ? "Close" : "View"} detail for ${context}`} aria-expanded={selectedCurrent === item.evaluation_id} onClick={() => selectedCurrent === item.evaluation_id ? (generations.current.currentDetail += 1, setSelectedCurrent(null)) : void openCurrent(item.evaluation_id)}>{selectedCurrent === item.evaluation_id ? "Close detail" : "View detail"}</button></div><OpportunityPreparation opportunity={item} ready={onboarding.data?.candidate_context_ready} context={context} onUnavailable={async () => { const refreshed = await loadOpportunities(WINDOW); setOpportunityLimit(WINDOW); setStaleNotice(refreshed ? "This opportunity is no longer available. The current opportunity view was refreshed." : "This opportunity refresh could not be confirmed."); return refreshed; }} onReadinessRefresh={acceptOnboardingAuthority} />{selectedCurrent === item.evaluation_id && <StateMessage state={currentDetail} empty={false} onRetry={() => void openCurrent(item.evaluation_id)}>{currentDetail.data && <OpportunityDetail opportunity={currentDetail.data} />}</StateMessage>}</li>; })}</ol>}
          {opportunities.data?.truncated && (opportunityLimit < MAX_WINDOW ? <button type="button" className="button-secondary" onClick={() => { const next = nextWindow(opportunityLimit); setOpportunityLimit(next); void loadOpportunities(next); }}>Show more current opportunities</button> : <p className="muted">Showing the first 100 current opportunities available through this view.</p>)}
        </StateMessage>
      </section>}
      {view === "history" && <section aria-labelledby="runs-heading" className="jobs-section"><div className="section-heading"><div><h2 id="runs-heading">Search history</h2><p className="muted">Recent discovery runs and their stored query/funnel summaries.</p></div><button type="button" className="button-secondary" onClick={() => void loadRuns()}>Refresh</button></div>
        <StateMessage state={runs} empty={false} onRetry={() => void loadRuns()}>{noRuns && <p className="muted">No discovery runs yet.</p>}{!!runs.data?.items.length && <ol className="run-list">{runs.data.items.map((run) => <li className="card run-card" key={run.id}><div className="section-heading"><div><h3>{run.status === "running" ? "Evaluation in progress" : titleCase(run.status)}</h3><p>Started {new Date(run.started_at).toLocaleString()}{run.completed_at ? ` · completed ${new Date(run.completed_at).toLocaleString()}` : ""}</p></div><button type="button" className="button-secondary" aria-label={`${selectedRun === run.id ? "Close run" : "View run"} for ${runContextLabel(run)}`} aria-expanded={selectedRun === run.id} onClick={() => selectedRun === run.id ? closeSelectedRun() : void openRun(run.id)}>{selectedRun === run.id ? "Close run" : "View run"}</button></div><RunSnapshot run={run} />{selectedRun === run.id && <StateMessage state={runDetail} empty={false} onRetry={() => void openRun(run.id)}>{runDetail.data && <RunRows run={runDetail.data} onHistorical={(jobId) => selectedHistorical === `${run.id}:${jobId}` ? (navigate(`/jobs/history?run=${encodeURIComponent(run.id)}`, { replace: true }), setSelectedHistorical(null)) : void openHistorical(run.id, jobId)} selectedHistorical={selectedHistorical} historicalDetail={historicalDetail} onRetryHistorical={(jobId) => void openHistorical(run.id, jobId)} />}</StateMessage>}</li>)}</ol>}{selectedRun && runs.data && !runs.data.items.some((item) => item.id === selectedRun) && <section className="card run-card" aria-label="Selected search history run"><div className="section-heading"><div><h3>{runDetail.data ? (runDetail.data.status === "running" ? "Evaluation in progress" : titleCase(runDetail.data.status)) : "Selected search history run"}</h3></div><button type="button" className="button-secondary" aria-label={`Close run for ${runDetail.data ? runContextLabel(runDetail.data) : "selected search history run"}`} aria-expanded="true" onClick={closeSelectedRun}>Close run</button></div>{runDetail.data ? <><RunSnapshot run={runDetail.data} /><RunRows run={runDetail.data} onHistorical={(jobId) => selectedHistorical === `${selectedRun}:${jobId}` ? (navigate(`/jobs/history?run=${encodeURIComponent(selectedRun)}`, { replace: true }), setSelectedHistorical(null)) : void openHistorical(selectedRun, jobId)} selectedHistorical={selectedHistorical} historicalDetail={historicalDetail} onRetryHistorical={(jobId) => void openHistorical(selectedRun, jobId)} /></> : <StateMessage state={runDetail} empty={false} onRetry={() => void openRun(selectedRun)}>{null}</StateMessage>}</section>}{runs.data?.truncated && (runLimit < MAX_WINDOW ? <button type="button" className="button-secondary" onClick={() => { const next = nextWindow(runLimit); setRunLimit(next); void loadRuns(next); }}>Show more runs</button> : <p className="muted">Showing the first 100 discovery runs available through this view.</p>)}</StateMessage>
        {selectedRun && !runs.data && <section className="card run-card" aria-label="Selected search history run"><div className="section-heading"><div><h3>{runDetail.data ? (runDetail.data.status === "running" ? "Evaluation in progress" : titleCase(runDetail.data.status)) : "Selected search history run"}</h3></div><button type="button" className="button-secondary" aria-label={`Close run for ${runDetail.data ? runContextLabel(runDetail.data) : "selected search history run"}`} aria-expanded="true" onClick={closeSelectedRun}>Close run</button></div>{runDetail.data ? <><RunSnapshot run={runDetail.data} /><RunRows run={runDetail.data} onHistorical={(jobId) => selectedHistorical === `${selectedRun}:${jobId}` ? (navigate(`/jobs/history?run=${encodeURIComponent(selectedRun)}`, { replace: true }), setSelectedHistorical(null)) : void openHistorical(selectedRun, jobId)} selectedHistorical={selectedHistorical} historicalDetail={historicalDetail} onRetryHistorical={(jobId) => void openHistorical(selectedRun, jobId)} /></> : <StateMessage state={runDetail} empty={false} onRetry={() => void openRun(selectedRun)}>{null}</StateMessage>}</section>}
      </section>}
      {view === "inbox" && <section aria-labelledby="inbox-heading" className="jobs-section"><div className="section-heading"><div><h2 id="inbox-heading">Inbox</h2><p className="muted">Showing recent shared persisted public vacancies. This is not all jobs, a live search, or a shortlist.</p></div><button type="button" className="button-secondary" onClick={() => void loadInbox()}>Refresh</button></div>
        {lastConfirmedInboxDismissal && <p className="notice" role="status">{lastConfirmedInboxDismissal.message} <button type="button" className="button-secondary" onClick={() => void undoInboxDismissal()}>Undo</button></p>}
        <StateMessage state={inbox} empty={false} onRetry={() => void loadInbox()}>
          {inbox.phase === "loaded" && inbox.data?.items.length === 0 && <p className="muted">No recently imported public vacancies are available.</p>}
          {!!inbox.data?.items.length && <>
            <ul className="inbox-list">{inbox.data.items.map((item) => { const context = jobContextLabel(item.title, item.company, item.location, `last seen ${dateLabel(item.last_seen_at)}`); return <li className={`card inbox-card${selectedIds.has(item.discovered_job_id) ? " is-selected" : ""}`} key={item.discovered_job_id}><label className="selection-label"><input type="checkbox" checked={selectedIds.has(item.discovered_job_id)} disabled={!item.actionable || !ready || submitting} onChange={() => toggleJob(item)} aria-label={`Select ${context}`} /><span>{item.title}</span></label><p>{[item.company, item.location, item.work_arrangement, item.employment_type].filter(Boolean).join(" · ") || "Details not provided"}</p><p>Lifecycle: {titleCase(item.state)} · Verification: {titleCase(item.verification_status)} · {item.actionable ? "Actionable" : "Not actionable"}</p>{item.verification_reason && <p>Verification note: {titleCase(item.verification_reason)}</p>}<p>Last seen: {new Date(item.last_seen_at).toLocaleString()}</p>{item.provenance.length > 0 && <p>Recent provenance: {item.provenance.map((source) => `${source.runtime}${source.discovered_via ? ` · ${source.discovered_via}` : ""}`).join("; ")}</p>}<div className="card-actions"><Link className="button-secondary" aria-label={`Open workspace for ${context}`} to={`/jobs/${encodeURIComponent(item.discovered_job_id)}`}>Open workspace</Link><Link className="button-secondary" aria-label={`View Fit for ${context}`} to={`/jobs/${encodeURIComponent(item.discovered_job_id)}/fit`}>View Fit</Link><a aria-label={`Open vacancy for ${context}`} href={item.url} target="_blank" rel="noopener noreferrer">Open vacancy</a></div><DecisionControls decision={item.decision} context={context} disabled={decisionMutator.pending.has(item.discovered_job_id)} onMutate={(target) => void mutateDecision(item.decision, target, "inbox")} />{decisionMutator.notices[item.discovered_job_id] && <p className="notice" role="status">{decisionMutator.notices[item.discovered_job_id]}</p>}</li>; })}</ul>
            {inbox.data.truncated && (inboxLimit < MAX_WINDOW ? <button type="button" className="button-secondary" onClick={() => { const next = nextWindow(inboxLimit); setInboxLimit(next); void loadInbox(next); }}>Show more recent vacancies</button> : <p className="muted">Showing the first 100 recent vacancies available through this view.</p>)}
          </>}
          {ready && <form className="card evaluation-form" onSubmit={(event) => void submitEvaluation(event)}><h3>Evaluate selected actionable jobs</h3><p className="muted">This evaluates persisted vacancies with the same explicit SearchIntent used by Find jobs. It does not start internet discovery.</p><p><strong>SearchIntent:</strong> {searchIntent.themes.join(", ") || "Not set"} · locations: {searchIntent.locations.join(", ") || "Any"} · remote policy: {searchIntent.remotePolicy === "exclude_remote" ? "Exclude remote jobs" : searchIntent.remotePolicy === "legacy_true" ? "No remote restriction (legacy stored value)" : "No remote restriction"}</p><p className="muted">SearchIntent is discovery context, not eligibility or evidence. <Link className="button-secondary" to="/jobs/find">Edit SearchIntent in Find jobs</Link></p><button type="submit" disabled={submitting || selectedIds.size === 0 || searchIntent.themes.length === 0}>{submitting ? "Evaluating…" : `Evaluate ${selectedIds.size || "selected"} jobs`}</button>{!searchIntent.themes.length && <p className="muted">Set an explicit SearchIntent in Find jobs before evaluating.</p>}{!selectedIds.size && <p className="muted">Select one or more actionable vacancies to continue.</p>}</form>}
        </StateMessage>
      </section>}
      {view === "shortlisted" && <section aria-labelledby="shortlisted-heading" className="jobs-section"><OpportunitiesNavigation view={view} /><div className="section-heading"><div><h2 id="shortlisted-heading">Shortlisted</h2><p className="muted">Jobs you explicitly shortlisted. This list is independent of recommendations, Fit, and evaluation history.</p></div><button type="button" className="button-secondary" onClick={() => void loadShortlisted()}>Refresh</button></div><StateMessage state={shortlisted} empty={false} onRetry={() => void loadShortlisted()}>{shortlisted.phase === "loaded" && shortlisted.data?.items.length === 0 && <p className="muted">No shortlisted jobs yet.</p>}{!!shortlisted.data?.items.length && <><ul className="opportunity-list">{shortlisted.data.items.map((item) => { const context = jobContextLabel(item.title, item.company, item.location, `decision updated ${dateLabel(item.updated_at)}`); return <li className="card opportunity-card" key={item.discovered_job_id}><h3>{item.title}</h3><p>{[item.company, item.location, item.work_arrangement].filter(Boolean).join(" · ") || "Details not provided"}</p><p>Decision updated {item.updated_at ? new Date(item.updated_at).toLocaleString() : "recently"} · {item.actionable ? "Actionable" : "Not actionable"}</p><div className="card-actions"><Link className="button-secondary" aria-label={`Open workspace for ${context}`} to={`/jobs/${encodeURIComponent(item.discovered_job_id)}`}>Open workspace</Link><Link className="button-secondary" aria-label={`View Fit for ${context}`} to={`/jobs/${encodeURIComponent(item.discovered_job_id)}/fit`}>View Fit</Link><a aria-label={`Open vacancy for ${context}`} href={item.url} target="_blank" rel="noopener noreferrer">Open vacancy</a></div><DecisionControls decision={item} context={context} disabled={decisionMutator.pending.has(item.discovered_job_id)} onMutate={(target) => void mutateDecision(item, target, "shortlisted")} />{decisionMutator.notices[item.discovered_job_id] && <p className="notice" role="status">{decisionMutator.notices[item.discovered_job_id]}</p>}</li>; })}</ul>{shortlisted.data.truncated && (shortlistedLimit < MAX_WINDOW ? <button type="button" className="button-secondary" onClick={() => { const next = nextWindow(shortlistedLimit); setShortlistedLimit(next); void loadShortlisted(next); }}>Show more shortlisted jobs</button> : <p className="muted">Showing the first 100 shortlisted jobs; more matching decisions exist.</p>)}</>}{shortlisted.data && <section className="card" aria-labelledby="dismissed-heading"><div className="section-heading"><h3 id="dismissed-heading">Dismissed jobs</h3><button type="button" className="button-secondary" aria-expanded={showDismissed} onClick={() => setShowDismissed((value) => !value)}>{showDismissed ? "Hide dismissed jobs" : "Manage dismissed jobs"}</button></div>{showDismissed && <StateMessage state={dismissed} empty={false} onRetry={() => void loadDismissed()}>{dismissed.phase === "loaded" && dismissed.data?.items.length === 0 && <p className="muted">No dismissed jobs.</p>}{!!dismissed.data?.items.length && <><ul className="opportunity-list">{dismissed.data.items.map((item) => { const context = jobContextLabel(item.title, item.company, item.location, `decision updated ${dateLabel(item.updated_at)}`); return <li className="card opportunity-card" key={item.discovered_job_id}><h4>{item.title}</h4><p>{[item.company, item.location].filter(Boolean).join(" · ") || "Details not provided"}</p><div className="card-actions"><Link className="button-secondary" aria-label={`Open workspace for ${context}`} to={`/jobs/${encodeURIComponent(item.discovered_job_id)}`}>Open workspace</Link><Link className="button-secondary" aria-label={`View Fit for ${context}`} to={`/jobs/${encodeURIComponent(item.discovered_job_id)}/fit`}>View Fit</Link></div><DecisionControls decision={item} context={context} disabled={decisionMutator.pending.has(item.discovered_job_id)} onMutate={(target) => void mutateDecision(item, target, "dismissed")} />{decisionMutator.notices[item.discovered_job_id] && <p className="notice" role="status">{decisionMutator.notices[item.discovered_job_id]}</p>}</li>; })}</ul>{dismissed.data.truncated && (dismissedLimit < MAX_WINDOW ? <button type="button" className="button-secondary" onClick={() => { const next = nextWindow(dismissedLimit); setDismissedLimit(next); void loadDismissed(next); }}>Show more dismissed jobs</button> : <p className="muted">Showing the first 100 dismissed jobs; more matching decisions exist.</p>)}</>}</StateMessage>}</section>}</StateMessage></section>}
    </div>
    </>}
    </>}
  </main>;
}

function RunSnapshot({ run }: { run: DiscoveryRunSummary }) {
  const input = run.run_input;
  const query = (input.query && typeof input.query === "object" ? input.query : {}) as Record<string, unknown>;
  const keywords = Array.isArray(query.keywords) ? query.keywords.map(String).join(", ") : "Not recorded";
  const locations = Array.isArray(query.locations) ? query.locations.map(String).join(", ") : "Any";
  const remote = query.remote_ok === false ? "Exclude remote" : "No remote restriction";
  const otherCriteria = (["companies", "excluded_companies", "excluded_title_terms", "employment_types"] as const).flatMap((key) => Array.isArray(query[key]) && query[key].length ? [`${titleCase(key)}: ${(query[key] as unknown[]).map(String).join(", ")}`] : []);
  if (typeof query.max_results === "number") otherCriteria.push(`Max results: ${query.max_results}`);
  return <><p>Search themes: {keywords} · locations: {locations} · remote policy: {remote}</p>{otherCriteria.map((criterion) => <p key={criterion}>{criterion}</p>)}<dl className="metric-grid">{Object.entries(run.funnel).map(([name, value]) => <div key={name}><dt>{titleCase(name)}</dt><dd>{value}</dd></div>)}</dl>{Object.entries(run.failure_summary).length > 0 && <p>Failure summary: {Object.entries(run.failure_summary).map(([key, value]) => `${titleCase(key)} ${value}`).join(" · ")}</p>}</>;
}

function RunRows({ run, onHistorical, selectedHistorical, historicalDetail, onRetryHistorical }: { run: DiscoveryRunDetail; onHistorical: (jobId: string) => void; selectedHistorical: string | null; historicalDetail: SectionState<HistoricalRunJobDetail>; onRetryHistorical: (jobId: string) => void }) {
  const isRunning = run.status === "running";
  return <section className="run-rows"><h4>{isRunning ? "Evaluation in progress" : "Per-job outcomes"}</h4>{isRunning && <p className="notice">Per-job values may still be provisional while this evaluation is running.</p>}<ul>{run.jobs.map((row, index) => { const key = `${run.id}:${row.discovered_job_id}`; const context = row.opportunity ? jobContextLabel(row.opportunity.title, row.opportunity.company, row.opportunity.location) : `historical result ${index + 1}`; return <li key={key}><p><strong>{isRunning && row.outcome === "analysis_failed" ? "In progress" : outcomeLabels[row.outcome]}</strong>{!isRunning && row.failure_kind ? ` · ${titleCase(row.failure_kind)}` : ""}</p>{row.opportunity && <p>{row.opportunity.title} · {row.opportunity.company ?? "Company not provided"}</p>}<div className="card-actions"><Link className="button-secondary" aria-label={`Open workspace for ${context}`} to={`/jobs/${encodeURIComponent(row.discovered_job_id)}`}>Open workspace</Link>{row.opportunity && <Link className="button-secondary" aria-label={`View Fit for ${context}`} to={`/jobs/${encodeURIComponent(row.discovered_job_id)}/fit`}>View Fit</Link>}<button type="button" className="button-secondary" aria-label={`Historical detail for ${context}`} onClick={() => onHistorical(row.discovered_job_id)} aria-expanded={selectedHistorical === key}>Historical detail</button></div>{selectedHistorical === key && <StateMessage state={historicalDetail} empty={false} onRetry={() => onRetryHistorical(row.discovered_job_id)}>{historicalDetail.data && <>{historicalDetail.data.opportunity && <OpportunityDetail opportunity={historicalDetail.data.opportunity} historical />}<RuntimeAttributionPanel attribution={historicalDetail.data.runtime_attribution} boundary="evaluation" />{!historicalDetail.data.opportunity && <p>Historical outcome: {outcomeLabels[historicalDetail.data.outcome]}. No evaluation snapshot exists for this row.</p>}</>}</StateMessage>}</li>; })}</ul></section>;
}

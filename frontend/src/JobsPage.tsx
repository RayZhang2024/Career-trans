import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ApplicationPreparation, ApplicationPrepareRequest, BoundedResponse, CreateDiscoveryRun, DiscoveryRunCreated, DiscoveryRunDetail, DiscoveryRunSummary, HistoricalRunJobDetail, InboxSummary, OnboardingStatus, Profile, RankedJobOpportunity, UserOpportunitySummary } from "./api";
import { ApiError, useAuth } from "./auth";
import { RuntimeAttributionPanel } from "./RuntimeAttributionPanel";

type SectionState<T> = { phase: "loading" | "loaded" | "error"; data?: T; error?: string };
type Tab = "opportunities" | "runs" | "inbox";
const WINDOW = 20;
const MAX_WINDOW = 100;
const emptyPage = <T,>(): SectionState<T> => ({ phase: "loading" });
const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const splitTerms = (value: string) => value.split(/[\n,]/).map((part) => part.trim()).filter(Boolean);
const splitLocations = (value: string) => value.split(/\r?\n/).map((part) => part.trim()).filter(Boolean);
const nextWindow = (current: number) => Math.min(MAX_WINDOW, current + WINDOW);
const numberLabel = (value: number) => Number.isInteger(value) ? String(value) : value.toFixed(1);

const outcomeLabels: Record<DiscoveryRunDetail["jobs"][number]["outcome"], string> = {
  newly_evaluated: "Newly evaluated", reused_evaluation: "Reused evaluation", not_actionable: "Not actionable",
  presemantic_filtered: "Presemantic filtered", outside_semantic_budget: "Outside semantic budget",
  semantic_rejected: "Semantic rejected", outside_deep_analysis_budget: "Outside deep-analysis budget",
  analysis_failed: "Analysis failed",
};

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

function OpportunityDetail({ opportunity, historical = false }: { opportunity: RankedJobOpportunity; historical?: boolean }) {
  const profile = opportunity.job_profile;
  const requirements = profile?.requirements ?? [];
  return <article className="job-detail" aria-label={historical ? "Historical evaluation detail" : "Current opportunity detail"}>
    <h3>{historical ? "Historical evaluation snapshot" : "Opportunity detail"}</h3>
    {historical && <p className="notice">This is historical evaluation state. It does not describe the vacancy or recommendation as current.</p>}
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
    <section><h4>{historical ? "Historical posting recency signal" : "Posting recency signal"}</h4><p>{titleCase(opportunity.legitimacy.legitimacy)} — {opportunity.legitimacy.reasoning}</p></section>
  </article>;
}

function OpportunityPreparation({ opportunity, ready, onUnavailable, onReadinessRefresh }: { opportunity: UserOpportunitySummary; ready: boolean | undefined; onUnavailable: () => Promise<boolean>; onReadinessRefresh: (status: OnboardingStatus | undefined) => void }) {
  const { api } = useAuth();
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
      const result = await api.request<ApplicationPreparation>("/api/v1/applications/prepare", { method: "POST", body: JSON.stringify(payload) });
      if (alive.current && requestGeneration === generation.current) setCreated(result);
    } catch (reason) {
      if (!alive.current || requestGeneration !== generation.current || (reason as Error)?.name === "AbortError") return;
      if (!(reason instanceof ApiError)) {
        try {
          const history = await api.request<ApplicationPreparation[]>("/api/v1/applications");
          if (alive.current && requestGeneration === generation.current) {
            setReconciled(history.filter((item) => item.target.canonical_discovered_job_id === opportunity.discovered_job_id));
            setError("The preparation request was interrupted. Career-trans cannot confirm from this response whether a preparation was created. Saved application history was refreshed; any matching items are shown as history only.");
          }
        } catch (historyError) {
          if (alive.current && requestGeneration === generation.current && (historyError as Error)?.name !== "AbortError") setError("The preparation request was interrupted. Career-trans cannot confirm from this response whether a preparation was created. Saved application history could not be confirmed as refreshed.");
        }
      } else if (reason.status === 404) {
        setUnavailable(true);
        const refreshed = await onUnavailable();
        if (alive.current && requestGeneration === generation.current) setError(refreshed ? "This opportunity is no longer available. The current shortlist was refreshed." : "This opportunity is no longer available. The shortlist refresh could not be confirmed.");
      } else if (reason.status === 409) {
        setPrerequisitesUnconfirmed(true);
        const [onboardingResult, profileResult] = await Promise.allSettled([api.request<OnboardingStatus>("/api/v1/onboarding/status"), api.request<Profile>("/api/v1/profile")]);
        if (!alive.current || requestGeneration !== generation.current) return;
        const refreshedReadiness = onboardingResult.status === "fulfilled" ? onboardingResult.value : undefined;
        onReadinessRefresh(refreshedReadiness);
        const candidateNotReady = refreshedReadiness?.candidate_context_ready === false;
        const profileMissing = profileResult.status === "rejected" && profileResult.reason instanceof ApiError && profileResult.reason.status === 404 || profileResult.status === "fulfilled" && !profileResult.value.display_name?.trim();
        const profileReady = profileResult.status === "fulfilled" && Boolean(profileResult.value.display_name?.trim());
        setProfileState(profileMissing ? "missing" : profileReady ? "ready" : "error");
        setPrerequisitesUnconfirmed(!refreshedReadiness?.candidate_context_ready || !profileReady);
        if (candidateNotReady) setError(<>A confirmed candidate CV is required before preparing an application. <Link to="/profile/cv">Continue CV onboarding</Link>.</>);
        else if (profileMissing) setError(<>An application display name is required. <Link to="/profile">Update your profile</Link>.</>);
        else if (onboardingResult.status !== "fulfilled" || profileResult.status !== "fulfilled") setError("Career-trans could not confirm the current preparation prerequisites. Review your CV and profile, then try again.");
        else setError("Career-trans could not prepare this application because the current candidate or application data conflicts with the request.");
      } else if (reason.status === 422) setError("Career-trans could not prepare this application because the target or request did not contain sufficient usable information.");
      else if (reason.status === 503) setError("Application preparation is temporarily unavailable. Your saved application history remains available.");
      else setError("Career-trans could not prepare this application. No preparation success was confirmed.");
    } finally { lock.current = false; if (alive.current) setPending(false); }
  };

  return <section className="preparation-panel" aria-label={`Prepare application for ${opportunity.title}`}>
    <button type="button" className="button-secondary" aria-expanded={open} disabled={!open && (ready !== true || unavailable)} onClick={() => { setOpen((value) => !value); if (!open && profileState === "unchecked") void checkDisplayName(); }}>{open ? "Close preparation options" : "Prepare application"}</button>
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


export function JobsPage() {
  const { api, user } = useAuth();
  const [onboarding, setOnboarding] = useState<SectionState<OnboardingStatus>>({ phase: "loading" });
  const [opportunities, setOpportunities] = useState<SectionState<BoundedResponse<UserOpportunitySummary>>>({ phase: "loading" });
  const [runs, setRuns] = useState<SectionState<BoundedResponse<DiscoveryRunSummary>>>({ phase: "loading" });
  const [inbox, setInbox] = useState<SectionState<BoundedResponse<InboxSummary>>>({ phase: "loading" });
  const [opportunityLimit, setOpportunityLimit] = useState(WINDOW);
  const [runLimit, setRunLimit] = useState(WINDOW);
  const [inboxLimit, setInboxLimit] = useState(WINDOW);
  const [tab, setTab] = useState<Tab>("opportunities");
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<SectionState<DiscoveryRunDetail>>({ phase: "loading" });
  const [selectedHistorical, setSelectedHistorical] = useState<string | null>(null);
  const [historicalDetail, setHistoricalDetail] = useState<SectionState<HistoricalRunJobDetail>>({ phase: "loading" });
  const [selectedCurrent, setSelectedCurrent] = useState<string | null>(null);
  const [currentDetail, setCurrentDetail] = useState<SectionState<RankedJobOpportunity>>({ phase: "loading" });
  const [staleNotice, setStaleNotice] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [keywords, setKeywords] = useState("");
  const [locations, setLocations] = useState("");
  const [remotePolicy, setRemotePolicy] = useState<"any" | "exclude_remote">("any");
  const [submitting, setSubmitting] = useState(false);
  const [evaluationSnapshot, setEvaluationSnapshot] = useState<{ titles: string[]; query: CreateDiscoveryRun["query"] } | null>(null);
  const [evaluationMessage, setEvaluationMessage] = useState("");
  const [evaluationError, setEvaluationError] = useState("");
  const submitLock = useRef(false);
  const alive = useRef(false);
  const generations = useRef({ onboarding: 0, opportunities: 0, runs: 0, inbox: 0, runDetail: 0, historicalDetail: 0, currentDetail: 0 });
  const userKey = user?.id ?? "";

  const loadOnboarding = async () => {
    const request = ++generations.current.onboarding;
    setOnboarding((previous) => ({ ...previous, phase: previous.data ? "loaded" : "loading", error: undefined }));
    try {
      const data = await api.request<OnboardingStatus>("/api/v1/onboarding/status");
      if (alive.current && request === generations.current.onboarding) setOnboarding({ phase: "loaded", data });
    } catch {
      if (alive.current && request === generations.current.onboarding) setOnboarding((previous) => ({ phase: previous.data ? "error" : "error", data: previous.data, error: "Candidate readiness is unavailable." }));
    }
  };
  const acceptOnboardingAuthority = (data: OnboardingStatus | undefined) => {
    generations.current.onboarding += 1;
    setOnboarding(data ? { phase: "loaded", data } : { phase: "error", error: "Candidate readiness is unavailable." });
  };
  const loadOpportunities = async (limit = opportunityLimit, clearExisting = false): Promise<boolean> => {
    const request = ++generations.current.opportunities;
    setOpportunities((previous) => ({ ...previous, phase: previous.data && !clearExisting ? "loaded" : "loading", data: clearExisting ? undefined : previous.data, error: undefined }));
    try {
      const data = await api.request<BoundedResponse<UserOpportunitySummary>>(`/api/v1/jobs/opportunities?limit=${limit}`);
      if (alive.current && request === generations.current.opportunities) { setOpportunities({ phase: "loaded", data }); return true; }
      return false;
    } catch {
      if (alive.current && request === generations.current.opportunities) setOpportunities((previous) => ({ phase: "error", data: previous.data, error: "Current opportunities are unavailable." }));
      return false;
    }
  };
  const loadRuns = async (limit = runLimit): Promise<boolean> => {
    const request = ++generations.current.runs;
    setRuns((previous) => ({ ...previous, phase: previous.data ? "loaded" : "loading", error: undefined }));
    try {
      const data = await api.request<BoundedResponse<DiscoveryRunSummary>>(`/api/v1/jobs/discovery-runs?limit=${limit}`);
      if (alive.current && request === generations.current.runs) { setRuns({ phase: "loaded", data }); return true; }
      return false;
    } catch {
      if (alive.current && request === generations.current.runs) setRuns((previous) => ({ phase: "error", data: previous.data, error: "Discovery run history is unavailable." }));
      return false;
    }
  };
  const loadInbox = async (limit = inboxLimit) => {
    const request = ++generations.current.inbox;
    setInbox((previous) => ({ ...previous, phase: previous.data ? "loaded" : "loading", error: undefined }));
    try {
      const data = await api.request<BoundedResponse<InboxSummary>>(`/api/v1/jobs/inbox?limit=${limit}`);
      if (alive.current && request === generations.current.inbox) {
        setInbox({ phase: "loaded", data });
        const actionableIds = new Set(data.items.filter((item) => item.actionable).map((item) => item.discovered_job_id));
        setSelectedIds((current) => new Set(Array.from(current).filter((id) => actionableIds.has(id))));
      }
    } catch {
      if (alive.current && request === generations.current.inbox) setInbox((previous) => ({ phase: "error", data: previous.data, error: "Recent imported vacancies are unavailable." }));
    }
  };
  useEffect(() => {
    alive.current = true;
    void loadOnboarding(); void loadOpportunities(WINDOW); void loadRuns(WINDOW); void loadInbox(WINDOW);
    return () => { alive.current = false; for (const key of Object.keys(generations.current) as Array<keyof typeof generations.current>) generations.current[key] += 1; };
  }, [api, userKey]);

  const openCurrent = async (evaluationId: string) => {
    const request = ++generations.current.currentDetail;
    setSelectedCurrent(evaluationId); setCurrentDetail({ phase: "loading" }); setStaleNotice("");
    try {
      const data = await api.request<RankedJobOpportunity>(`/api/v1/jobs/opportunities/${encodeURIComponent(evaluationId)}`);
      if (alive.current && request === generations.current.currentDetail) setCurrentDetail({ phase: "loaded", data });
    } catch (error) {
      if (!alive.current || request !== generations.current.currentDetail) return;
      if (error instanceof ApiError && error.status === 404) {
        setSelectedCurrent(null); setCurrentDetail({ phase: "error", error: "This opportunity is no longer current. The shortlist has been refreshed." });
        setStaleNotice("This opportunity is no longer current. The shortlist has been refreshed.");
        void loadOpportunities(WINDOW); setOpportunityLimit(WINDOW);
      } else setCurrentDetail({ phase: "error", error: "Opportunity detail is unavailable." });
    }
  };
  const openRun = async (runId: string) => {
    const request = ++generations.current.runDetail;
    setSelectedRun(runId); setRunDetail({ phase: "loading" }); setSelectedHistorical(null); setHistoricalDetail({ phase: "loading" });
    try {
      const data = await api.request<DiscoveryRunDetail>(`/api/v1/jobs/discovery-runs/${encodeURIComponent(runId)}`);
      if (alive.current && request === generations.current.runDetail) setRunDetail({ phase: "loaded", data });
    } catch {
      if (alive.current && request === generations.current.runDetail) setRunDetail({ phase: "error", error: "Run detail is unavailable." });
    }
  };
  const openHistorical = async (runId: string, jobId: string) => {
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
  const toggleJob = (item: InboxSummary) => {
    if (!item.actionable) return;
    setSelectedIds((current) => { const next = new Set(current); if (next.has(item.discovered_job_id)) next.delete(item.discovered_job_id); else next.add(item.discovered_job_id); return next; });
  };
  const submitEvaluation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitLock.current || submitting || !onboarding.data?.candidate_context_ready || selectedIds.size === 0 || splitTerms(keywords).length === 0) return;
    const query: CreateDiscoveryRun["query"] = {
      keywords: splitTerms(keywords), locations: splitLocations(locations),
      remote_ok: remotePolicy === "exclude_remote" ? false : null,
      companies: [], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 50,
    };
    const ids = Array.from(selectedIds);
    const titles = inbox.data?.items.filter((item) => selectedIds.has(item.discovered_job_id)).map((item) => item.title) ?? [];
    const payload: CreateDiscoveryRun = { query, discovered_job_ids: ids, max_semantic_candidates: 10, max_full_analyses: 5, min_relevance_score: 0.5 };
    submitLock.current = true; setSubmitting(true); setEvaluationSnapshot({ titles, query }); setEvaluationMessage(""); setEvaluationError("");
    try {
      await api.request<DiscoveryRunCreated>("/api/v1/jobs/discovery-runs", { method: "POST", body: JSON.stringify(payload) });
      setEvaluationMessage("Evaluation completed. Refreshing recent runs and the current shortlist…");
      setSelectedIds(new Set()); setOpportunityLimit(WINDOW);
      const [runsRefreshed, opportunitiesRefreshed] = await Promise.all([loadRuns(runLimit), loadOpportunities(WINDOW, true)]);
      setEvaluationMessage(`Evaluation completed. Recent runs ${runsRefreshed ? "were refreshed" : "could not be confirmed as refreshed"}; the current shortlist ${opportunitiesRefreshed ? "was refreshed" : "could not be confirmed as refreshed"}.`);
    } catch (error) {
      const runsRefreshed = await loadRuns(runLimit);
      setEvaluationError(error instanceof ApiError
        ? `Career-trans returned an error while creating the evaluation. ${runsRefreshed ? "Recent run history has been refreshed." : "Recent run history could not be confirmed as refreshed."}`
        : `The evaluation request was interrupted. Career-trans cannot confirm from this response whether the run started. ${runsRefreshed ? "Recent run history has been refreshed." : "Recent run history could not be confirmed as refreshed."}`);
    } finally { submitLock.current = false; if (alive.current) setSubmitting(false); }
  };

  const ready = onboarding.data?.candidate_context_ready === true;
  const noRuns = runs.data?.items.length === 0 && runs.phase === "loaded";
  const hasHistoricalRuns = runs.data !== undefined && runs.data.items.length > 0;
  const confirmedWindow = opportunities.data?.items ?? [];

  return <main className="jobs-page">
    <header className="workspace-header jobs-header"><div><p className="eyebrow">Career workspace</p><h1>Jobs</h1><p className="muted">Review current evaluations, run history, and recent imported public vacancies.</p><Link to="/jobs/searches">Manage saved discovery configurations</Link></div></header>
    <nav className="jobs-tabs" aria-label="Jobs sections">{(["opportunities", "runs", "inbox"] as Tab[]).map((item) => <button type="button" key={item} aria-pressed={tab === item} onClick={() => setTab(item)}>{item === "opportunities" ? "Opportunities" : item === "runs" ? "Discovery runs" : "Recent vacancies"}</button>)}</nav>
    <section className="jobs-prerequisite card" aria-label="Candidate readiness">
      {onboarding.phase === "loading" && !onboarding.data ? <p role="status">Checking candidate readiness…</p> : onboarding.data ? <>
        {!ready && <><h2>Complete your Profile first</h2><p>Confirm structured career information in Profile or confirm a CV to create the candidate context used for evaluation.</p><Link to="/profile">Review Profile</Link></>}
        {ready && onboarding.data.latest_cv_draft && onboarding.data.latest_cv_draft.state !== "confirmed" && <p className="notice">Evaluations use your current confirmed candidate profile. A newer CV update is awaiting review.</p>}
        {ready && onboarding.data.adviser.assessment_status !== "confirmed" && <p className="muted">Career Adviser completion is optional. Saved Adviser intake may already inform your evaluation context.</p>}
      </> : <div><p role="alert">Candidate readiness is unavailable. Evaluation controls remain hidden until it can be confirmed.</p><button type="button" className="button-secondary" onClick={() => void loadOnboarding()}>Retry readiness</button></div>}
    </section>
    {evaluationMessage && <p className="notice" role="status">{evaluationMessage}</p>}{evaluationError && <p className="notice" role="status">{evaluationError}</p>}{staleNotice && <p className="notice" role="status">{staleNotice}</p>}
    {evaluationSnapshot && <section className="card evaluation-snapshot"><h2>{submitting ? "Evaluation in progress" : "Submitted evaluation"}</h2>{submitting && <p role="status">Evaluating selected jobs… this may take several minutes.</p>}<p>Selected jobs: {evaluationSnapshot.titles.join(", ") || "Selection submitted"}</p><p>Search themes: {evaluationSnapshot.query.keywords.join(", ")} · locations: {evaluationSnapshot.query.locations.join(", ") || "Any"} · remote policy: {evaluationSnapshot.query.remote_ok === false ? "Exclude remote jobs" : "No remote restriction"}</p></section>}
    <div className="jobs-content">
      {tab === "opportunities" && <section aria-labelledby="opportunities-heading" className="jobs-section"><div className="section-heading"><div><h2 id="opportunities-heading">Current ranked opportunities</h2><p className="muted">Ordered by the current backend shortlist. Posting recency is a signal, not proof the vacancy is live.</p></div><button type="button" className="button-secondary" onClick={() => void loadOpportunities()}>Refresh</button></div>
        <StateMessage state={opportunities} empty={false} onRetry={() => void loadOpportunities()}>
          {!confirmedWindow.length && noRuns && <p className="muted">No jobs have been evaluated yet.</p>}
          {!confirmedWindow.length && hasHistoricalRuns && opportunities.phase === "loaded" && <p className="muted">No current evaluated opportunities. Historical runs are available below.</p>}
          {!!confirmedWindow.length && <ol className="opportunity-list">{confirmedWindow.map((item, index) => <li className="card opportunity-card" key={item.discovered_job_id}><div className="opportunity-top"><div><p className="recommendation-label">{item.recommendation.toUpperCase()}</p><h3>{item.title}</h3><p>{[item.company, item.location, item.work_arrangement].filter(Boolean).join(" · ")}</p></div><span className="ordinal">{index + 1}</span></div><dl className="metric-grid"><div><dt>Fit</dt><dd>{numberLabel(item.fit_score)}</dd></div><div><dt>Career alignment</dt><dd>{numberLabel(item.career_alignment_score)} · {titleCase(item.career_alignment_confidence)}</dd></div><div><dt>Relevance</dt><dd>{numberLabel(item.relevance_score * 100)}%</dd></div><div><dt>Role archetype</dt><dd>{titleCase(item.archetype)}</dd></div></dl><p><strong>Posting recency signal:</strong> {titleCase(item.posting_recency.legitimacy)} — {item.posting_recency.reasoning}</p><div className="card-actions"><a href={item.url} target="_blank" rel="noopener noreferrer">Open vacancy</a><button type="button" className="button-secondary" aria-expanded={selectedCurrent === item.evaluation_id} onClick={() => selectedCurrent === item.evaluation_id ? (generations.current.currentDetail += 1, setSelectedCurrent(null)) : void openCurrent(item.evaluation_id)}>{selectedCurrent === item.evaluation_id ? "Close detail" : "View detail"}</button></div><OpportunityPreparation opportunity={item} ready={onboarding.data?.candidate_context_ready} onUnavailable={async () => { const refreshed = await loadOpportunities(WINDOW); setOpportunityLimit(WINDOW); setStaleNotice(refreshed ? "This opportunity is no longer available. The current shortlist was refreshed." : "This opportunity is no longer available. The shortlist refresh could not be confirmed."); return refreshed; }} onReadinessRefresh={acceptOnboardingAuthority} />{selectedCurrent === item.evaluation_id && <StateMessage state={currentDetail} empty={false} onRetry={() => void openCurrent(item.evaluation_id)}>{currentDetail.data && <OpportunityDetail opportunity={currentDetail.data} />}</StateMessage>}</li>)}</ol>}
          {opportunities.data?.truncated && (opportunityLimit < MAX_WINDOW ? <button type="button" className="button-secondary" onClick={() => { const next = nextWindow(opportunityLimit); setOpportunityLimit(next); void loadOpportunities(next); }}>Show more current opportunities</button> : <p className="muted">Showing the first 100 current opportunities available through this view.</p>)}
        </StateMessage>
      </section>}
      {tab === "runs" && <section aria-labelledby="runs-heading" className="jobs-section"><div className="section-heading"><div><h2 id="runs-heading">Discovery-run history</h2><p className="muted">Recent runs and their stored query/funnel summaries.</p></div><button type="button" className="button-secondary" onClick={() => void loadRuns()}>Refresh</button></div>
        <StateMessage state={runs} empty={false} onRetry={() => void loadRuns()}>{noRuns && <p className="muted">No discovery runs yet.</p>}{!!runs.data?.items.length && <ol className="run-list">{runs.data.items.map((run) => <li className="card run-card" key={run.id}><div className="section-heading"><div><h3>{run.status === "running" ? "Evaluation in progress" : titleCase(run.status)}</h3><p>Started {new Date(run.started_at).toLocaleString()}{run.completed_at ? ` · completed ${new Date(run.completed_at).toLocaleString()}` : ""}</p></div><button type="button" className="button-secondary" aria-expanded={selectedRun === run.id} onClick={() => selectedRun === run.id ? (generations.current.runDetail += 1, setSelectedRun(null)) : void openRun(run.id)}>{selectedRun === run.id ? "Close run" : "View run"}</button></div><RunSnapshot run={run} />{selectedRun === run.id && <StateMessage state={runDetail} empty={false} onRetry={() => void openRun(run.id)}>{runDetail.data && <RunRows run={runDetail.data} onHistorical={(jobId) => void openHistorical(run.id, jobId)} selectedHistorical={selectedHistorical} historicalDetail={historicalDetail} onRetryHistorical={(jobId) => void openHistorical(run.id, jobId)} />}</StateMessage>}</li>)}</ol>}{runs.data?.truncated && (runLimit < MAX_WINDOW ? <button type="button" className="button-secondary" onClick={() => { const next = nextWindow(runLimit); setRunLimit(next); void loadRuns(next); }}>Show more runs</button> : <p className="muted">Showing the first 100 discovery runs available through this view.</p>)}</StateMessage>
      </section>}
      {tab === "inbox" && <section aria-labelledby="inbox-heading" className="jobs-section"><div className="section-heading"><div><h2 id="inbox-heading">Recent imported public vacancies</h2><p className="muted">Showing the most recent imported public vacancies. This is a shared persisted slice, not all jobs or live search results.</p></div><button type="button" className="button-secondary" onClick={() => void loadInbox()}>Refresh</button></div>
        <StateMessage state={inbox} empty={false} onRetry={() => void loadInbox()}>
          {inbox.phase === "loaded" && inbox.data?.items.length === 0 && <p className="muted">No recently imported public vacancies are available.</p>}
          {!!inbox.data?.items.length && <>
            <ul className="inbox-list">{inbox.data.items.map((item) => <li className={`card inbox-card${selectedIds.has(item.discovered_job_id) ? " is-selected" : ""}`} key={item.discovered_job_id}><label className="selection-label"><input type="checkbox" checked={selectedIds.has(item.discovered_job_id)} disabled={!item.actionable || !ready || submitting} onChange={() => toggleJob(item)} aria-label={`Select ${item.title}`} /><span>{item.title}</span></label><p>{[item.company, item.location, item.work_arrangement, item.employment_type].filter(Boolean).join(" · ") || "Details not provided"}</p><p>Lifecycle: {titleCase(item.state)} · Verification: {titleCase(item.verification_status)} · {item.actionable ? "Actionable" : "Not actionable"}</p>{item.verification_reason && <p>Verification note: {titleCase(item.verification_reason)}</p>}<p>Last seen: {new Date(item.last_seen_at).toLocaleString()}</p>{item.provenance.length > 0 && <p>Recent provenance: {item.provenance.map((source) => `${source.runtime}${source.discovered_via ? ` · ${source.discovered_via}` : ""}`).join("; ")}</p>}<a href={item.url} target="_blank" rel="noopener noreferrer">Open vacancy</a></li>)}</ul>
            {inbox.data.truncated && (inboxLimit < MAX_WINDOW ? <button type="button" className="button-secondary" onClick={() => { const next = nextWindow(inboxLimit); setInboxLimit(next); void loadInbox(next); }}>Show more recent vacancies</button> : <p className="muted">Showing the first 100 recent vacancies available through this view.</p>)}
          </>}
          {ready && <form className="card evaluation-form" onSubmit={(event) => void submitEvaluation(event)}><h3>Evaluate selected actionable jobs</h3><p className="muted">This evaluates persisted vacancies. It does not start internet discovery.</p><label htmlFor="job-keywords">Search themes (soft prioritisation; not eligibility filters)</label><textarea id="job-keywords" value={keywords} onChange={(event) => setKeywords(event.target.value)} placeholder="AI Engineer, Applied AI Engineer" disabled={submitting} /><p className="muted">Enter at least one theme to create the structured query.</p><label htmlFor="job-locations">Location eligibility (one location per line)</label><textarea id="job-locations" value={locations} onChange={(event) => setLocations(event.target.value)} placeholder={'London, United Kingdom\nOxford, United Kingdom'} disabled={submitting} /><p className="muted">Enter one complete location per line; commas within a location are preserved.</p><label htmlFor="remote-policy">Remote policy</label><select id="remote-policy" value={remotePolicy} onChange={(event) => setRemotePolicy(event.target.value as typeof remotePolicy)} disabled={submitting}><option value="any">No remote restriction</option><option value="exclude_remote">Exclude remote jobs</option></select><button type="submit" disabled={submitting || selectedIds.size === 0 || splitTerms(keywords).length === 0}>{submitting ? "Evaluating…" : `Evaluate ${selectedIds.size || "selected"} jobs`}</button>{!selectedIds.size && <p className="muted">Select one or more actionable vacancies to continue.</p>}</form>}
        </StateMessage>
      </section>}
    </div>
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
  return <section className="run-rows"><h4>{isRunning ? "Evaluation in progress" : "Per-job outcomes"}</h4>{isRunning && <p className="notice">Per-job values may still be provisional while this evaluation is running.</p>}<ul>{run.jobs.map((row) => { const key = `${run.id}:${row.discovered_job_id}`; return <li key={key}><p><strong>{isRunning && row.outcome === "analysis_failed" ? "In progress" : outcomeLabels[row.outcome]}</strong>{!isRunning && row.failure_kind ? ` · ${titleCase(row.failure_kind)}` : ""}</p>{row.opportunity && <p>{row.opportunity.title} · {row.opportunity.company ?? "Company not provided"}</p>}<button type="button" className="button-secondary" onClick={() => selectedHistorical === key ? undefined : onHistorical(row.discovered_job_id)} aria-expanded={selectedHistorical === key}>Historical detail</button>{selectedHistorical === key && <StateMessage state={historicalDetail} empty={false} onRetry={() => onRetryHistorical(row.discovered_job_id)}>{historicalDetail.data && <>{historicalDetail.data.opportunity && <OpportunityDetail opportunity={historicalDetail.data.opportunity} historical />}<RuntimeAttributionPanel attribution={historicalDetail.data.runtime_attribution} boundary="evaluation" />{!historicalDetail.data.opportunity && <p>Historical outcome: {outcomeLabels[historicalDetail.data.outcome]}. No evaluation snapshot exists for this row.</p>}</>}</StateMessage>}</li>; })}</ul></section>;
}

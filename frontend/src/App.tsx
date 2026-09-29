import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { type CandidateCVData, type CandidateEligibility, type CanonicalCandidateReadSnapshot, type OnboardingStatus, type PasswordPolicy, type SessionApi, type StructuredProfileCurrentItemProvenanceRead, type StructuredProfileLineageEventRead, type StructuredProfileProvenanceRead } from "./api";
import { ApiError, useAuth } from "./auth";
import { CvPage } from "./CvPage";
import { AdviserPage } from "./AdviserPage";
import { JobsPage } from "./JobsPage";
import { JobsSearchesPage } from "./JobsSearchesPage";
import { ApplicationsPage, ApplicationDetailPage } from "./ApplicationsPage";
import { TrackingDetailPage, TrackingPage } from "./TrackingPage";
import { AiSettingsPage } from "./AiSettingsPage";
import { ProfileRevisionWorkflow } from "./ProfileRevisionWorkflow";
import { ProfileSecondaryNavigation } from "./ProfileSecondaryNavigation";
import { structuredItemLabel, StructuredItemPresentation } from "./StructuredItemPresentation";
import "./App.css";

function AppShell({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  const jobSearchActive = location.pathname === "/jobs" || location.pathname.startsWith("/jobs/");
  return <div className="app-shell"><header className="site-header"><Link className="brand" to="/home">Career-trans</Link>{status === "authenticated" && <nav className="site-nav" aria-label="Workspace"><NavLink to="/home">Home</NavLink><NavLink to="/profile">Profile</NavLink><NavLink to="/jobs/find" className={jobSearchActive ? "active" : undefined} aria-current={jobSearchActive ? "page" : undefined}>Job Search</NavLink><NavLink to="/applications">Applications</NavLink><NavLink to="/tracking">Tracking</NavLink><NavLink to="/settings/ai">Settings</NavLink></nav>}</header>{children}</div>;
}

export function ProfileFamilyShell({ children }: { children: React.ReactNode }) {
  return <AppShell><ProfileSecondaryNavigation />{children}</AppShell>;
}

function Protected({ children }: { children: React.ReactNode }) {
  const { status, retryRestore } = useAuth();
  if (status === "checking") return <AppShell><main className="page-status"><p>Checking your session…</p></main></AppShell>;
  if (status === "unavailable") return <AppShell><main className="page-status"><p role="alert">Session validation is temporarily unavailable.</p><button onClick={retryRestore}>Retry</button></main></AppShell>;
  return status === "authenticated" ? <>{children}</> : <Navigate to="/login" replace />;
}

function AuthPage({ children }: { children: React.ReactNode }) {
  return <AppShell><main className="auth-page"><section className="card auth-card">{children}</section></main></AppShell>;
}

export function HomePage() {
  const { api, logout } = useAuth();
  const [readiness, setReadiness] = useState<OnboardingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const request = useRef(0);
  useEffect(() => {
    const current = ++request.current;
    setLoading(true);
    setError(false);
    void api.request<OnboardingStatus>("/api/v1/onboarding/status").then((found) => {
      if (current === request.current) { setReadiness(found); setLoading(false); }
    }).catch(() => {
      if (current === request.current) { setReadiness(null); setError(true); setLoading(false); }
    });
    return () => { request.current += 1; };
  }, [api, attempt]);
  return <AppShell><header className="workspace-header"><div><p className="eyebrow">Career workspace</p><h1>Home</h1><p className="muted">Your starting point for profile setup and the rest of your workspace.</p></div><button className="button-secondary" onClick={logout}>Sign out</button></header><main className="home-page">
    <section className="card" aria-busy={loading} aria-labelledby="home-readiness-heading"><h2 id="home-readiness-heading">Profile readiness</h2>{loading ? <p role="status">Checking profile readiness…</p> : error ? <><p role="alert">Readiness is unavailable right now. Please retry before continuing.</p><button className="button-secondary" onClick={() => setAttempt((value) => value + 1)}>Retry readiness</button></> : readiness?.candidate_context_ready ? <p>Your confirmed profile context is ready for the workspace.</p> : <p>Complete your CV and confirm your profile to prepare your workspace.</p>}</section>
    <section aria-labelledby="home-workspace-heading"><h2 id="home-workspace-heading">Your workspace</h2><div className="home-link-grid"><Link className="card home-link-card" to="/profile"><strong>Profile</strong><span>Review and update your profile.</span></Link><Link className="card home-link-card" to="/jobs/find"><strong>Job Search</strong><span>Explore evaluated opportunities and discovery.</span></Link><Link className="card home-link-card" to="/applications"><strong>Applications</strong><span>Continue application preparation.</span></Link><Link className="card home-link-card" to="/tracking"><strong>Tracking</strong><span>Review tracked applications.</span></Link></div></section>
  </main></AppShell>;
}

function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current) return;
    pendingRef.current = true; setPending(true); setError("");
    const data = new FormData(event.currentTarget);
    try { await login(String(data.get("email")), String(data.get("password"))); navigate("/home"); }
    catch (e) { setError(e instanceof ApiError && e.status === 401 ? "Incorrect email or password." : "Login is unavailable."); }
    finally { pendingRef.current = false; setPending(false); }
  }
  return <AuthPage><form className="form-stack" onSubmit={submit}><div><h1>Sign in</h1><p className="muted">Access your Career-trans workspace.</p></div><label htmlFor="login-email">Email</label><input id="login-email" name="email" type="email" autoComplete="email" required disabled={pending} /><label htmlFor="login-password">Password</label><input id="login-password" name="password" type="password" autoComplete="current-password" required disabled={pending} /><button disabled={pending}>{pending ? "Signing in…" : "Sign in"}</button>{pending && <p role="status">Signing in…</p>}{error && <p role="alert">{error}</p>}<p className="auth-link">New to Career-trans? <Link to="/register">Create account</Link></p></form></AuthPage>;
}

function Register() {
  const { api, register } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [policy, setPolicy] = useState<PasswordPolicy | null>(null);
  const [policyError, setPolicyError] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [confirmationTouched, setConfirmationTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const pendingRef = useRef(false);
  useEffect(() => {
    let active = true;
    void api.request<PasswordPolicy>("/api/v1/auth/password-policy", {}, false).then((loaded) => {
      if (active) { setPolicy(loaded); setPolicyError(""); }
    }).catch(() => {
      if (active) setPolicyError("Password requirements could not be loaded. Please reload this page before creating an account.");
    });
    return () => { active = false; };
  }, [api]);
  const passwordLength = Array.from(password).length;
  const minimumMet = policy !== null && passwordLength >= policy.min_length;
  const maximumMet = policy !== null && passwordLength <= policy.max_length;
  // Keep this set aligned with Python's str.isspace() used by the backend.
  const containsWhitespace = /[\p{Z}\u0009-\u000d\u001c-\u001f\u0085]/u.test(password);
  const whitespaceMet = policy !== null && (policy.whitespace_allowed || !containsWhitespace);
  const unmetPasswordRules = policy ? [
    ...(!minimumMet ? [`At least ${policy.min_length} characters`] : []),
    ...(!maximumMet ? [`No more than ${policy.max_length} characters`] : []),
    ...(!whitespaceMet ? ["No whitespace characters"] : []),
  ] : [];
  const confirmationMismatch = confirmationTouched && confirmation !== password;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current) return;
    setSubmitted(true);
    setError("");
    if (!policy) {
      setError("Password requirements could not be verified. Please reload this page before creating an account.");
      return;
    }
    if (unmetPasswordRules.length > 0) return;
    if (confirmation !== password) {
      setConfirmationTouched(true);
      return;
    }
    const emailInput = event.currentTarget.elements.namedItem("email");
    if (emailInput instanceof HTMLInputElement && !emailInput.reportValidity()) return;
    pendingRef.current = true;
    setPending(true);
    const data = new FormData(event.currentTarget);
    try { await register(String(data.get("email")), String(data.get("password"))); navigate("/login"); }
    catch (e) {
      if (e instanceof ApiError && e.status === 409) setError("An account already exists for this email.");
      else if (e instanceof ApiError && e.detail === "password_too_common") setError("Choose a less common password.");
      else if (e instanceof ApiError && policy && e.detail === "password_too_short") setError(`Use at least ${policy.min_length} characters.`);
      else if (e instanceof ApiError && policy && e.detail === "password_too_long") setError(`Use no more than ${policy.max_length} characters.`);
      else if (e instanceof ApiError && e.detail === "password_contains_whitespace") setError("Remove all spaces and other whitespace characters from the password.");
      else setError("Registration is unavailable.");
    } finally { pendingRef.current = false; setPending(false); }
  }
  return <AuthPage><form className="form-stack" noValidate onSubmit={submit}>
    <div><h1>Create account</h1><p className="muted">Create an account, then sign in to continue.</p></div>
    <label htmlFor="register-email">Email</label><input id="register-email" name="email" type="email" autoComplete="email" required disabled={pending} />
    <label htmlFor="register-password">Password</label>
    <input id="register-password" name="password" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} aria-describedby={submitted && unmetPasswordRules.length ? "password-policy password-validation-error" : "password-policy"} aria-invalid={submitted && unmetPasswordRules.length > 0} required disabled={pending} />
    <div id="password-policy" className="password-policy" role="group" aria-labelledby="password-requirements-label">
      {policy ? <>
        <p id="password-requirements-label">Password requirements</p>
        <p>Use at least {policy.min_length} characters and no more than {policy.max_length} characters. Common passwords are not accepted.</p>
        <p>Whitespace is not allowed. No special mix of uppercase, numbers, or symbols is required.</p>
        <ul className="password-rules">
          <li>{minimumMet ? "Met:" : "Not met:"} At least {policy.min_length} characters</li>
          <li>{maximumMet ? "Met:" : "Not met:"} No more than {policy.max_length} characters</li>
          <li>{whitespaceMet ? "Met:" : "Not met:"} No whitespace characters</li>
        </ul>
      </> : <p role="status">Loading password requirements…</p>}
    </div>
    {submitted && unmetPasswordRules.length > 0 && <div id="password-validation-error" className="field-error" role="alert"><p>Update the password to meet every requirement:</p><ul>{unmetPasswordRules.map((rule) => <li key={rule}>{rule}</li>)}</ul></div>}
    <label htmlFor="register-confirm-password">Confirm password</label>
    <input id="register-confirm-password" name="confirmPassword" type="password" autoComplete="new-password" value={confirmation} onChange={(event) => { setConfirmation(event.target.value); setConfirmationTouched(true); }} onBlur={() => setConfirmationTouched(true)} aria-describedby={confirmationMismatch ? "confirm-password-error" : undefined} required disabled={pending} />
    {confirmationMismatch && <p id="confirm-password-error" className="field-error" role="alert">Passwords do not match.</p>}
    {policyError && <p role="alert">{policyError}</p>}
    {error && <p role="alert">{error}</p>}
    <button type="submit" disabled={pending}>{pending ? "Creating account…" : "Create account"}</button>
    {pending && <p role="status">Creating your account…</p>}
    <p className="auth-link">Already have an account? <Link to="/login">Sign in</Link></p>
  </form></AuthPage>;
}

function OnboardingCard({ status, error }: { status: OnboardingStatus | undefined; error: string }) {
  if (!status) return <section className="card onboarding-card"><h2>Getting started</h2>{error ? <p role="alert">{error}</p> : <p className="muted">Loading onboarding status…</p>}</section>;
  const adviserLabel = !status.candidate_context_ready ? "Complete CV first" : !status.adviser.intake_exists ? "Start Career Adviser" : status.adviser.assessment_status === "stale" ? "Reassessment needed" : status.adviser.assessment_status === "review_ready" ? "Review adviser assessment" : status.adviser.assessment_status === "confirmed" ? "Adviser assessment current" : "Intake saved";
  return <section className="card onboarding-card"><h2>Getting started</h2>{error && <p role="alert">{error}</p>}<ol className="onboarding-list"><li><strong>Account</strong><span>Ready</span></li><li><strong>Profile</strong><span>{status.profile_exists ? "Saved" : "Not saved yet"}</span></li><li><strong>CV</strong><Link to="/profile/cv">{status.latest_cv_draft?.state === "confirmed" ? "Confirmed" : "Continue CV onboarding"}</Link></li><li>{status.candidate_context_ready ? <><strong>Career Adviser</strong><Link to="/profile/adviser">{adviserLabel}</Link></> : <><strong>Career Adviser</strong><span>{adviserLabel}</span></>}</li></ol></section>;
}

function DetailList({ items }: { items: string[] }) {
  return items.length ? <ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul> : null;
}

function ProfileUrl({ label, value }: { label: string; value: string }) {
  let safeUrl = false;
  try { const parsed = new URL(value); safeUrl = parsed.protocol === "https:" || parsed.protocol === "http:"; } catch { /* Keep malformed saved values visible as text. */ }
  return <div><dt>{label}</dt><dd>{safeUrl ? <a href={value}>{value}</a> : value}</dd></div>;
}

function EligibilityView({ eligibility }: { eligibility: CandidateEligibility }) {
  const groups = [
    ["Work authorisation", eligibility.work_authorisation],
    ["Security clearances", eligibility.security_clearances],
    ["Locations", eligibility.locations],
  ] as const;
  const populated = groups.filter(([, items]) => items.length > 0);
  return <section className="card profile-section"><h2>Current eligibility</h2>{populated.length ? <dl className="profile-detail-grid">{populated.map(([label, items]) => <div key={label}><dt>{label}</dt><dd>{items.join(", ")}</dd></div>)}</dl> : <p className="muted">No eligibility details have been saved.</p>}</section>;
}

function CvInformation({ data }: { data: CandidateCVData | null }) {
  if (!data) return <section className="card profile-section"><h2>Confirmed career information</h2><p className="muted">Confirmed career information is not available yet. <Link to="/profile/cv">Continue CV onboarding</Link>.</p></section>;
  const hasAny = data.employment.length + data.education.length + data.credentials.length + data.skills.length + data.projects.length + data.achievements.length > 0;
  return <section className="card profile-section"><h2>Confirmed career information</h2>
    {!hasAny && <p className="muted">No confirmed structured career details are available yet.</p>}
    {data.employment.length > 0 && <div className="profile-subsection"><h3>Experience</h3><ul className="profile-record-list">{data.employment.map((item, index) => <li key={`${item.employer}-${item.title}-${index}`}><h4>{item.title} at {item.employer}</h4>{(item.start_date || item.end_date || item.location) && <p className="muted">{[item.start_date, item.end_date, item.location].filter(Boolean).join(" · ")}</p>}{item.description && <p className="profile-prose">{item.description}</p>}</li>)}</ul></div>}
    {data.education.length > 0 && <div className="profile-subsection"><h3>Education</h3><ul className="profile-record-list">{data.education.map((item, index) => <li key={`${item.institution}-${index}`}><h4>{item.qualification} — {item.institution}</h4>{item.field_of_study && <p>{item.field_of_study}</p>}{item.description && <p className="profile-prose">{item.description}</p>}</li>)}</ul></div>}
    {data.credentials.length > 0 && <div className="profile-subsection"><h3>Credentials and certifications</h3><ul className="profile-record-list">{data.credentials.map((item, index) => <li key={`${item.name}-${index}`}><h4>{item.name}</h4><p>{item.credential_type.replaceAll("_", " ")}{item.issuer ? ` · ${item.issuer}` : ""}</p>{(item.status || item.issued_date || item.expiry_date) && <p className="muted">{[item.status, item.issued_date, item.expiry_date].filter(Boolean).join(" · ")}</p>}{item.description && <p className="profile-prose">{item.description}</p>}</li>)}</ul></div>}
    {data.skills.length > 0 && <div className="profile-subsection"><h3>Skills</h3><ul className="profile-chip-list">{data.skills.map((item, index) => <li key={`${item.name}-${index}`}>{item.name}{item.category ? ` · ${item.category}` : ""}</li>)}</ul></div>}
    {data.projects.length > 0 && <div className="profile-subsection"><h3>Projects</h3><ul className="profile-record-list">{data.projects.map((item, index) => <li key={`${item.name}-${index}`}><h4>{item.name}</h4>{item.description && <p className="profile-prose">{item.description}</p>}<DetailList items={item.skills} /></li>)}</ul></div>}
    {data.achievements.length > 0 && <div className="profile-subsection"><h3>Achievements</h3><DetailList items={data.achievements.map((item) => item.text)} /></div>}
  </section>;
}

function sourceEvent(event: StructuredProfileLineageEventRead) {
  const source = event.source;
  return <article className="provenance-event" key={event.event_id}>
    {source.available ? source.kind === "cv" ? <><p><strong>Confirmed from CV: {source.filenames.length ? source.filenames.join(", ") : "CV source"}</strong></p>{source.source_state && <p className="muted">Source state: {source.source_state}</p>}</>
      : source.kind === "manual_profile" ? <p><strong>Added or changed through a Profile edit.</strong></p>
        : <><p><strong>Supported through Candidate Adviser.</strong></p>{source.clarification_question && <p><strong>Clarification:</strong> {source.clarification_question}</p>}{source.proposal_item && <div><strong>Adviser suggestion:</strong><StructuredItemPresentation section={event.section} item={source.proposal_item} /></div>}</>
      : <p className="muted">The original source record is no longer available. Known source category: {source.kind.replaceAll("_", " ")}.</p>}
    {source.kind === "manual_profile" && source.confirmed_at && <p className="muted">Confirmed {new Date(source.confirmed_at).toLocaleDateString()}</p>}
    <p className="muted">Relationship: {event.relationship.replaceAll("_", " ")}</p>
  </article>;
}

function ProfileSourceHistory({ api, structured, refreshToken }: { api: Pick<SessionApi, "request">; structured: CandidateCVData | null; refreshToken: number }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "unavailable">("idle");
  const [data, setData] = useState<StructuredProfileProvenanceRead | null>(null);
  const generation = useRef(0);
  const load = async () => {
    const request = ++generation.current; setState("loading");
    try { const found = await api.request<StructuredProfileProvenanceRead>("/api/v1/profile/structured-provenance"); if (request === generation.current) { setData(found); setState("ready"); } }
    catch { if (request === generation.current) setState("unavailable"); }
  };
  useEffect(() => {
    generation.current += 1; setData(null); setState("idle");
    if (open) void load();
  }, [refreshToken]);
  const items = data?.items ?? [];
  return <section className="card profile-source-history"><h2>Confirmed career information</h2><button className="button-secondary" type="button" onClick={() => { const next = !open; setOpen(next); if (next && state === "idle") void load(); }}>{open ? "Hide source history" : "Show source history"}</button>
    {open && <>{state === "loading" && <p role="status">Loading source history…</p>}{state === "unavailable" && <div><p role="alert">Source history is unavailable. Your Profile information remains available.</p><button className="button-secondary" onClick={() => void load()}>Retry source history</button></div>}
      {state === "ready" && structured && <div className="provenance-list">{items.map((entry: StructuredProfileCurrentItemProvenanceRead) => <details key={`${entry.section}-${entry.item_index}`} className="provenance-item"><summary>Source history · {entry.section.replaceAll("_", " ")} {entry.item_index + 1}: {structuredItemLabel(entry.section, entry.item)}</summary>
        {!entry.source_history_available ? <p className="muted">Source history is unavailable for this older item.</p> : <>
          <section className="provenance-group"><h4>Sources for this current item</h4>{entry.direct_events.length ? entry.direct_events.map(sourceEvent) : <p className="muted">No direct source events are available.</p>}</section>
          {entry.history.length > 0 && <section className="provenance-group historical-provenance"><h4>Earlier versions and sources</h4>{entry.history.map(({ depth, lineage_event }) => <div className="provenance-history-event" key={`${depth}-${lineage_event.event_id}`}><p className="relationship-badge">{depth === 1 ? "Earlier version" : depth === 2 ? "Earlier source chain" : `Earlier source history · ${depth} steps back`}</p><StructuredItemPresentation section={lineage_event.section} item={lineage_event.item} label="Earlier version" />{sourceEvent(lineage_event)}</div>)}</section>}
        </>}
      </details>)}</div>}</>}
  </section>;
}

function ProfileReadView({ snapshot, api, provenanceRefresh }: { snapshot: CanonicalCandidateReadSnapshot; api: Pick<SessionApi, "request">; provenanceRefresh: number }) {
  const profile = snapshot.profile;
  const intake = snapshot.adviser_intake;
  const pendingCv = snapshot.structured_profile !== null && (snapshot.readiness.latest_cv_draft_state === "uploaded" || snapshot.readiness.latest_cv_draft_state === "review_ready");
  const incomplete = snapshot.readiness.evidence_materialization_status === "incomplete";
  const adviserLabel: Record<CanonicalCandidateReadSnapshot["adviser_assessment_status"], string> = {
    confirmed: "Career Adviser — confirmed and current",
    review_ready: "Career Adviser assessment needs review",
    stale: "Career Adviser reassessment needed",
    unavailable: "Career Adviser information is currently unavailable",
    not_available: "Career Adviser is not set up yet",
  };
  return <div className="profile-view">
    {incomplete && <div className="profile-warning" role="alert"><h2>Candidate evidence needs attention</h2><p>Career-trans detected an internal candidate-evidence consistency issue. Some matching or application actions may be temporarily unavailable.</p><p>Expected: {snapshot.readiness.expected_evidence_count}; materialised: {snapshot.readiness.materialized_evidence_count}; missing: {snapshot.readiness.missing_evidence_count}; stale: {snapshot.readiness.stale_evidence_count}.</p></div>}
    {pendingCv && <p className="notice profile-notice" role="status">Your current structured career information remains in use. A newer CV update is awaiting review. <Link to="/profile/cv">Review CV update</Link>.</p>}
    {!profile && !snapshot.structured_profile && <section className="card profile-section"><h2>Let’s build your career profile</h2><p>Add profile details and career information to help Career-trans understand your experience.</p><p><Link to="/profile/cv">Start CV onboarding</Link></p></section>}
    {profile && <section className="card profile-section"><h2>Saved profile details</h2><p className="muted">Saved profile and application information; these details are not CV-confirmed facts.</p>{(profile.display_name || profile.headline || profile.current_role || profile.location) && <div className="profile-identity">{profile.display_name && <h3>{profile.display_name}</h3>}{profile.headline && <p>{profile.headline}</p>}{profile.current_role && <p>{profile.current_role}</p>}{profile.location && <p>{profile.location}</p>}</div>}{profile.summary && <div className="profile-prose"><h3>Summary</h3><p>{profile.summary}</p></div>}{(profile.preferred_email || profile.phone || profile.linkedin_url || profile.github_url || profile.portfolio_url) && <dl className="profile-detail-grid">{profile.preferred_email && <div><dt>Preferred email</dt><dd><a href={`mailto:${profile.preferred_email}`}>{profile.preferred_email}</a></dd></div>}{profile.phone && <div><dt>Phone</dt><dd><a href={`tel:${profile.phone}`}>{profile.phone}</a></dd></div>}{profile.linkedin_url && <ProfileUrl label="LinkedIn" value={profile.linkedin_url} />}{profile.github_url && <ProfileUrl label="GitHub" value={profile.github_url} />}{profile.portfolio_url && <ProfileUrl label="Portfolio" value={profile.portfolio_url} />}</dl>}</section>}
    <CvInformation data={snapshot.structured_profile} />
    <ProfileSourceHistory api={api} structured={snapshot.structured_profile} refreshToken={provenanceRefresh} />
    {(profile?.career_goal || profile?.job_search_criteria || intake) && <section className="card profile-section"><h2>Career goals and current preferences</h2>{profile?.career_goal && <div><h3>Career goal</h3><p className="profile-prose">{profile.career_goal}</p></div>}{profile?.job_search_criteria && <div><h3>Job-search criteria</h3><p className="profile-prose">{profile.job_search_criteria}</p></div>}{intake?.career_direction && <div><h3>Career direction</h3><p className="profile-prose">{intake.career_direction}</p></div>}{intake && ([ ["Work preferences", intake.work_preferences], ["Constraints", intake.constraints], ["Trade-offs", intake.tradeoffs], ["Self-assessment", intake.self_assessment], ["Motivations", intake.motivations] ] as const).filter(([, items]) => items.length > 0).map(([label, items]) => <div key={label}><h3>{label}</h3><DetailList items={items} /></div>)}</section>}
    <EligibilityView eligibility={snapshot.eligibility} />
    <section className="card profile-section"><h2>{adviserLabel[snapshot.adviser_assessment_status]}</h2>{snapshot.adviser_assessment_status === "confirmed" && snapshot.adviser_assessment ? <div className="profile-adviser"><p>{snapshot.adviser_assessment.professional_positioning.text}</p><h3>Career strategy</h3><p>{snapshot.adviser_assessment.career_strategy_summary.text}</p><h3>Job-search strategy</h3><p>{snapshot.adviser_assessment.job_search_strategy_summary.text}</p></div> : snapshot.adviser_assessment_status === "review_ready" ? <p>The draft assessment is not shown as current. <Link to="/profile/adviser">Review Career Adviser assessment</Link>.</p> : snapshot.adviser_assessment_status === "stale" ? <p>The saved assessment needs a fresh review. <Link to="/profile/adviser">Revisit Career Adviser</Link>.</p> : snapshot.adviser_assessment_status === "unavailable" ? <p className="muted">Current Career Adviser information is unavailable.</p> : <p className="muted">No current assessment is available. <Link to="/profile/adviser">Set up Career Adviser</Link>.</p>}</section>
    {snapshot.active_evidence.length > 0 && <details className="card profile-section profile-evidence"><summary>Evidence Career-trans currently uses ({snapshot.active_evidence.length})</summary><ul className="profile-record-list">{snapshot.active_evidence.map((item) => <li key={item.evidence_id}><h3>{item.title}</h3><p className="muted">{item.evidence_type.replaceAll("_", " ")}</p><p className="profile-prose">{item.text}</p><DetailList items={item.skills} /></li>)}</ul></details>}
  </div>;
}

export function ProfileHome() {
  const { api, logout, user } = useAuth();
  const [status, setStatus] = useState<OnboardingStatus | undefined>(undefined);
  const [snapshot, setSnapshot] = useState<CanonicalCandidateReadSnapshot | undefined>(undefined);
  const [snapshotLoading, setSnapshotLoading] = useState(true);
  const [statusError, setStatusError] = useState("");
  const [snapshotError, setSnapshotError] = useState("");
  const [provenanceRefresh, setProvenanceRefresh] = useState(0);
  const statusRequest = useRef(0);
  const snapshotRequest = useRef(0);
  const loadStatus = async (): Promise<boolean> => {
    const request = ++statusRequest.current;
    setStatus(undefined);
    setStatusError("");
    try {
      const found = await api.request<OnboardingStatus>("/api/v1/onboarding/status");
      if (request === statusRequest.current) { setStatus(found); return true; }
    } catch {
      if (request === statusRequest.current) {
        setStatus(undefined);
        setStatusError("Onboarding status is unavailable.");
      }
    }
    return false;
  };
  const loadSnapshot = async (): Promise<boolean> => {
    const request = ++snapshotRequest.current;
    setSnapshotError("");
    setSnapshotLoading(true);
    try {
      const found = await api.request<CanonicalCandidateReadSnapshot>("/api/v1/profile/snapshot");
      if (request === snapshotRequest.current) { setSnapshot(found); setSnapshotLoading(false); return true; }
      return false;
    } catch {
      if (request === snapshotRequest.current) {
        setSnapshot(undefined);
        setSnapshotError("Your career profile could not be loaded.");
        setSnapshotLoading(false);
      }
      return false;
    }
  };
  const load = () => { void loadStatus(); void loadSnapshot(); };
  useEffect(() => {
    load();
    return () => { snapshotRequest.current += 1; statusRequest.current += 1; };
  }, []);
  const profileConfirmed = async (): Promise<boolean> => {
    const [current] = await Promise.all([loadSnapshot(), loadStatus()]);
    setProvenanceRefresh((value) => value + 1);
    return current;
  };
  return <ProfileFamilyShell><header className="workspace-header"><div><p className="eyebrow">Career workspace</p><h1>Your career profile</h1><p className="muted">This is the information Career-trans currently uses for matching, job discovery and application preparation.</p></div><button className="button-secondary" onClick={logout}>Sign out</button></header><main className="workspace"><OnboardingCard status={status} error={statusError} /><section className="profile-area" aria-busy={snapshotLoading}><ProfileRevisionWorkflow snapshot={snapshot} onConfirmed={profileConfirmed} />{snapshot === undefined ? snapshotError ? <div className="card section-error"><p role="alert">{snapshotError}</p><button onClick={() => void loadSnapshot()}>Retry profile</button></div> : <p className="muted" role="status">Loading your career profile…</p> : <><div className="profile-refresh">{snapshotLoading && <p className="muted" role="status">Refreshing your career profile…</p>}<button className="button-secondary" onClick={() => void loadSnapshot()}>Refresh profile</button></div><ProfileReadView snapshot={snapshot} api={api} provenanceRefresh={provenanceRefresh} /></>}</section></main></ProfileFamilyShell>;
}

function LegacySavedSearchesRedirect() {
  const location = useLocation();
  return <Navigate to="/jobs/find/saved" replace state={location.state} />;
}

export function App() { return <Routes><Route path="/login" element={<Login />} /><Route path="/register" element={<Register />} /><Route path="/" element={<Navigate to="/profile" replace />} /><Route path="/home" element={<Protected><HomePage /></Protected>} /><Route path="/profile" element={<Protected><ProfileHome /></Protected>} /><Route path="/profile/cv" element={<Protected><ProfileFamilyShell><CvPage /></ProfileFamilyShell></Protected>} /><Route path="/profile/adviser" element={<Protected><ProfileFamilyShell><AdviserPage /></ProfileFamilyShell></Protected>} /><Route path="/cv" element={<Navigate to="/profile/cv" replace />} /><Route path="/adviser" element={<Navigate to="/profile/adviser" replace />} /><Route path="/jobs" element={<Protected><Navigate to="/jobs/find" replace /></Protected>} /><Route path="/jobs/searches" element={<Protected><LegacySavedSearchesRedirect /></Protected>} /><Route path="/jobs/*" element={<Protected><AppShell><JobsPage /></AppShell></Protected>} /><Route path="/applications" element={<Protected><AppShell><ApplicationsPage /></AppShell></Protected>} /><Route path="/applications/:preparationId" element={<Protected><AppShell><ApplicationDetailPage /></AppShell></Protected>} /><Route path="/tracking" element={<Protected><AppShell><TrackingPage /></AppShell></Protected>} /><Route path="/tracking/:trackingId" element={<Protected><AppShell><TrackingDetailPage /></AppShell></Protected>} /><Route path="/settings" element={<Protected><Navigate to="/settings/ai" replace /></Protected>} /><Route path="/settings/ai" element={<Protected><AppShell><AiSettingsPage /></AppShell></Protected>} /><Route path="*" element={<Navigate to="/profile" replace />} /></Routes>; }

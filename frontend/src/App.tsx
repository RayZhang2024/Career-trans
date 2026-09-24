import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { type OnboardingStatus, type Profile } from "./api";
import { ApiError, useAuth } from "./auth";
import { CvPage } from "./CvPage";
import { AdviserPage } from "./AdviserPage";
import { JobsPage } from "./JobsPage";
import { JobsSearchesPage } from "./JobsSearchesPage";
import { ApplicationsPage, ApplicationDetailPage } from "./ApplicationsPage";
import { TrackingDetailPage, TrackingPage } from "./TrackingPage";
import { AiSettingsPage } from "./AiSettingsPage";
import "./App.css";

function AppShell({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  return <div className="app-shell"><header className="site-header"><Link className="brand" to="/">Career-trans</Link>{status === "authenticated" && <nav className="site-nav" aria-label="Workspace"><Link to="/">Profile</Link><Link to="/cv">CV</Link><Link to="/adviser">Career Adviser</Link><Link to="/jobs">Jobs</Link><Link to="/applications">Applications</Link><Link to="/tracking">Tracking</Link><Link to="/jobs/searches">Saved searches</Link><Link to="/settings/ai">Settings</Link></nav>}</header>{children}</div>;
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
    try { await login(String(data.get("email")), String(data.get("password"))); navigate("/"); }
    catch (e) { setError(e instanceof ApiError && e.status === 401 ? "Incorrect email or password." : "Login is unavailable."); }
    finally { pendingRef.current = false; setPending(false); }
  }
  return <AuthPage><form className="form-stack" onSubmit={submit}><div><h1>Sign in</h1><p className="muted">Access your Career-trans workspace.</p></div><label htmlFor="login-email">Email</label><input id="login-email" name="email" type="email" required disabled={pending} /><label htmlFor="login-password">Password</label><input id="login-password" name="password" type="password" required disabled={pending} /><button disabled={pending}>{pending ? "Signing in…" : "Sign in"}</button>{pending && <p role="status">Signing in…</p>}{error && <p role="alert">{error}</p>}<p className="auth-link">New to Career-trans? <Link to="/register">Create account</Link></p></form></AuthPage>;
}

function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current) return;
    pendingRef.current = true; setPending(true); setError("");
    const data = new FormData(event.currentTarget);
    try { await register(String(data.get("email")), String(data.get("password"))); navigate("/login"); }
    catch (e) { setError(e instanceof ApiError && e.status === 409 ? "An account already exists for this email." : "Registration is unavailable."); }
    finally { pendingRef.current = false; setPending(false); }
  }
  return <AuthPage><form className="form-stack" onSubmit={submit}><div><h1>Create account</h1><p className="muted">Create an account, then sign in to continue.</p></div><label htmlFor="register-email">Email</label><input id="register-email" name="email" type="email" required disabled={pending} /><label htmlFor="register-password">Password</label><input id="register-password" name="password" type="password" required disabled={pending} /><button disabled={pending}>{pending ? "Creating account…" : "Create account"}</button>{pending && <p role="status">Creating your account…</p>}{error && <p role="alert">{error}</p>}<p className="auth-link">Already have an account? <Link to="/login">Sign in</Link></p></form></AuthPage>;
}

const fields = ["display_name", "headline", "current_role", "location", "summary", "career_goal", "job_search_criteria", "preferred_email", "phone", "linkedin_url", "github_url", "portfolio_url"] as const;
const longFields = new Set(["summary", "career_goal", "job_search_criteria"]);
const fieldLabels: Record<(typeof fields)[number], string> = {
  display_name: "Display name", headline: "Headline", current_role: "Current role", location: "Location", summary: "Summary", career_goal: "Career goal", job_search_criteria: "Job-search criteria", preferred_email: "Preferred email", phone: "Phone", linkedin_url: "LinkedIn URL", github_url: "GitHub URL", portfolio_url: "Portfolio URL",
};
type Values = Record<(typeof fields)[number], string>;
const emptyValues = (): Values => Object.fromEntries(fields.map((field) => [field, ""])) as Values;

export function ProfileForm({ profile, onSaved, onFeedbackClear }: { profile: Profile | null; onSaved: () => void; onFeedbackClear: () => void }) {
  const { api } = useAuth();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [values, setValues] = useState<Values>(emptyValues);
  useEffect(() => setValues(Object.fromEntries(fields.map((field) => [field, profile?.[field] ?? ""])) as Values), [profile]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError("");
    onFeedbackClear();
    try { await api.request("/api/v1/profile", { method: profile ? "PATCH" : "POST", body: JSON.stringify(values) }); onSaved(); }
    catch { setError("Profile could not be saved."); }
    finally { pendingRef.current = false; setPending(false); }
  }
  const change = (field: (typeof fields)[number], value: string) => { onFeedbackClear(); setValues((current) => ({ ...current, [field]: value })); };
  return <section className="card profile-card"><form className="form-stack" onSubmit={submit}><div><h2>{profile ? "Edit profile" : "Create profile"}</h2><p className="muted">All profile fields are optional and can be updated later.</p></div><div className="profile-fields">{fields.map((field) => <div className={longFields.has(field) ? "field field-wide" : "field"} key={field}><label htmlFor={`profile-${field}`}>{fieldLabels[field]}</label>{longFields.has(field) ? <textarea id={`profile-${field}`} name={field} value={values[field]} disabled={pending} onChange={(event) => change(field, event.target.value)} /> : <input id={`profile-${field}`} name={field} value={values[field]} disabled={pending} onChange={(event) => change(field, event.target.value)} />}</div>)}</div><button disabled={pending}>{pending ? "Saving…" : "Save profile"}</button>{pending && <p role="status">Saving profile…</p>}{error && <p role="alert">{error}</p>}</form></section>;
}

function OnboardingCard({ status, error }: { status: OnboardingStatus | undefined; error: string }) {
  if (!status) return <section className="card onboarding-card"><h2>Getting started</h2>{error ? <p role="alert">{error}</p> : <p className="muted">Loading onboarding status…</p>}</section>;
  const adviserLabel = !status.candidate_context_ready ? "Complete CV first" : !status.adviser.intake_exists ? "Start Career Adviser" : status.adviser.assessment_status === "stale" ? "Reassessment needed" : status.adviser.assessment_status === "review_ready" ? "Review adviser assessment" : status.adviser.assessment_status === "confirmed" ? "Adviser assessment current" : "Intake saved";
  return <section className="card onboarding-card"><h2>Getting started</h2>{error && <p role="alert">{error}</p>}<ol className="onboarding-list"><li><strong>Account</strong><span>Ready</span></li><li><strong>Profile</strong><span>{status.profile_exists ? "Saved" : "Not saved yet"}</span></li><li><strong>CV</strong><Link to="/cv">{status.latest_cv_draft?.state === "confirmed" ? "Confirmed" : "Continue CV onboarding"}</Link></li><li>{status.candidate_context_ready ? <><strong>Career Adviser</strong><Link to="/adviser">{adviserLabel}</Link></> : <><strong>Career Adviser</strong><span>{adviserLabel}</span></>}</li></ol>{status.candidate_context_ready && status.latest_cv_draft && status.latest_cv_draft.state !== "confirmed" && <p className="notice">Active candidate profile ready; newer CV update awaiting review.</p>}</section>;
}

function Home() {
  const { api, logout, user } = useAuth();
  const [status, setStatus] = useState<OnboardingStatus | undefined>(undefined);
  const [profile, setProfile] = useState<Profile | null | undefined>(undefined);
  const [statusError, setStatusError] = useState("");
  const [profileError, setProfileError] = useState("");
  const [profileNotice, setProfileNotice] = useState("");
  const statusRequest = useRef(0);
  const profileRequest = useRef(0);
  const loadStatus = async () => {
    const request = ++statusRequest.current;
    setStatus(undefined);
    setStatusError("");
    try {
      const found = await api.request<OnboardingStatus>("/api/v1/onboarding/status");
      if (request === statusRequest.current) setStatus(found);
    } catch {
      if (request === statusRequest.current) {
        setStatus(undefined);
        setStatusError("Onboarding status is unavailable.");
      }
    }
  };
  const loadProfile = async () => {
    const request = ++profileRequest.current;
    setProfile(undefined);
    setProfileError("");
    try {
      const found = await api.request<Profile>("/api/v1/profile");
      if (request === profileRequest.current) setProfile(found);
    } catch (e) {
      if (request !== profileRequest.current) return;
      if (e instanceof ApiError && e.status === 404) setProfile(null);
      else {
        setProfile(undefined);
        setProfileError("Profile is unavailable.");
      }
    }
  };
  const load = () => { void loadStatus(); void loadProfile(); };
  useEffect(() => { load(); }, []);
  const profileSaved = () => { setProfileNotice("Profile saved successfully."); load(); };
  return <AppShell><header className="workspace-header"><div><p className="eyebrow">Career workspace</p><h1>Welcome {user?.email}</h1></div><button className="button-secondary" onClick={logout}>Sign out</button></header><main className="workspace"><OnboardingCard status={status} error={statusError} /><section className="profile-area">{profileError && <p role="alert">{profileError}</p>}{profileNotice && <p role="status">{profileNotice}</p>}{profile === undefined ? <p className="muted">Loading profile…</p> : <ProfileForm profile={profile} onSaved={profileSaved} onFeedbackClear={() => setProfileNotice("")} />}</section></main></AppShell>;
}

export function App() { return <Routes><Route path="/login" element={<Login />} /><Route path="/register" element={<Register />} /><Route path="/" element={<Protected><Home /></Protected>} /><Route path="/cv" element={<Protected><AppShell><CvPage /></AppShell></Protected>} /><Route path="/adviser" element={<Protected><AppShell><AdviserPage /></AppShell></Protected>} /><Route path="/jobs" element={<Protected><AppShell><JobsPage /></AppShell></Protected>} /><Route path="/jobs/searches" element={<Protected><AppShell><JobsSearchesPage /></AppShell></Protected>} /><Route path="/applications" element={<Protected><AppShell><ApplicationsPage /></AppShell></Protected>} /><Route path="/applications/:preparationId" element={<Protected><AppShell><ApplicationDetailPage /></AppShell></Protected>} /><Route path="/tracking" element={<Protected><AppShell><TrackingPage /></AppShell></Protected>} /><Route path="/tracking/:trackingId" element={<Protected><AppShell><TrackingDetailPage /></AppShell></Protected>} /><Route path="/settings" element={<Protected><Navigate to="/settings/ai" replace /></Protected>} /><Route path="/settings/ai" element={<Protected><AppShell><AiSettingsPage /></AppShell></Protected>} /><Route path="*" element={<Navigate to="/" replace />} /></Routes>; }

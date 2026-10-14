import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, type PasswordChangeResult, type PasswordPolicy } from "./api";
import { useAuth } from "./auth";
import { SettingsNavigation } from "./SettingsNavigation";

type Notice = { role: "status" | "alert"; text: string };
type Authority = { ownerId: string | null; epoch: number; generation: number };

function passwordError(error: ApiError): string {
  if (error.detail === "current_password_incorrect") return "Current password is incorrect.";
  if (error.detail === "new_password_must_differ") return "Choose a new password that differs from your current password.";
  if (error.detail === "password_too_short") return "The new password is shorter than the current minimum.";
  if (error.detail === "password_too_long") return "The new password is longer than the current maximum.";
  if (error.detail === "password_contains_whitespace") return "The new password cannot contain whitespace.";
  if (error.detail === "password_too_common") return "Choose a less common password.";
  if (error.status === 409) return "Your password changed in another request. Sign in again with the current password.";
  if (error.status === 422) return "The new password does not meet the current password requirements.";
  if (error.status >= 500) return "We could not confirm whether the password change completed. No automatic retry was made; try signing in with the new password to check, or retry the change.";
  return "Your password could not be changed. Review the fields and try again.";
}

export function AccountSecurityPage() {
  const { api, user, logoutWithNotice } = useAuth();
  const navigate = useNavigate();
  const ownerId = user?.id ?? null;
  const epoch = api.sessionEpoch();
  const authorityRef = useRef<Authority>({ ownerId, epoch, generation: 0 });
  if (authorityRef.current.ownerId !== ownerId || authorityRef.current.epoch !== epoch) {
    authorityRef.current = { ownerId, epoch, generation: authorityRef.current.generation + 1 };
  }
  const [policy, setPolicy] = useState<PasswordPolicy | null>(null);
  const [policyState, setPolicyState] = useState<"loading" | "ready" | "error">("loading");
  const [policyAttempt, setPolicyAttempt] = useState(0);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [confirmationTouched, setConfirmationTouched] = useState(false);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const pendingRef = useRef(false);

  useEffect(() => {
    const generation = authorityRef.current.generation;
    let active = true;
    setCurrentPassword(""); setNewPassword(""); setConfirmation("");
    setConfirmationTouched(false); setNotice(null); setPending(false); pendingRef.current = false;
    setPolicy(null); setPolicyState("loading");
    void api.request<PasswordPolicy>("/api/v1/auth/password-policy", {}, false).then((loaded) => {
      if (active && authorityRef.current.generation === generation && api.sessionEpoch() === epoch) {
        setPolicy(loaded); setPolicyState("ready");
      }
    }).catch(() => {
      if (active && authorityRef.current.generation === generation && api.sessionEpoch() === epoch) {
        setPolicyState("error");
      }
    });
    return () => { active = false; };
  }, [api, ownerId, epoch, policyAttempt]);

  const passwordLength = Array.from(newPassword).length;
  const tooShort = policy !== null && passwordLength < policy.min_length;
  const tooLong = policy !== null && passwordLength > policy.max_length;
  // Keep in sync with Python str.isspace() without imposing UTF-16 length limits.
  const containsWhitespace = /[\p{Z}\u0009-\u000d\u001c-\u001f\u0085]/u.test(newPassword);
  const hasWhitespace = policy !== null && !policy.whitespace_allowed && containsWhitespace;
  const passwordsDoNotMatch = confirmation !== newPassword;
  const confirmationMismatch = confirmationTouched && passwordsDoNotMatch;
  const clientInvalid = tooShort || tooLong || hasWhitespace || passwordsDoNotMatch;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pendingRef.current || !policy || policyState !== "ready") return;
    setConfirmationTouched(true);
    setNotice(null);
    if (clientInvalid) {
      setNotice({ role: "alert", text: passwordsDoNotMatch
        ? "The new password and confirmation do not match."
        : "The new password does not meet the current password requirements." });
      return;
    }
    const authority = authorityRef.current;
    const stillCurrent = () => authorityRef.current.generation === authority.generation
      && authorityRef.current.ownerId === authority.ownerId
      && api.sessionEpoch() === authority.epoch;
    pendingRef.current = true;
    setPending(true);
    try {
      await api.request<PasswordChangeResult>("/api/v1/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      if (!stillCurrent()) return;
      setCurrentPassword(""); setNewPassword(""); setConfirmation("");
      logoutWithNotice("Password updated. Sign in again; existing sessions have been signed out.");
      navigate("/login", { replace: true });
    } catch (error) {
      if (error instanceof ApiError && error.sessionExpired) {
        navigate("/login", { replace: true, state: { notice: "Your session expired. Sign in again to continue." } });
        return;
      }
      if (!stillCurrent()) return;
      if (error instanceof ApiError && error.status === 409) {
        logoutWithNotice("Your password changed in another request. Sign in with the current password.");
        navigate("/login", { replace: true });
        return;
      }
      if (error instanceof ApiError) {
        if (error.detail === "current_password_incorrect") setCurrentPassword("");
        setNotice({ role: "alert", text: passwordError(error) });
      } else {
        setNotice({ role: "alert", text: "We could not confirm the password change because the service is unavailable. No automatic retry was made; you can try again or sign in with the new password to check." });
      }
    } finally {
      if (stillCurrent()) { pendingRef.current = false; setPending(false); }
    }
  }

  return <main className="ai-settings-page account-security-page">
    <header className="workspace-header"><div><p className="eyebrow"><Link to="/settings/security">Settings</Link> / Account Security</p><h1>Account Security</h1><p>Change the password for {user?.email ?? "your account"}.</p></div></header>
    <SettingsNavigation current="security" />
    {notice && <p className={notice.role === "alert" ? "profile-warning" : "profile-notice"} role={notice.role}>{notice.text}</p>}
    <section className="card ai-state-card" aria-labelledby="change-password-heading">
      <h2 id="change-password-heading">Change password</h2>
      <p>Your current password is required. After a successful change, you’ll need to sign in again and other active sessions will be signed out.</p>
      {policyState === "loading" && <p role="status">Loading the current password requirements…</p>}
      {policyState === "error" && <div><p role="alert">Password requirements could not be loaded. Reload them before changing your password.</p><button type="button" className="button-secondary" onClick={() => setPolicyAttempt((value) => value + 1)}>Reload password requirements</button></div>}
      {policy && <p className="muted">Use {policy.min_length}–{policy.max_length} Unicode characters{policy.whitespace_allowed ? "" : ", with no whitespace"}{policy.common_passwords_rejected ? "; common passwords are rejected" : ""}{policy.composition_requirements.length ? `; ${policy.composition_requirements.join(", ")}` : ""}.</p>}
      <form className="settings-form" onSubmit={submit} aria-busy={pending}>
        <label htmlFor="security-current-password">Current password</label>
        <input id="security-current-password" name="current_password" type="password" autoComplete="current-password" required disabled={pending || policyState !== "ready"} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
        <label htmlFor="security-new-password">New password</label>
        <input id="security-new-password" name="new_password" type="password" autoComplete="new-password" required disabled={pending || policyState !== "ready"} value={newPassword} aria-describedby="security-password-requirements" onChange={(event) => setNewPassword(event.target.value)} />
        {policy && <span id="security-password-requirements" className="muted">At least {policy.min_length} and no more than {policy.max_length} Unicode characters{policy.whitespace_allowed ? "" : "; no whitespace"}.</span>}
        <label htmlFor="security-confirm-password">Confirm new password</label>
        <input id="security-confirm-password" name="confirm_password" type="password" autoComplete="new-password" required disabled={pending || policyState !== "ready"} value={confirmation} aria-invalid={confirmationMismatch} onBlur={() => setConfirmationTouched(true)} onChange={(event) => { setConfirmation(event.target.value); setConfirmationTouched(true); }} />
        {confirmationMismatch && <p className="ai-validation" role="alert">The new password and confirmation do not match.</p>}
        {pending && <p role="status">Changing password…</p>}
        <button type="submit" disabled={pending || policyState !== "ready" || !policy || !currentPassword || !newPassword || !confirmation || clientInvalid}>
          {pending ? "Changing password…" : "Change password"}
        </button>
      </form>
    </section>
  </main>;
}

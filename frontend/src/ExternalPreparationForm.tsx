import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, type ApplicationPreparation, type ApplicationPrepareRequest } from "./api";
import { useAuth } from "./auth";
import { createApplicationPreparation, readPreparationPrerequisites } from "./applicationPreparationController";

type Prerequisite = "checking" | "ready" | "candidate_info_missing" | "profile_missing" | "unavailable";
type SourceMode = "text" | "url";

const MIN_JOB_TEXT_CODE_POINTS = 100;
const MAX_JOB_TEXT_CODE_POINTS = 200_000;
const INSUFFICIENT_DETAIL = "Job detail is insufficient; provide job text.";

function codePointLength(value: string): number { return Array.from(value).length; }

function isHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && Boolean(parsed.hostname);
  } catch { return false; }
}

export function ExternalPreparationForm({ onHistoryRefresh }: { onHistoryRefresh: () => Promise<boolean> }) {
  const { api, user } = useAuth();
  const [mode, setMode] = useState<SourceMode>("text");
  const [jobText, setJobText] = useState("");
  const [jobUrl, setJobUrl] = useState("");
  const [pages, setPages] = useState<1 | 2 | 3>(2);
  const [includeCoverLetter, setIncludeCoverLetter] = useState(true);
  const [questions, setQuestions] = useState([""]);
  const [prerequisite, setPrerequisite] = useState<Prerequisite>("checking");
  const [prerequisiteOwner, setPrerequisiteOwner] = useState<string | undefined>();
  const [prerequisiteError, setPrerequisiteError] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<ApplicationPreparation | null>(null);
  const submitLock = useRef(false);
  const prerequisiteGeneration = useRef(0);
  const alive = useRef(false);
  const identityRef = useRef(user?.id);
  const previousOwner = useRef(user?.id);
  identityRef.current = user?.id;

  useEffect(() => {
    if (previousOwner.current === user?.id) return;
    previousOwner.current = user?.id;
    submitLock.current = false;
    setPending(false); setMode("text"); setJobText(""); setJobUrl(""); setPages(2);
    setIncludeCoverLetter(true); setQuestions([""]); setError(""); setCreated(null);
  }, [user?.id]);

  const refreshPrerequisites = async (): Promise<Prerequisite | null> => {
    const request = ++prerequisiteGeneration.current;
    const requestOwner = user?.id;
    setPrerequisiteOwner(requestOwner);
    setPrerequisite("checking");
    setPrerequisiteError("");
    const result = await readPreparationPrerequisites(api, { userId: requestOwner, getUserId: () => identityRef.current });
    if (!alive.current || request !== prerequisiteGeneration.current || identityRef.current !== requestOwner || result.state === "session_stale") return null;
    const resolved: Prerequisite = result.state === "candidate_not_ready" ? "candidate_info_missing" : result.state === "profile_missing" ? "profile_missing" : result.state === "ready" ? "ready" : "unavailable";
    setPrerequisite(resolved);
    if (resolved === "unavailable") setPrerequisiteError("Career-trans could not confirm the current preparation prerequisites. Retry before creating a preparation.");
    return resolved;
  };

  useEffect(() => {
    alive.current = true;
    void refreshPrerequisites();
    return () => { alive.current = false; prerequisiteGeneration.current += 1; };
  }, [api, user?.id]);

  const textCodePoints = codePointLength(jobText);
  const validText = textCodePoints >= MIN_JOB_TEXT_CODE_POINTS && textCodePoints <= MAX_JOB_TEXT_CODE_POINTS && Boolean(jobText.trim());
  const normalizedUrl = jobUrl.trim();
  const validUrl = Boolean(normalizedUrl) && isHttpUrl(normalizedUrl);
  const sourceValid = mode === "text" ? validText : validUrl;
  const currentPrerequisite = prerequisiteOwner === user?.id ? prerequisite : "checking";
  const canSubmit = currentPrerequisite === "ready" && !pending && sourceValid;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitLock.current || pending || currentPrerequisite !== "ready" || !sourceValid) return;
    const cleanedQuestions = questions.map((question) => question.trim()).filter(Boolean);
    if (cleanedQuestions.length > 8) return;
    const target = mode === "text" ? { job_text: jobText } : { job_url: normalizedUrl };
    const payload: ApplicationPrepareRequest = {
      target,
      target_pages: pages,
      include_cover_letter: includeCoverLetter,
      application_questions: cleanedQuestions,
    };
    const submitOwner = user?.id;
    submitLock.current = true;
    setPending(true);
    setError("");
    setCreated(null);
    try {
      const result = await createApplicationPreparation(api, payload, { userId: submitOwner, getUserId: () => identityRef.current });
      if (!alive.current || identityRef.current !== submitOwner || result.kind === "session_stale") return;
      if (result.kind === "locked") setError("A preparation request is already in progress. No new preparation was created.");
      else if (result.kind === "confirmed") { setCreated(result.value); void onHistoryRefresh(); }
      else if (result.kind === "uncertain") {
        let historyRefreshed = false;
        try { historyRefreshed = await onHistoryRefresh(); } catch { /* History and request outcomes are independent. */ }
        if (!alive.current || identityRef.current !== submitOwner) return;
        setError(historyRefreshed
          ? "The preparation request was interrupted. Career-trans cannot confirm from this response whether a preparation was created. Saved application history has been refreshed; entries remain ordinary saved history."
          : "The preparation request was interrupted. Career-trans cannot confirm from this response whether a preparation was created. Saved application history could not be confirmed as refreshed.");
      } else if (result.kind === "prerequisite_conflict") {
        const refreshed = result.prerequisites.state === "candidate_not_ready" ? "candidate_info_missing" : result.prerequisites.state === "profile_missing" ? "profile_missing" : result.prerequisites.state === "ready" ? "ready" : "unavailable";
        setPrerequisiteOwner(submitOwner); setPrerequisite(refreshed);
        if (refreshed === "unavailable") setPrerequisiteError("Career-trans could not confirm the current preparation prerequisites. Retry before creating a preparation.");
        else if (refreshed === "ready") setError("Career-trans could not complete this preparation with the current application data. No preparation success was confirmed.");
      } else if (result.kind === "target_unavailable") {
        setError(mode === "text"
          ? "Career-trans could not obtain enough usable job requirements from this job description. Check that the full vacancy text is included."
          : "Career-trans could not obtain enough usable vacancy detail from this URL. Try switching to Paste job description and provide the full text.");
      } else if (result.kind === "failed" && result.error instanceof ApiError && result.error.status === 422 && result.error.detail === INSUFFICIENT_DETAIL) {
        setError(mode === "text"
          ? "Career-trans could not extract enough usable job requirements from this job description. Check that the full vacancy text is included."
          : "Career-trans could not obtain enough usable vacancy detail from this URL. Try switching to Paste job description and provide the full text.");
      } else if (result.kind === "failed" && result.error instanceof ApiError && result.error.status === 422) {
        setError("Career-trans could not validate this preparation request. Review the entered vacancy and preparation options, then try again.");
      } else if (result.kind === "failed" && result.error instanceof ApiError && result.error.status === 503) {
        setError("Application preparation is temporarily unavailable. Your saved application history remains available.");
      } else {
        setError("Career-trans could not complete this preparation with the current application data. No preparation success was confirmed.");
      }
    } finally {
      submitLock.current = false;
      if (alive.current && identityRef.current === submitOwner) setPending(false);
    }
  };

  const retryPrerequisites = () => { setPrerequisiteError(""); void refreshPrerequisites(); };

  return <section className="card application-section external-preparation" aria-labelledby="external-preparation-heading">
    <h2 id="external-preparation-heading">Prepare for another vacancy</h2>
    <p className="muted">Prepare a vacancy directly here; it does not need to come from Career-trans job discovery.</p>
    {currentPrerequisite === "candidate_info_missing" && <p role="alert">Confirmed structured career information is required before preparing an application. Add it through the Profile revision workflow or confirm a CV. <Link to="/">Review your Profile</Link>.</p>}
    {currentPrerequisite === "profile_missing" && <p role="alert">An application display name is required before preparing an application. <Link to="/">Update your profile</Link>.</p>}
    {currentPrerequisite === "unavailable" && <div role="alert">{prerequisiteError} <button type="button" className="button-secondary" onClick={retryPrerequisites} disabled={pending}>Retry prerequisites</button></div>}
    {currentPrerequisite === "checking" && <p role="status">Checking candidate readiness and application display name…</p>}
    {currentPrerequisite !== "unavailable" && <button type="button" className="button-secondary" onClick={retryPrerequisites} disabled={pending}>{currentPrerequisite === "ready" ? "Re-check prerequisites" : "Retry prerequisite check"}</button>}
    <form className="preparation-form" aria-label="Prepare another vacancy" onSubmit={(event) => void submit(event)}>
      <fieldset disabled={pending}>
        <legend>Vacancy source</legend>
        <label><input type="radio" name="external-source-mode" value="text" checked={mode === "text"} onChange={() => { setMode("text"); setError(""); }} /> Paste job description (recommended)</label>
        <label><input type="radio" name="external-source-mode" value="url" checked={mode === "url"} onChange={() => { setMode("url"); setError(""); }} /> Job URL</label>
      </fieldset>
      {mode === "text" ? <>
        <label htmlFor="external-job-text">Job description</label>
        <textarea id="external-job-text" aria-describedby="external-job-text-guidance" value={jobText} onChange={(event) => { setJobText(event.target.value); setError(""); }} disabled={pending} />
        <p className="muted" id="external-job-text-guidance">Paste the full vacancy text. Enter {MIN_JOB_TEXT_CODE_POINTS.toLocaleString()}–{MAX_JOB_TEXT_CODE_POINTS.toLocaleString()} Unicode characters. Current length: {textCodePoints.toLocaleString()}.{jobText.length > 0 && !jobText.trim() ? " Blank-only text cannot be submitted." : textCodePoints < MIN_JOB_TEXT_CODE_POINTS && jobText ? ` Add at least ${MIN_JOB_TEXT_CODE_POINTS - textCodePoints} more characters.` : textCodePoints > MAX_JOB_TEXT_CODE_POINTS ? ` Remove at least ${textCodePoints - MAX_JOB_TEXT_CODE_POINTS} characters.` : ""}</p>
      </> : <>
        <label htmlFor="external-job-url">Public job URL</label>
        <input id="external-job-url" type="url" inputMode="url" aria-describedby="external-job-url-guidance" value={jobUrl} onChange={(event) => { setJobUrl(event.target.value); setError(""); }} disabled={pending} />
        <p className="muted" id="external-job-url-guidance">Enter an absolute HTTP or HTTPS vacancy URL. Career-trans retrieves and validates it securely; this page does not fetch it.</p>
      </>}
      <label htmlFor="external-target-pages">Target CV pages</label>
      <select id="external-target-pages" value={pages} onChange={(event) => setPages(Number(event.target.value) as 1 | 2 | 3)} disabled={pending}><option value={1}>1</option><option value={2}>2</option><option value={3}>3</option></select>
      <label><input type="checkbox" checked={includeCoverLetter} onChange={(event) => setIncludeCoverLetter(event.target.checked)} disabled={pending} /> Include a cover letter</label>
      <fieldset disabled={pending}>
        <legend>Application questions (up to 8)</legend>
        {questions.map((question, index) => <div className="question-input" key={index}>
          <label htmlFor={`external-question-${index}`}>Question {index + 1}</label>
          <textarea id={`external-question-${index}`} value={question} onChange={(event) => setQuestions((old) => old.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} />
          {questions.length > 1 && <button type="button" className="button-secondary" onClick={() => setQuestions((old) => old.filter((_, itemIndex) => itemIndex !== index))}>Remove question</button>}
        </div>)}
        {questions.length < 8 && <button type="button" className="button-secondary" onClick={() => setQuestions((old) => [...old, ""])}>Add question</button>}
      </fieldset>
      <button type="submit" disabled={!canSubmit}>{pending ? "Preparing…" : "Create preparation"}</button>
      {pending && <p role="status">Preparing application… this may take several minutes.</p>}
      {error && <p role="alert">{error}</p>}
      {created && <p role="status">Preparation saved. <Link to={`/applications/${encodeURIComponent(created.id)}`}>Review this preparation</Link>.</p>}
    </form>
  </section>;
}

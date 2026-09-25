import { ChangeEvent, useEffect, useRef, useState } from "react";
import { ApiError, useAuth } from "./auth";
import type { CandidateCVData, CVIngestionDraft, CVIngestionHistoryItem, CVIngestionHistoryRead, OnboardingStatus } from "./api";
import { RuntimeAttributionPanel } from "./RuntimeAttributionPanel";

type CVData = CandidateCVData;
type Draft = CVIngestionDraft;

const extensions = [".pdf", ".docx", ".md", ".markdown", ".json"];
const maxFile = 5 * 1024 * 1024;
const maxTotal = 15 * 1024 * 1024;
const credentialTypes = [
  ["certification", "Certification"],
  ["professional_qualification", "Professional qualification"],
  ["professional_registration", "Professional registration"],
  ["formal_training", "Formal training"],
  ["professional_membership", "Professional membership"],
  ["other", "Other"],
] as const;
const emptyData = (): CVData => ({ employment: [], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] });
const friendlyError = (operation: "upload" | "interpret" | "save" | "confirm", error: unknown) => {
  const status = error instanceof ApiError ? error.status : 0;
  if (operation === "upload") return status === 422 ? "Those files could not be uploaded. Check the file type and size limits." : "CV upload is unavailable. Please try again.";
  if (operation === "interpret") return status === 409 ? "This draft can no longer be interpreted. Refresh its status." : "CV interpretation is temporarily unavailable. Please try again later.";
  if (operation === "save") return status === 409 ? "Your changes could not be saved. Career Evidence can only be retained or excluded unchanged." : "CV review changes could not be saved.";
  return status === 404 || status === 409 ? "This CV draft is no longer ready to confirm. Refresh and try again." : "CV confirmation could not be completed.";
};

function EvidenceCards({ data, onExclude, disabled }: { data: CVData; onExclude: (index: number) => void; disabled: boolean }) {
  return <section className="cv-section"><h2>Career Evidence</h2><p className="muted">Evidence from your uploaded CV is read-only. You can exclude an item before confirming.</p>
    {data.evidence.length === 0 ? <p className="muted">No extracted Career Evidence.</p> : data.evidence.map((item, index) => <article className="evidence-card" key={`${item.title}-${index}`}><p><strong>{item.evidence_type}</strong> · From uploaded CV</p><h3>{item.title}</h3><p>{item.text}</p>{item.skills.length > 0 && <p className="muted">Skills: {item.skills.join(", ")}</p>}<button className="button-secondary" type="button" disabled={disabled} onClick={() => onExclude(index)}>Exclude</button></article>)}</section>;
}

const fields: Record<string, string[]> = {
  employment: ["employer", "title", "start_date", "end_date", "location", "description"],
  education: ["institution", "qualification", "field_of_study", "description"],
  credentials: ["name", "credential_type", "issuer", "issued_date", "expiry_date", "status", "description"],
  skills: ["name", "category"], projects: ["name", "description", "skills"], achievements: ["text"],
};
const labels: Record<string, string> = { employment: "Employment", education: "Education", credentials: "Credentials", skills: "Skills", projects: "Projects", achievements: "Achievements" };
const singularLabels: Record<string, string> = { employment: "Employment", education: "Education", credentials: "Credential", skills: "Skill", projects: "Project", achievements: "Achievement" };
const emptyItem = (section: string): Record<string, unknown> => Object.fromEntries(fields[section].map((field) => [
  field,
  field === "skills" ? [] : field === "credential_type" ? "certification" : "",
]));

function StructuredSection({ section, data, onChange, disabled }: { section: string; data: CVData; onChange: (next: CVData) => void; disabled: boolean }) {
  const values = data[section as keyof CVData] as Array<Record<string, unknown>>;
  const update = (index: number, field: string, value: string) => {
    const next = values.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: field === "skills" ? value.split(",").map((part) => part.trim()).filter(Boolean) : value } : item);
    onChange({ ...data, [section]: next });
  };
  return <section className="cv-section"><div className="section-heading"><h2>{labels[section]}</h2><button type="button" className="button-secondary" disabled={disabled} onClick={() => onChange({ ...data, [section]: [...values, emptyItem(section)] })}>Add {singularLabels[section]}</button></div>
    {values.map((item, index) => <fieldset className="structured-item" key={index}><legend>{singularLabels[section]} {index + 1}</legend><div className="profile-fields">{fields[section].map((field) => <div className={field === "description" ? "field field-wide" : "field"} key={field}><label>{field.replaceAll("_", " ")}{field === "credential_type"
      ? <select disabled={disabled} value={String(item[field] ?? "certification")} onChange={(event) => update(index, field, event.target.value)}>{credentialTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>
      : <input disabled={disabled} value={Array.isArray(item[field]) ? (item[field] as string[]).join(", ") : String(item[field] ?? "")} onChange={(event) => update(index, field, event.target.value)} />}</label></div>)}</div><button className="button-danger" type="button" disabled={disabled} onClick={() => onChange({ ...data, [section]: values.filter((_, itemIndex) => itemIndex !== index) })}>Remove</button></fieldset>)}
  </section>;
}

export function CvPage() {
  const { api } = useAuth();
  const [status, setStatus] = useState<OnboardingStatus | undefined>();
  // undefined = status/draft is loading; null = authoritative or explicit upload mode.
  const [draft, setDraft] = useState<Draft | null | undefined>(undefined);
  const [files, setFiles] = useState<File[]>([]);
  const [review, setReview] = useState<CVData>(emptyData());
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<"upload" | "interpret" | "save" | "confirm" | "" >("");
  const [supersedePrompt, setSupersedePrompt] = useState(false);
  const [confirmReplacementPrompt, setConfirmReplacementPrompt] = useState(false);
  const [history, setHistory] = useState<CVIngestionHistoryItem[] | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyTruncated, setHistoryTruncated] = useState(false);
  const [sourceDraft, setSourceDraft] = useState<Draft | null>(null);
  const [sourceError, setSourceError] = useState("");
  const generation = useRef(0);
  const sourceGeneration = useRef(0);
  const recoveredMissing = useRef(false);

  const loadStatus = async (retryMissing = false) => {
    const request = ++generation.current;
    sourceGeneration.current += 1; setHistory(null); setSourceDraft(null); setConfirmReplacementPrompt(false);
    if (!retryMissing) recoveredMissing.current = false;
    setStatus(undefined); setDraft(undefined); setError(""); setSupersedePrompt(false);
    try {
      const found = await api.request<OnboardingStatus>("/api/v1/onboarding/status");
      if (request !== generation.current) return;
      setStatus(found);
      if (!found.latest_cv_draft) { setDraft(null); setDirty(false); return; }
      await loadDraft(found.latest_cv_draft.id, retryMissing, request);
    } catch {
      if (request === generation.current) { setStatus(undefined); setDraft(undefined); setError("CV status is unavailable. Please retry."); }
    }
  };
  const loadDraft = async (id: string, retryMissing: boolean, request = ++generation.current) => {
    try {
      const found = await api.request<Draft>(`/api/v1/cv-ingestion/${id}`);
      if (request === generation.current) { setDraft(found); setReview(found.merged ?? emptyData()); setDirty(false); }
    } catch (caught) {
      if (request !== generation.current) return;
      if (caught instanceof ApiError && caught.status === 404 && !retryMissing && !recoveredMissing.current) { recoveredMissing.current = true; await loadStatus(true); return; }
      setError("The latest CV draft could not be found. Please start a new upload."); setDraft(null); setDirty(false);
    }
  };
  const loadHistory = async () => {
    setHistory(null);
    try {
      const result = await api.request<CVIngestionHistoryRead>("/api/v1/cv-ingestion?limit=20");
      setHistory(result.items); setHistoryTruncated(result.truncated);
    } catch { setHistory([]); setSourceError("CV source history is unavailable. Please retry."); }
  };
  const showSource = async (id: string) => {
    const request = ++sourceGeneration.current;
    setSourceDraft(null); setSourceError("");
    try {
      const found = await api.request<Draft>(`/api/v1/cv-ingestion/${id}`);
      if (request === sourceGeneration.current) setSourceDraft(found);
    } catch {
      if (request === sourceGeneration.current) setSourceError("This CV source could not be opened. Refresh the history and try again.");
    }
  };
  useEffect(() => { void loadStatus(); }, []);

  const chooseFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []);
    if (selected.length > 5) { setError("Choose no more than 5 files."); return; }
    if (selected.some((file) => !extensions.some((extension) => file.name.toLowerCase().endsWith(extension)))) { setError("Use PDF, DOCX, Markdown, or JSON files only."); return; }
    if (selected.some((file) => file.size === 0 || file.size > maxFile)) { setError("Each file must be non-empty and no larger than 5 MB."); return; }
    if (selected.reduce((total, file) => total + file.size, 0) > maxTotal) { setError("Selected files exceed the 15 MB total limit."); return; }
    setError(""); setFiles(selected);
  };
  const upload = async () => { if (!files.length || pending) return; setPending("upload"); setError(""); setNotice(""); try { const body = new FormData(); files.forEach((file) => body.append("files", file)); await api.request<Draft>("/api/v1/cv-ingestion/upload", { method: "POST", body }); setFiles([]); await loadStatus(); } catch (caught) { setError(friendlyError("upload", caught)); } finally { setPending(""); } };
  const interpret = async () => { if (!draft || pending) return; setPending("interpret"); setError(""); setNotice(""); try { await api.request<Draft>(`/api/v1/cv-ingestion/${draft.id}/interpret`, { method: "POST" }); await loadStatus(); } catch (caught) { setError(friendlyError("interpret", caught)); } finally { setPending(""); } };
  const save = async () => { if (!draft || pending) return; setPending("save"); setError(""); setNotice(""); try { const found = await api.request<Draft>(`/api/v1/cv-ingestion/${draft.id}`, { method: "PATCH", body: JSON.stringify(review) }); setDraft(found); setReview(found.merged ?? emptyData()); setDirty(false); setNotice("CV changes saved."); } catch (caught) { setError(friendlyError("save", caught)); } finally { setPending(""); } };
  const confirm = async () => { if (!draft || pending || dirty) return; setPending("confirm"); setError(""); setNotice(""); try { await api.request(`/api/v1/cv-ingestion/${draft.id}/confirm`, { method: "POST" }); await loadStatus(); } catch (caught) { setError(friendlyError("confirm", caught)); } finally { setPending(""); } };
  const requestConfirm = () => { if (status?.candidate_context_ready) setConfirmReplacementPrompt(true); else void confirm(); };
  const changeReview = (next: CVData) => { if (pending) return; setNotice(""); setReview(next); setDirty(true); };
  const beginNewUpload = () => { generation.current += 1; setDraft(null); setFiles([]); setReview(emptyData()); setDirty(false); setError(""); setNotice(""); setSupersedePrompt(false); };
  const requestNewUpload = () => {
    if (draft?.state === "uploaded" || draft?.state === "review_ready") setSupersedePrompt(true);
    else beginNewUpload();
  };
  const activePendingUpdate = Boolean(status?.candidate_context_ready && draft && draft.state !== "confirmed");

  if (status === undefined) return <main className="workspace cv-page"><p className="muted">Loading CV onboarding…</p>{error && <><p role="alert">{error}</p><button onClick={() => void loadStatus()}>Retry</button></>}</main>;
  if (status.latest_cv_draft && draft === undefined) return <main className="workspace cv-page"><section className="card"><h1>Loading your latest CV</h1><p className="muted">Resuming the CV draft saved to your account…</p></section></main>;
  if (draft === null) return <main className="workspace cv-page">{status.candidate_context_ready && <p className="notice">Your currently confirmed candidate profile remains active. No source file representation is retained for that profile; uploading a CV is optional.</p>}<Upload files={files} error={error} pending={pending === "upload"} onChoose={chooseFiles} onRemove={(index) => setFiles(files.filter((_, itemIndex) => itemIndex !== index))} onClear={() => setFiles([])} onUpload={() => void upload()} /><History history={history} open={historyOpen} truncated={historyTruncated} sourceDraft={sourceDraft} error={sourceError} latestId={status.latest_cv_draft?.id ?? null} onToggle={() => { const next = !historyOpen; setHistoryOpen(next); if (next && history === null) void loadHistory(); }} onView={(id) => void showSource(id)} /></main>;
  if (draft === undefined) return <main className="workspace cv-page"><section className="card"><h1>Loading your latest CV</h1><p className="muted">Resuming the CV draft saved to your account…</p></section></main>;

  const pendingMessage = pending === "interpret" ? "Interpreting CV…" : pending === "save" ? "Saving CV changes…" : pending === "confirm" ? "Confirming reviewed CV…" : "";
  return <main className="workspace cv-page">{pendingMessage && <p role="status">{pendingMessage}</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{activePendingUpdate && <p className="notice">Your currently confirmed candidate profile remains active until this newer CV is confirmed.</p>}
    <RuntimeAttributionPanel attribution={draft.runtime_attribution} boundary="cv" />
    {supersedePrompt && <section className="card supersede-warning" aria-label="Replace unfinished CV draft"><h2>Start a newer CV upload?</h2><p>This unfinished draft will remain stored, but the newer upload will become the draft resumed by onboarding.</p><div className="cv-actions"><button type="button" onClick={beginNewUpload}>Continue with new upload</button><button type="button" className="button-secondary" onClick={() => setSupersedePrompt(false)}>Keep current draft</button></div></section>}
    {draft.state === "uploaded" && <section className="card"><h1>CV uploaded</h1><p>Files: {draft.documents.map((item) => item.provenance.filename).join(", ")}</p><div className="cv-actions"><button disabled={Boolean(pending) || supersedePrompt} onClick={() => void interpret()}>{pending === "interpret" ? "Interpreting…" : "Interpret CV"}</button><button className="button-secondary" disabled={Boolean(pending) || supersedePrompt} onClick={requestNewUpload}>Start new CV upload</button></div></section>}
    {draft.state === "review_ready" && <section className="card cv-review"><div><h1>Review your CV</h1><p className="muted">Save changes before confirming. Confirming makes the backend-saved review your active candidate context.</p><button className="button-secondary" disabled={Boolean(pending) || supersedePrompt} onClick={requestNewUpload}>Start new CV upload</button></div>{Object.keys(fields).map((section) => <StructuredSection section={section} data={review} onChange={changeReview} disabled={Boolean(pending)} key={section} />)}<EvidenceCards data={review} disabled={Boolean(pending)} onExclude={(index) => changeReview({ ...review, evidence: review.evidence.filter((_, itemIndex) => itemIndex !== index) })} />{dirty && <p className="notice">You have unsaved review changes. Save them before confirming this CV.</p>}{confirmReplacementPrompt && <section className="card supersede-warning" role="alert"><h2>Replace your current career profile?</h2><p>Confirming this review will replace the structured CV information currently feeding your Profile and reconcile its active Career Evidence. The current profile stays active if you cancel.</p><div className="cv-actions"><button disabled={Boolean(pending)} onClick={() => void confirm()}>Replace current profile with this CV</button><button type="button" className="button-secondary" onClick={() => setConfirmReplacementPrompt(false)}>Cancel</button></div></section>}<div className="cv-actions"><button disabled={Boolean(pending) || !dirty} onClick={() => void save()}>{pending === "save" ? "Saving…" : "Save changes"}</button><button disabled={Boolean(pending) || dirty} onClick={requestConfirm}>{pending === "confirm" ? "Confirming…" : "Confirm reviewed CV"}</button></div></section>}
    {draft.state === "confirmed" && <section className="card"><h1>CV confirmed</h1><p>This CV review has been confirmed. Its structured information now feeds your current career profile.</p><a href="/">View current Profile</a><p><button onClick={requestNewUpload}>Update CV / Upload newer CV</button></p></section>}
    <SourceSummary draft={draft} />
    <History history={history} open={historyOpen} truncated={historyTruncated} sourceDraft={sourceDraft} error={sourceError} latestId={status.latest_cv_draft?.id ?? null} onToggle={() => { const next = !historyOpen; setHistoryOpen(next); if (next && history === null) void loadHistory(); }} onView={(id) => void showSource(id)} />
  </main>;
}

function SourceSummary({ draft }: { draft: Draft }) {
  return <section className="card"><h2>Uploaded source representation</h2><p className="muted">The original uploaded file is not stored. Extracted text and source details are retained for review.</p><ul>{draft.documents.map((document) => <li key={document.provenance.document_sha256}>{document.provenance.filename} · {document.provenance.media_type} · SHA-256 {document.provenance.document_sha256}</li>)}</ul>{draft.documents.map((document) => <details key={document.provenance.document_sha256}><summary>View extracted text: {document.provenance.filename}</summary>{(document.segments ?? []).map((segment) => <article key={segment.segment_id}><p className="muted">{segment.heading ?? "Source segment"}{segment.page_number ? ` · page ${segment.page_number}` : ""} · {segment.segment_id}</p><pre>{segment.text}</pre></article>)}</details>)}</section>;
}

function History({ history, open, truncated, sourceDraft, error, latestId, onToggle, onView }: { history: CVIngestionHistoryItem[] | null; open: boolean; truncated: boolean; sourceDraft: Draft | null; error: string; latestId: string | null; onToggle: () => void; onView: (id: string) => void }) {
  return <section className="card"><h2>CV source history</h2><button type="button" className="button-secondary" onClick={onToggle}>{open ? "Hide CV history" : "View CV history"}</button>{open && <><p className="muted">Historical source records are read-only. Each detail view shows extracted text, filenames, media types, segment identifiers, and document hashes. Original files are not stored.</p>{history === null ? <p role="status">Loading CV history…</p> : history.length === 0 ? <p className="muted">No retained CV source records.</p> : <ul>{history.map((item) => <li key={item.id}><strong>{item.filenames.join(", ") || "CV source"}</strong> · {item.state}{item.id === latestId ? " · Latest draft" : " · Historical"} · {new Date(item.created_at).toLocaleString()} · {item.document_count} document(s) <button type="button" className="button-secondary" onClick={() => onView(item.id)}>View source</button></li>)}</ul>}{truncated && <p className="muted">Showing the newest records in your history.</p>}{error && <p role="alert">{error}</p>}{sourceDraft && <section aria-label="Historical CV source"><h3>Read-only source: {sourceDraft.documents.map((item) => item.provenance.filename).join(", ")}</h3><p>State: {sourceDraft.state}. Original uploaded files are not stored.</p>{sourceDraft.documents.map((document, index) => <details key={`${document.provenance.document_sha256}-${index}`} open><summary>{document.provenance.filename} · {document.provenance.media_type} · SHA-256 {document.provenance.document_sha256}</summary>{(document.segments ?? []).map((segment) => <article key={segment.segment_id}><p className="muted">{segment.heading ?? "Source segment"}{segment.page_number ? ` · page ${segment.page_number}` : ""} · {segment.segment_id}</p><pre>{segment.text}</pre></article>)}</details>)}</section>}</>}</section>;
}

function Upload({ files, error, pending, onChoose, onRemove, onClear, onUpload }: { files: File[]; error: string; pending: boolean; onChoose: (event: ChangeEvent<HTMLInputElement>) => void; onRemove: (index: number) => void; onClear: () => void; onUpload: () => void }) {
  return <section className="card cv-upload"><h1>Upload your CV</h1><p className="muted">PDF, DOCX, Markdown, or JSON · up to 5 files · 5 MB each · 15 MB total.</p><label htmlFor="cv-files">CV files</label><input id="cv-files" type="file" multiple accept=".pdf,.docx,.md,.markdown,.json" onChange={onChoose} disabled={pending} />{files.length > 0 && <ul>{files.map((file, index) => <li key={`${file.name}-${index}`}>{file.name} <button type="button" className="button-secondary" disabled={pending} onClick={() => onRemove(index)}>Remove</button></li>)}</ul>}<button disabled={!files.length || pending} onClick={onUpload}>{pending ? "Uploading…" : "Upload CV"}</button>{pending && <p role="status">Uploading CV…</p>}{files.length > 0 && <button className="button-secondary" disabled={pending} onClick={onClear}>Clear selected files</button>}{error && <p role="alert">{error}</p>}</section>;
}

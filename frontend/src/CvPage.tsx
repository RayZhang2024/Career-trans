import { ChangeEvent, useEffect, useRef, useState } from "react";
import { ApiError, useAuth } from "./auth";
import type { OnboardingStatus } from "./api";

type Provenance = { document_sha256: string; segment_ids: string[]; source_kind: "cv" };
type Evidence = { evidence_type: string; title: string; text: string; skills: string[]; provenance: Provenance[] };
type CVData = {
  employment: Array<Record<string, unknown>>; education: Array<Record<string, unknown>>;
  credentials: Array<Record<string, unknown>>; skills: Array<Record<string, unknown>>;
  projects: Array<Record<string, unknown>>; achievements: Array<Record<string, unknown>>;
  evidence: Evidence[];
};
type Draft = { id: string; state: "uploaded" | "review_ready" | "confirmed"; documents: Array<{ provenance: { filename: string } }>; merged: CVData | null; created_at: string; updated_at: string };

const extensions = [".pdf", ".docx", ".md", ".markdown", ".json"];
const maxFile = 5 * 1024 * 1024;
const maxTotal = 15 * 1024 * 1024;
const emptyData = (): CVData => ({ employment: [], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] });
const friendlyError = (operation: "upload" | "interpret" | "save" | "confirm", error: unknown) => {
  const status = error instanceof ApiError ? error.status : 0;
  if (operation === "upload") return status === 422 ? "Those files could not be uploaded. Check the file type and size limits." : "CV upload is unavailable. Please try again.";
  if (operation === "interpret") return status === 409 ? "This draft can no longer be interpreted. Refresh its status." : "CV interpretation is temporarily unavailable. Please try again later.";
  if (operation === "save") return status === 409 ? "Your changes could not be saved. Career Evidence can only be retained or excluded unchanged." : "CV review changes could not be saved.";
  return status === 404 || status === 409 ? "This CV draft is no longer ready to confirm. Refresh and try again." : "CV confirmation could not be completed.";
};

function EvidenceCards({ data, onExclude }: { data: CVData; onExclude: (index: number) => void }) {
  return <section className="cv-section"><h2>Career Evidence</h2><p className="muted">Evidence from your uploaded CV is read-only. You can exclude an item before confirming.</p>
    {data.evidence.length === 0 ? <p className="muted">No extracted Career Evidence.</p> : data.evidence.map((item, index) => <article className="evidence-card" key={`${item.title}-${index}`}><p><strong>{item.evidence_type}</strong> · From uploaded CV</p><h3>{item.title}</h3><p>{item.text}</p>{item.skills.length > 0 && <p className="muted">Skills: {item.skills.join(", ")}</p>}<button className="button-secondary" type="button" onClick={() => onExclude(index)}>Exclude</button></article>)}</section>;
}

const fields: Record<string, string[]> = {
  employment: ["employer", "title", "start_date", "end_date", "location", "description"],
  education: ["institution", "qualification", "field_of_study", "description"],
  credentials: ["name", "credential_type", "issuer", "issued_date", "expiry_date", "status", "description"],
  skills: ["name", "category"], projects: ["name", "description", "skills"], achievements: ["text"],
};
const labels: Record<string, string> = { employment: "Employment", education: "Education", credentials: "Credentials", skills: "Skills", projects: "Projects", achievements: "Achievements" };
const singularLabels: Record<string, string> = { employment: "Employment", education: "Education", credentials: "Credential", skills: "Skill", projects: "Project", achievements: "Achievement" };
const emptyItem = (section: string): Record<string, unknown> => Object.fromEntries(fields[section].map((field) => [field, field === "skills" ? [] : ""]));

function StructuredSection({ section, data, onChange }: { section: string; data: CVData; onChange: (next: CVData) => void }) {
  const values = data[section as keyof CVData] as Array<Record<string, unknown>>;
  const update = (index: number, field: string, value: string) => {
    const next = values.map((item, itemIndex) => itemIndex === index ? { ...item, [field]: field === "skills" ? value.split(",").map((part) => part.trim()).filter(Boolean) : value } : item);
    onChange({ ...data, [section]: next });
  };
  return <section className="cv-section"><div className="section-heading"><h2>{labels[section]}</h2><button type="button" className="button-secondary" onClick={() => onChange({ ...data, [section]: [...values, emptyItem(section)] })}>Add {singularLabels[section]}</button></div>
    {values.map((item, index) => <fieldset className="structured-item" key={index}><legend>{singularLabels[section]} {index + 1}</legend><div className="profile-fields">{fields[section].map((field) => <div className={field === "description" ? "field field-wide" : "field"} key={field}><label>{field.replaceAll("_", " ")}<input value={Array.isArray(item[field]) ? (item[field] as string[]).join(", ") : String(item[field] ?? "")} onChange={(event) => update(index, field, event.target.value)} /></label></div>)}</div><button className="button-danger" type="button" onClick={() => onChange({ ...data, [section]: values.filter((_, itemIndex) => itemIndex !== index) })}>Remove</button></fieldset>)}
  </section>;
}

export function CvPage() {
  const { api } = useAuth();
  const [status, setStatus] = useState<OnboardingStatus | undefined>();
  const [draft, setDraft] = useState<Draft | undefined>();
  const [files, setFiles] = useState<File[]>([]);
  const [review, setReview] = useState<CVData>(emptyData());
  const [error, setError] = useState("");
  const [pending, setPending] = useState<"upload" | "interpret" | "save" | "confirm" | "" >("");
  const generation = useRef(0);
  const recoveredMissing = useRef(false);

  const loadStatus = async (retryMissing = false) => {
    const request = ++generation.current;
    setStatus(undefined); setError("");
    try {
      const found = await api.request<OnboardingStatus>("/api/v1/onboarding/status");
      if (request !== generation.current) return;
      setStatus(found);
      if (!found.latest_cv_draft) { setDraft(undefined); return; }
      await loadDraft(found.latest_cv_draft.id, retryMissing, request);
    } catch { if (request === generation.current) { setStatus(undefined); setError("CV status is unavailable. Please retry."); } }
  };
  const loadDraft = async (id: string, retryMissing: boolean, request = ++generation.current) => {
    try {
      const found = await api.request<Draft>(`/api/v1/cv-ingestion/${id}`);
      if (request === generation.current) { setDraft(found); setReview(found.merged ?? emptyData()); }
    } catch (caught) {
      if (request !== generation.current) return;
      if (caught instanceof ApiError && caught.status === 404 && !retryMissing && !recoveredMissing.current) { recoveredMissing.current = true; await loadStatus(true); return; }
      setError("The latest CV draft could not be found. Please start a new upload."); setDraft(undefined);
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
  const upload = async () => { if (!files.length || pending) return; setPending("upload"); setError(""); try { const body = new FormData(); files.forEach((file) => body.append("files", file)); const found = await api.request<Draft>("/api/v1/cv-ingestion/upload", { method: "POST", body }); setFiles([]); setDraft(found); setReview(emptyData()); await loadStatus(); } catch (caught) { setError(friendlyError("upload", caught)); } finally { setPending(""); } };
  const interpret = async () => { if (!draft || pending) return; setPending("interpret"); setError(""); try { const found = await api.request<Draft>(`/api/v1/cv-ingestion/${draft.id}/interpret`, { method: "POST" }); setDraft(found); setReview(found.merged ?? emptyData()); await loadStatus(); } catch (caught) { setError(friendlyError("interpret", caught)); } finally { setPending(""); } };
  const save = async () => { if (!draft || pending) return; setPending("save"); setError(""); try { const found = await api.request<Draft>(`/api/v1/cv-ingestion/${draft.id}`, { method: "PATCH", body: JSON.stringify(review) }); setDraft(found); setReview(found.merged ?? emptyData()); } catch (caught) { setError(friendlyError("save", caught)); } finally { setPending(""); } };
  const confirm = async () => { if (!draft || pending) return; setPending("confirm"); setError(""); try { await api.request(`/api/v1/cv-ingestion/${draft.id}/confirm`, { method: "POST" }); await loadStatus(); } catch (caught) { setError(friendlyError("confirm", caught)); } finally { setPending(""); } };
  const startNew = () => { generation.current += 1; setDraft(undefined); setFiles([]); setReview(emptyData()); setError(""); };
  const activePendingUpdate = Boolean(status?.candidate_context_ready && draft && draft.state !== "confirmed");

  if (status === undefined) return <main className="workspace cv-page"><p className="muted">Loading CV onboarding…</p>{error && <><p role="alert">{error}</p><button onClick={() => void loadStatus()}>Retry</button></>}</main>;
  if (!draft) return <main className="workspace cv-page"><Upload files={files} error={error} pending={pending === "upload"} onChoose={chooseFiles} onRemove={(index) => setFiles(files.filter((_, itemIndex) => itemIndex !== index))} onClear={() => setFiles([])} onUpload={() => void upload()} /></main>;
  return <main className="workspace cv-page">{error && <p role="alert">{error}</p>}{activePendingUpdate && <p className="notice">Your currently confirmed candidate profile remains active until this newer CV is confirmed.</p>}
    {draft.state === "uploaded" && <section className="card"><h1>CV uploaded</h1><p>Files: {draft.documents.map((item) => item.provenance.filename).join(", ")}</p><button disabled={Boolean(pending)} onClick={() => void interpret()}>Interpret CV</button><button className="button-secondary" disabled={Boolean(pending)} onClick={startNew}>Start new CV upload</button></section>}
    {draft.state === "review_ready" && <section className="card cv-review"><div><h1>Review your CV</h1><p className="muted">Save changes before confirming. Confirming makes this reviewed CV your active candidate context.</p><button className="button-secondary" disabled={Boolean(pending)} onClick={startNew}>Start new CV upload</button></div>{Object.keys(fields).map((section) => <StructuredSection section={section} data={review} onChange={setReview} key={section} />)}<EvidenceCards data={review} onExclude={(index) => setReview({ ...review, evidence: review.evidence.filter((_, itemIndex) => itemIndex !== index) })} /><div className="cv-actions"><button disabled={Boolean(pending)} onClick={() => void save()}>Save changes</button><button disabled={Boolean(pending)} onClick={() => void confirm()}>Confirm reviewed CV</button></div></section>}
    {draft.state === "confirmed" && <section className="card"><h1>CV confirmed</h1><p>Your reviewed CV is active in your candidate context.</p><button onClick={startNew}>Update CV / Upload newer CV</button></section>}
  </main>;
}

function Upload({ files, error, pending, onChoose, onRemove, onClear, onUpload }: { files: File[]; error: string; pending: boolean; onChoose: (event: ChangeEvent<HTMLInputElement>) => void; onRemove: (index: number) => void; onClear: () => void; onUpload: () => void }) {
  return <section className="card cv-upload"><h1>Upload your CV</h1><p className="muted">PDF, DOCX, Markdown, or JSON · up to 5 files · 5 MB each · 15 MB total.</p><label htmlFor="cv-files">CV files</label><input id="cv-files" type="file" multiple accept=".pdf,.docx,.md,.markdown,.json" onChange={onChoose} />{files.length > 0 && <ul>{files.map((file, index) => <li key={`${file.name}-${index}`}>{file.name} <button type="button" className="button-secondary" onClick={() => onRemove(index)}>Remove</button></li>)}</ul>}<button disabled={!files.length || pending} onClick={onUpload}>Upload CV</button>{files.length > 0 && <button className="button-secondary" disabled={pending} onClick={onClear}>Clear selected files</button>}{error && <p role="alert">{error}</p>}</section>;
}

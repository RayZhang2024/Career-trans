import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, type ApplicationPreparation, type ApplicationSourceRef } from "./api";
import { useAuth } from "./auth";

type LoadState<T> = { phase: "loading" | "ready" | "error"; value?: T; message?: string };
const sourceNames: Record<string, string> = {
  career_evidence: "Career evidence", employment: "Employment record", project: "Project record",
  education: "Education record", credential: "Credential record", candidate_profile: "Candidate profile",
  candidate_eligibility: "Eligibility information",
};
const sourceName = (value: string) => sourceNames[value] ?? "Application source";
const targetName = (value: ApplicationPreparation["target"]["source_kind"]) => value === "discovered_job" ? "Ranked opportunity" : value === "job_text" ? "Provided job description" : "Job URL";
const dateLabel = (value: string) => new Date(value).toLocaleString();

function EvidenceReferences({ refs }: { refs: ApplicationSourceRef[] }) {
  if (!refs.length) return null;
  return <details className="evidence-references"><summary>Evidence references</summary><ul>{refs.map((ref, index) => <li key={`${ref.source_type}:${ref.source_ref}:${index}`}><span>{sourceName(ref.source_type)}</span><code className="muted">{ref.source_ref}</code></li>)}</ul></details>;
}

function PreparationSummary({ value }: { value: ApplicationPreparation }) {
  return <article className="card application-summary">
    <p className="eyebrow">Created {dateLabel(value.created_at)}</p>
    <h2><Link to={`/applications/${encodeURIComponent(value.id)}`}>{value.target.title}</Link></h2>
    <p>{[value.target.company, value.target.location].filter(Boolean).join(" · ") || "Company and location not recorded"}</p>
    <dl className="metric-grid">
      <div><dt>Source</dt><dd>{targetName(value.target.source_kind)}</dd></div>
      <div><dt>CV pages</dt><dd>{value.result.actual_pdf_pages} / {value.result.target_pages} · {value.result.layout_status === "fit" ? "Fits target" : "Over target"}</dd></div>
      <div><dt>Cover letter</dt><dd>{value.result.cover_letter ? "Included" : "Not included"}</dd></div>
      <div><dt>Question answers</dt><dd>{value.result.answers.length}</dd></div>
    </dl>
    <Link to={`/applications/${encodeURIComponent(value.id)}`}>Review preparation</Link>
  </article>;
}

export function ApplicationsPage() {
  const { api } = useAuth();
  const [state, setState] = useState<LoadState<ApplicationPreparation[]>>({ phase: "loading" });
  const generation = useRef(0);
  const alive = useRef(false);
  const load = async () => {
    const current = ++generation.current;
    setState((old) => ({ phase: old.value ? "ready" : "loading", value: old.value }));
    try {
      const value = await api.request<ApplicationPreparation[]>("/api/v1/applications");
      if (alive.current && current === generation.current) setState({ phase: "ready", value });
    } catch {
      if (alive.current && current === generation.current) setState((old) => ({ phase: "error", value: old.value, message: "Application history is unavailable." }));
    }
  };
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; generation.current += 1; }; }, [api]);
  return <main className="applications-page">
    <header className="workspace-header"><div><p className="eyebrow">Career workspace</p><h1>Applications</h1><p className="muted">Review saved application-preparation snapshots and download their documents.</p></div><Link to="/jobs">Back to Jobs</Link></header>
    <section aria-label="Application preparation history">
      <div className="section-heading"><h2>Preparation history</h2><button type="button" className="button-secondary" onClick={() => void load()}>Refresh history</button></div>
      {state.phase === "loading" && !state.value && <p role="status">Loading application history…</p>}
      {state.phase === "error" && <p role="alert">{state.message}</p>}
      {state.value?.length === 0 && <p className="muted">No application preparations yet. Prepare one from a current ranked opportunity in Jobs.</p>}
      {!!state.value?.length && <div className="application-list">{state.value.map((value) => <PreparationSummary key={value.id} value={value} />)}</div>}
    </section>
  </main>;
}

function safeFilename(value: string | null, fallback: string): string {
  const leaf = (value ?? "").split(/[\\/]/).at(-1)?.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return leaf && leaf !== "." && leaf !== ".." ? leaf : fallback;
}

export function ApplicationDetailPage() {
  const { preparationId = "" } = useParams();
  const { api } = useAuth();
  const [state, setState] = useState<LoadState<ApplicationPreparation>>({ phase: "loading" });
  const [downloadError, setDownloadError] = useState("");
  const [downloading, setDownloading] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    let alive = true;
    const current = ++generation.current;
    setState({ phase: "loading" }); setDownloadError("");
    void api.request<ApplicationPreparation>(`/api/v1/applications/${encodeURIComponent(preparationId)}`).then((value) => {
      if (alive && current === generation.current) setState({ phase: "ready", value });
    }).catch((error: unknown) => {
      if (alive && current === generation.current && (error as Error)?.name !== "AbortError") setState({ phase: "error", message: error instanceof ApiError && error.status === 404 ? "This preparation is not available to this account." : "Application preparation detail is unavailable." });
    });
    return () => { alive = false; generation.current += 1; };
  }, [api, preparationId]);
  const download = async (kind: "cv.docx" | "cv.pdf" | "cover-letter.docx" | "cover-letter.pdf") => {
    if (downloading) return;
    setDownloading(kind); setDownloadError("");
    try {
      const result = await api.requestBlob(`/api/v1/applications/${encodeURIComponent(preparationId)}/${kind}`);
      const url = URL.createObjectURL(result.blob);
      const fallback = kind === "cv.docx" ? "CV.docx" : kind === "cv.pdf" ? "CV.pdf" : kind === "cover-letter.docx" ? "Cover_Letter.docx" : "Cover_Letter.pdf";
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = safeFilename(result.filename, fallback);
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      if ((error as Error)?.name !== "AbortError") setDownloadError(error instanceof ApiError ? "The document could not be downloaded." : "The document download was interrupted. Please try again.");
    } finally { setDownloading(null); }
  };
  const value = state.value;
  return <main className="applications-page application-detail-page">
    <header className="workspace-header"><div><p className="eyebrow">Historical application preparation</p><h1>{value?.target.title ?? "Application preparation"}</h1><p className="muted"><Link to="/applications">Back to Applications</Link></p></div></header>
    {state.phase === "loading" && <p role="status">Loading saved preparation…</p>}
    {state.phase === "error" && <p role="alert">{state.message}</p>}
    {value && <>
      <p className="notice">This is the saved snapshot created {dateLabel(value.created_at)}. It is not replaced by your current profile, CV, or vacancy information.</p>
      <section className="card application-section"><h2>Target used</h2><dl className="detail-grid">{([["Title", value.target.title], ["Company", value.target.company], ["Location", value.target.location], ["Work arrangement", value.target.work_arrangement], ["Employment type", value.target.employment_type], ["Source", targetName(value.target.source_kind)]] as Array<[string, string | null | undefined]>).map(([label, item]) => item ? <div key={label}><dt>{label}</dt><dd>{item}</dd></div> : null)}</dl>{value.target.public_url && <p><a href={value.target.public_url} target="_blank" rel="noopener noreferrer">Open saved public vacancy</a></p>}</section>
      <section className="card application-section"><h2>Identity used</h2><p>This contact information was saved with the historical preparation.</p><dl className="detail-grid">{([["Name", value.identity.display_name], ["Email", value.identity.email], ["Phone", value.identity.phone], ["Location", value.identity.location], ["LinkedIn", value.identity.linkedin_url], ["GitHub", value.identity.github_url], ["Portfolio", value.identity.portfolio_url]] as Array<[string, string | null | undefined]>).map(([label, item]) => item ? <div key={label}><dt>{label}</dt><dd>{label.endsWith("In") || label === "GitHub" || label === "Portfolio" ? <a href={item} target="_blank" rel="noopener noreferrer">{item}</a> : item}</dd></div> : null)}</dl></section>
      <section className="card application-section"><h2>Tailored CV</h2><p className="notice">{value.result.layout_status === "fit" ? `Fits the ${value.result.target_pages}-page target.` : `Over the ${value.result.target_pages}-page target; the document is ${value.result.actual_pdf_pages} pages.`} Actual PDF pages: {value.result.actual_pdf_pages}.</p><h3>Professional summary</h3><p>{value.result.cv.professional_summary}</p><EvidenceReferences refs={value.result.cv.summary_source_refs} />
        <TextValues title="Key skills" values={value.result.cv.key_skills} />
        <h3>Employment</h3>{value.result.cv.roles.map((role, index) => <article className="application-subsection" key={`${role.employer}-${role.title}-${index}`}><h4>{role.title} · {role.employer}</h4><p>{[role.start_date, role.end_date, role.location].filter(Boolean).join(" · ")}</p>{role.bullets.map((bullet, bulletIndex) => <div key={`${bulletIndex}-${bullet.text}`}><p>{bullet.text}</p><EvidenceReferences refs={bullet.source_refs} /></div>)}</article>)}
        <TextValues title="Selected projects" values={value.result.cv.selected_projects.map((project) => `${project.name}: ${project.text}`)} /><div>{value.result.cv.selected_projects.map((project, index) => <EvidenceReferences key={`${project.name}-${index}`} refs={project.source_refs} />)}</div>
        <TextValues title="Education" values={value.result.cv.education} /><TextValues title="Credentials" values={value.result.cv.credentials} />
      </section>
      <section className="card application-section"><h2>Application questions</h2>{value.result.answers.length ? value.result.answers.map((answer, index) => <article className="application-subsection" key={`${index}-${answer.question}`}><h3>{answer.question}</h3>{answer.status === "drafted" ? <><p>{answer.answer}</p><EvidenceReferences refs={answer.source_refs} /></> : <p className="muted">Unsupported by the available evidence — no answer was drafted.</p>}</article>) : <p className="muted">No application questions were supplied.</p>}</section>
      <section className="card application-section"><h2>Cover letter</h2>{value.result.cover_letter ? <><p className="application-prose">{value.result.cover_letter.body}</p><EvidenceReferences refs={value.result.cover_letter.source_refs} /></> : <p className="muted">No cover letter exists for this preparation.</p>}</section>
      <section className="card application-section"><h2>Downloads</h2><DownloadButtons downloading={downloading} onDownload={download} cover={Boolean(value.result.cover_letter)} />{downloadError && <p role="alert">{downloadError}</p>}</section>
    </>}
  </main>;
}

function TextValues({ title, values }: { title: string; values: string[] }) { return <section><h3>{title}</h3>{values.length ? <ul>{values.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : <p className="muted">None recorded.</p>}</section>; }
function DownloadButtons({ downloading, onDownload, cover }: { downloading: string | null; onDownload: (kind: "cv.docx" | "cv.pdf" | "cover-letter.docx" | "cover-letter.pdf") => void; cover: boolean }) {
  return <div className="card-actions"><button type="button" className="button-secondary" disabled={Boolean(downloading)} onClick={() => onDownload("cv.docx")}>{downloading === "cv.docx" ? "Downloading…" : "Download CV DOCX"}</button><button type="button" className="button-secondary" disabled={Boolean(downloading)} onClick={() => onDownload("cv.pdf")}>{downloading === "cv.pdf" ? "Downloading…" : "Download CV PDF"}</button>{cover && <><button type="button" className="button-secondary" disabled={Boolean(downloading)} onClick={() => onDownload("cover-letter.docx")}>{downloading === "cover-letter.docx" ? "Downloading…" : "Download cover letter DOCX"}</button><button type="button" className="button-secondary" disabled={Boolean(downloading)} onClick={() => onDownload("cover-letter.pdf")}>{downloading === "cover-letter.pdf" ? "Downloading…" : "Download cover letter PDF"}</button></>}</div>;
}

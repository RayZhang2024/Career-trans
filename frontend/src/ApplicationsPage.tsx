import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, type ApplicationPreparation, type ApplicationPreparationReview, type ApplicationSourceRef } from "./api";
import { useAuth } from "./auth";
import { buildRequirementReview, collectFinalCitationUsage, evidenceIdentity, snapshotStatusLabel, type CitationUsage, type RequirementReviewRow } from "./applicationReview";
import { PreparationTrackingPanel } from "./TrackingPage";
import { RuntimeAttributionPanel } from "./RuntimeAttributionPanel";
import { ExternalPreparationForm } from "./ExternalPreparationForm";

type LoadState<T> = { phase: "loading" | "ready" | "error"; value?: T; message?: string };
const sourceNames: Record<string, string> = {
  career_evidence: "Career evidence", employment: "Employment record", project: "Project record",
  education: "Education record", credential: "Credential record", candidate_profile: "Candidate profile",
  candidate_eligibility: "Eligibility information",
};
const sourceName = (value: string) => sourceNames[value] ?? "Application source";
const targetName = (value: ApplicationPreparation["target"]["source_kind"]) => value === "discovered_job" ? "Ranked opportunity" : value === "job_text" ? "Provided job description" : "Job URL";
const dateLabel = (value: string) => new Date(value).toLocaleString();

function EvidenceUsed({ refs, review, citations, reviewPending }: { refs: ApplicationSourceRef[]; review?: ApplicationPreparationReview; citations: CitationUsage; reviewPending: boolean }) {
  if (!refs.length) return null;
  const uniqueRefs = refs.filter((ref, index, all) => all.findIndex((item) => evidenceIdentity(item) === evidenceIdentity(ref)) === index);
  const snapshot = new Map((review?.evidence_sources ?? []).map((source) => [evidenceIdentity(source), source.text]));
  return <details className="evidence-references"><summary>Evidence used</summary><ul>{uniqueRefs.map((ref) => {
    const key = evidenceIdentity(ref);
    const inSnapshot = review?.evidence_snapshot_status === "available" && snapshot.has(key);
    const usage = citations.get(key) ?? [];
    return <li key={key}><span>{sourceName(ref.source_type)}</span> <code className="muted">{ref.source_ref}</code>
      {reviewPending && !review && <p className="muted">Loading historical evidence review…</p>}
      {review && review.evidence_snapshot_status === "legacy_unavailable" && <p className="muted">Human-readable historical preparation evidence was not stored for this older preparation.</p>}
      {review?.evidence_snapshot_status === "available" && inSnapshot && <p><strong>Preparation-time evidence:</strong> {snapshot.get(key)}</p>}
      {review?.evidence_snapshot_status === "available" && !inSnapshot && <p className="muted">Human-readable preparation evidence is unavailable for this citation.</p>}
      {!reviewPending && !review && <p className="muted">Historical source text is unavailable.</p>}
      {usage.length > 0 && <p><strong>Used in:</strong> {usage.join("; ")}</p>}
    </li>;
  })}</ul></details>;
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

function ReviewSummary({ value, review, rows }: { value: ApplicationPreparation; review: ApplicationPreparationReview; rows: RequirementReviewRow[] }) {
  const citations = collectFinalCitationUsage(value);
  const matchCounts = new Map<string, number>();
  for (const row of rows) if (row.match && !row.inconsistent) matchCounts.set(row.match.match_type, (matchCounts.get(row.match.match_type) ?? 0) + 1);
  const distribution = [...matchCounts.entries()].map(([name, count]) => `${name}: ${count}`).join(" · ") || "No valid persisted matches";
  return <section className="card application-section" aria-label="Preparation review summary">
    <h2>Preparation review summary</h2>
    <dl className="metric-grid">
      <div><dt>CV layout</dt><dd>{value.result.layout_status === "fit" ? "Fit" : "Overflow"}</dd></div>
      <div><dt>Target pages</dt><dd>{value.result.target_pages}</dd></div>
      <div><dt>Actual PDF pages</dt><dd>{value.result.actual_pdf_pages}</dd></div>
      <div><dt>Canonical requirements</dt><dd>{value.target.job_profile.requirements.length}</dd></div>
      <div><dt>Persisted match distribution</dt><dd>{distribution}</dd></div>
      <div><dt>Drafted answers</dt><dd>{value.result.answers.filter((answer) => answer.status === "drafted").length}</dd></div>
      <div><dt>Unsupported answers</dt><dd>{value.result.answers.filter((answer) => answer.status === "unsupported").length}</dd></div>
      {review.evidence_snapshot_status === "available" && <div><dt>Historical evidence sources</dt><dd>{review.evidence_sources.length}</dd></div>}
      <div><dt>Distinct cited sources in final materials</dt><dd>{citations.size}</dd></div>
    </dl>
    <p className="muted">{snapshotStatusLabel(review.evidence_snapshot_status)}. Traceability describes persisted references; it is not a quality or submission-readiness assessment.</p>
    <EvidenceUsed refs={value.result.cv.summary_source_refs} review={review} citations={citations} reviewPending={false} />
  </section>;
}

function stateLabel(state: RequirementReviewRow["evidence"][number]["state"]): string {
  if (state === "admitted_cited") return "Admitted to drafting · cited in final materials";
  if (state === "admitted_uncited") return "Admitted to drafting · not cited in final materials";
  if (state === "not_admitted") return "Not admitted to the bounded drafting context";
  return "Drafting-context admission unknown for this older preparation";
}

function RequirementReview({ value, review }: { value: ApplicationPreparation; review: ApplicationPreparationReview }) {
  const citations = collectFinalCitationUsage(value);
  const { rows, invalidIndexCount } = buildRequirementReview(value, review, citations);
  return <section className="card application-section" aria-label="Requirement review">
    <h2>Requirement review</h2>
    <p className="muted">Historical requirement matches and evidence-use signals are shown as saved. Evidence-use does not change the persisted match judgement.</p>
    {invalidIndexCount > 0 && <p role="note">Some persisted requirement-match data is unavailable or inconsistent.</p>}
    {rows.length === 0 && <p className="muted">No canonical job requirements were recorded.</p>}
    {rows.map((row) => <RequirementRow key={row.index} row={row} review={review} citations={citations} />)}
  </section>;
}

function RequirementRow({ row, review, citations }: { row: RequirementReviewRow; review: ApplicationPreparationReview; citations: CitationUsage }) {
  return <article className="application-subsection" aria-label={`Requirement ${row.index + 1}`}>
    <h3>Requirement {row.index + 1}</h3>
    <p><strong>{row.requirement.text}</strong></p>
    <p>{row.requirement.importance} · {row.requirement.category}</p>
    {row.inconsistent ? <p role="note">Persisted match data is unavailable or inconsistent for this requirement.</p> : row.match ? <>
      <p>Persisted match: {row.match.match_type} · Score: {row.match.score}</p>
      <p>{row.match.reasoning}</p>
      <p><strong>Evidence-use signal:</strong> {row.aggregate}</p>
      {!row.evidence.length && <p>No matched evidence recorded.</p>}
      {row.evidence.map((item) => <div className="application-subsection" key={evidenceIdentity(item.ref)}>
        <p>{sourceName(item.ref.source_type)} <code className="muted">{item.ref.source_ref}</code></p>
        <p>{stateLabel(item.state)}</p>
        {item.historicalTextSource === "snapshot" && <p><strong>Preparation-time evidence:</strong> {item.historicalText}</p>}
        {item.historicalTextSource === "match_value" && <p><strong>Historical value saved with this requirement match:</strong> {item.historicalText}</p>}
        {item.historicalTextSource === "unavailable" && <p className="muted">Human-readable historical evidence was not stored for this reference.</p>}
        {review.evidence_snapshot_status === "legacy_unavailable" && item.cited && <p>This source is cited in the final prepared materials.</p>}
        {citations.has(evidenceIdentity(item.ref)) && <p className="muted">Final usage: {citations.get(evidenceIdentity(item.ref))!.join("; ")}</p>}
      </div>)}
    </> : <p>No persisted match recorded.</p>}
  </article>;
}

export function ApplicationsPage() {
  const { api, user } = useAuth();
  const [state, setState] = useState<LoadState<ApplicationPreparation[]> & { ownerId?: string }>({ phase: "loading" });
  const generation = useRef(0);
  const alive = useRef(false);
  const ownerRef = useRef(user?.id);
  ownerRef.current = user?.id;
  const load = async (): Promise<boolean> => {
    const current = ++generation.current;
    const ownerId = user?.id;
    setState((old) => ({ phase: old.ownerId === ownerId && old.value ? "ready" : "loading", value: old.ownerId === ownerId ? old.value : undefined, ownerId }));
    try {
      const value = await api.request<ApplicationPreparation[]>("/api/v1/applications");
      if (alive.current && current === generation.current && ownerRef.current === ownerId) { setState({ phase: "ready", value, ownerId }); return true; }
      return false;
    } catch {
      if (alive.current && current === generation.current && ownerRef.current === ownerId) setState((old) => ({ phase: "error", value: old.ownerId === ownerId ? old.value : undefined, ownerId, message: "Application history is unavailable." }));
      return false;
    }
  };
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; generation.current += 1; }; }, [api, user?.id]);
  const visibleState = state.ownerId === user?.id ? state : { phase: "loading" as const };
  return <main className="applications-page">
    <header className="workspace-header"><div><p className="eyebrow">Career workspace</p><h1>Applications</h1><p className="muted">Review saved application-preparation snapshots and download their documents.</p></div><Link to="/jobs">Back to Jobs</Link></header>
    <ExternalPreparationForm onHistoryRefresh={load} />
    <section aria-label="Application preparation history">
      <div className="section-heading"><h2>Preparation history</h2><button type="button" className="button-secondary" onClick={() => void load()}>Refresh history</button></div>
      {visibleState.phase === "loading" && !visibleState.value && <p role="status">Loading application history…</p>}
      {visibleState.phase === "error" && <p role="alert">{visibleState.message}</p>}
      {visibleState.value?.length === 0 && <p className="muted">No application preparations yet. Prepare a ranked opportunity from <Link to="/jobs">Jobs</Link>, or prepare another vacancy directly here.</p>}
      {!!visibleState.value?.length && <div className="application-list">{visibleState.value.map((value) => <PreparationSummary key={value.id} value={value} />)}</div>}
    </section>
  </main>;
}

function safeFilename(value: string | null, fallback: string): string {
  const leaf = (value ?? "").split(/[\\/]/).at(-1)?.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return leaf && leaf !== "." && leaf !== ".." ? leaf : fallback;
}

export function ApplicationDetailPage() {
  const { preparationId = "" } = useParams();
  const { api, user } = useAuth();
  const [state, setState] = useState<LoadState<ApplicationPreparation> & { ownerId?: string }>({ phase: "loading" });
  const [reviewState, setReviewState] = useState<LoadState<ApplicationPreparationReview> & { ownerId?: string }>({ phase: "loading" });
  const [downloadError, setDownloadError] = useState("");
  const [downloading, setDownloading] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    const ownerId = user?.id;
    setState({ phase: "loading", ownerId }); setReviewState({ phase: "loading", ownerId }); setDownloadError("");
    if (!ownerId) return () => { generation.current += 1; };
    void api.request<ApplicationPreparation>(`/api/v1/applications/${encodeURIComponent(preparationId)}`).then((value) => {
      if (current !== generation.current) return;
      if (value.id !== preparationId) { setState({ phase: "error", ownerId, message: "Application preparation detail is unavailable." }); return; }
      setState({ phase: "ready", value, ownerId });
    }).catch((error: unknown) => {
      if (current === generation.current && (error as Error)?.name !== "AbortError") setState({ phase: "error", ownerId, message: error instanceof ApiError && error.status === 404 ? "This preparation is not available to this account." : "Application preparation detail is unavailable." });
    });
    void api.request<ApplicationPreparationReview>(`/api/v1/applications/${encodeURIComponent(preparationId)}/review`).then((value) => {
      if (current !== generation.current) return;
      if (value.preparation_id !== preparationId) { setReviewState({ phase: "error", ownerId, message: "Historical evidence review did not match this preparation." }); return; }
      setReviewState({ phase: "ready", value, ownerId });
    }).catch((error: unknown) => {
      if (current === generation.current && (error as Error)?.name !== "AbortError") setReviewState({ phase: "error", ownerId, message: "Historical evidence review is unavailable." });
    });
    return () => { generation.current += 1; };
  }, [api, preparationId, user?.id]);
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
  const value = state.ownerId === user?.id && state.value?.id === preparationId ? state.value : undefined;
  const review = reviewState.ownerId === user?.id && reviewState.value?.preparation_id === preparationId && reviewState.value.preparation_id === value?.id ? reviewState.value : undefined;
  const reviewPending = reviewState.ownerId === user?.id && reviewState.phase === "loading";
  const citations = value ? collectFinalCitationUsage(value) : new Map<string, string[]>();
  return <main className="applications-page application-detail-page">
    <header className="workspace-header"><div><p className="eyebrow">Historical application preparation</p><h1>{value?.target.title ?? "Application preparation"}</h1><p className="muted"><Link to="/applications">Back to Applications</Link></p></div></header>
    {state.phase === "loading" && <p role="status">Loading saved preparation…</p>}
    {state.phase === "error" && <p role="alert">{state.message}</p>}
    {reviewState.phase === "loading" && <p role="status">Loading historical evidence review…</p>}
    {reviewState.phase === "error" && value && <p role="alert">{reviewState.message}</p>}
    {value && <>
      <p className="notice">This is the saved snapshot created {dateLabel(value.created_at)}. It is not replaced by your current profile, CV, or vacancy information.</p>
      <RuntimeAttributionPanel attribution={value.runtime_attribution} boundary="preparation" />
      <PreparationTrackingPanel preparationId={value.id} />
      {review && <ReviewSummary value={value} review={review} rows={buildRequirementReview(value, review, citations).rows} />}
      {review && <RequirementReview value={value} review={review} />}
      <section className="card application-section"><h2>Target used</h2><dl className="detail-grid">{([["Title", value.target.title], ["Company", value.target.company], ["Location", value.target.location], ["Work arrangement", value.target.work_arrangement], ["Employment type", value.target.employment_type], ["Source", targetName(value.target.source_kind)]] as Array<[string, string | null | undefined]>).map(([label, item]) => item ? <div key={label}><dt>{label}</dt><dd>{item}</dd></div> : null)}</dl>{value.target.public_url && <p><a href={value.target.public_url} target="_blank" rel="noopener noreferrer">Open saved public vacancy</a></p>}</section>
      <section className="card application-section"><h2>Identity used</h2><p>This contact information was saved with the historical preparation.</p><dl className="detail-grid">{([["Name", value.identity.display_name], ["Email", value.identity.email], ["Phone", value.identity.phone], ["Location", value.identity.location], ["LinkedIn", value.identity.linkedin_url], ["GitHub", value.identity.github_url], ["Portfolio", value.identity.portfolio_url]] as Array<[string, string | null | undefined]>).map(([label, item]) => item ? <div key={label}><dt>{label}</dt><dd>{label.endsWith("In") || label === "GitHub" || label === "Portfolio" ? <a href={item} target="_blank" rel="noopener noreferrer">{item}</a> : item}</dd></div> : null)}</dl></section>
      <section className="card application-section"><h2>Tailored CV</h2><p className="notice">{value.result.layout_status === "fit" ? `Fits the ${value.result.target_pages}-page target.` : `Over the ${value.result.target_pages}-page target; the document is ${value.result.actual_pdf_pages} pages.`} Actual PDF pages: {value.result.actual_pdf_pages}.</p><h3>Professional summary</h3><p>{value.result.cv.professional_summary}</p><EvidenceUsed refs={value.result.cv.summary_source_refs} review={review} citations={citations} reviewPending={reviewPending} />
        <TextValues title="Key skills" values={value.result.cv.key_skills} />
        <h3>Employment</h3>{value.result.cv.roles.map((role, index) => <article className="application-subsection" key={`${role.employer}-${role.title}-${index}`}><h4>{role.title} · {role.employer}</h4><p>{[role.start_date, role.end_date, role.location].filter(Boolean).join(" · ")}</p>{role.bullets.map((bullet, bulletIndex) => <div key={`${bulletIndex}-${bullet.text}`}><p>{bullet.text}</p><EvidenceUsed refs={bullet.source_refs} review={review} citations={citations} reviewPending={reviewPending} /></div>)}</article>)}
        <h3>Selected projects</h3>{value.result.cv.selected_projects.length ? value.result.cv.selected_projects.map((project, index) => <article className="application-subsection" key={`${project.name}-${index}`}><h4>{project.name}</h4><p>{project.text}</p><EvidenceUsed refs={project.source_refs} review={review} citations={citations} reviewPending={reviewPending} /></article>) : <p className="muted">None recorded.</p>}
        <TextValues title="Education" values={value.result.cv.education} /><TextValues title="Credentials" values={value.result.cv.credentials} />
      </section>
      <section className="card application-section"><h2>Application questions</h2>{value.result.answers.length ? value.result.answers.map((answer, index) => <article className="application-subsection" key={`${index}-${answer.question}`}><h3>{answer.question}</h3>{answer.status === "drafted" ? <><p>{answer.answer}</p><EvidenceUsed refs={answer.source_refs} review={review} citations={citations} reviewPending={reviewPending} /></> : <p className="muted">Unsupported by the available evidence — no answer was drafted.</p>}</article>) : <p className="muted">No application questions were supplied.</p>}</section>
      <section className="card application-section"><h2>Cover letter</h2>{value.result.cover_letter ? <><p className="application-prose">{value.result.cover_letter.body}</p><EvidenceUsed refs={value.result.cover_letter.source_refs} review={review} citations={citations} reviewPending={reviewPending} /></> : <p className="muted">No cover letter exists for this preparation.</p>}</section>
      <section className="card application-section"><h2>Downloads</h2><DownloadButtons downloading={downloading} onDownload={download} cover={Boolean(value.result.cover_letter)} />{downloadError && <p role="alert">{downloadError}</p>}</section>
    </>}
  </main>;
}

function TextValues({ title, values }: { title: string; values: string[] }) { return <section><h3>{title}</h3>{values.length ? <ul>{values.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : <p className="muted">None recorded.</p>}</section>; }
function DownloadButtons({ downloading, onDownload, cover }: { downloading: string | null; onDownload: (kind: "cv.docx" | "cv.pdf" | "cover-letter.docx" | "cover-letter.pdf") => void; cover: boolean }) {
  return <div className="card-actions"><button type="button" className="button-secondary" disabled={Boolean(downloading)} onClick={() => onDownload("cv.docx")}>{downloading === "cv.docx" ? "Downloading…" : "Download CV DOCX"}</button><button type="button" className="button-secondary" disabled={Boolean(downloading)} onClick={() => onDownload("cv.pdf")}>{downloading === "cv.pdf" ? "Downloading…" : "Download CV PDF"}</button>{cover && <><button type="button" className="button-secondary" disabled={Boolean(downloading)} onClick={() => onDownload("cover-letter.docx")}>{downloading === "cover-letter.docx" ? "Downloading…" : "Download cover letter DOCX"}</button><button type="button" className="button-secondary" disabled={Boolean(downloading)} onClick={() => onDownload("cover-letter.pdf")}>{downloading === "cover-letter.pdf" ? "Downloading…" : "Download cover letter PDF"}</button></>}</div>;
}

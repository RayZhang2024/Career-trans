import { useEffect, useRef, useState } from "react";
import { ApiError, useAuth } from "./auth";
import type {
  CanonicalCandidateReadSnapshot,
  EditableProfile,
  EditableStructuredProfile,
  Employment,
  Education,
  Credential,
  CandidateSkill,
  CandidateProject,
  CandidateAchievement,
  ProfileRevision,
  ProfileRevisionPatch,
} from "./api";
import { relationshipLabel } from "./StructuredItemPresentation";

type Props = {
  snapshot: CanonicalCandidateReadSnapshot | undefined;
  onConfirmed: () => Promise<boolean>;
};

const profileFields = [
  ["display_name", "Display name"], ["headline", "Headline"], ["current_role", "Current role"],
  ["location", "Location"], ["summary", "Summary"], ["career_goal", "Career goal"],
  ["job_search_criteria", "Job-search criteria"], ["preferred_email", "Preferred email"],
  ["phone", "Phone"], ["linkedin_url", "LinkedIn URL"], ["github_url", "GitHub URL"],
  ["portfolio_url", "Portfolio URL"],
] as const satisfies ReadonlyArray<readonly [keyof EditableProfile, string]>;
const multilineProfileFields = new Set<keyof EditableProfile>(["summary", "career_goal", "job_search_criteria"]);

type StructuredKey = keyof EditableStructuredProfile;
type StructuredField = { key: string; label: string; multiline?: boolean; commaList?: boolean; select?: boolean };
const credentialTypes = [
  ["certification", "Certification"], ["professional_qualification", "Professional qualification"],
  ["professional_registration", "Professional registration"], ["formal_training", "Formal training"],
  ["professional_membership", "Professional membership"], ["other", "Other"],
] as const;
const structuredSections: ReadonlyArray<{ key: StructuredKey; label: string; singular: string; fields: StructuredField[] }> = [
  { key: "employment", label: "Employment", singular: "Employment record", fields: [
    { key: "employer", label: "Employer" }, { key: "title", label: "Title" }, { key: "start_date", label: "Start date" },
    { key: "end_date", label: "End date" }, { key: "location", label: "Location" }, { key: "description", label: "Description", multiline: true },
  ] },
  { key: "education", label: "Education", singular: "Education record", fields: [
    { key: "institution", label: "Institution" }, { key: "qualification", label: "Qualification" },
    { key: "field_of_study", label: "Field of study" }, { key: "description", label: "Description", multiline: true },
  ] },
  { key: "credentials", label: "Credentials", singular: "Credential", fields: [
    { key: "name", label: "Name" }, { key: "credential_type", label: "Credential type", select: true },
    { key: "issuer", label: "Issuer" }, { key: "issued_date", label: "Issued date" },
    { key: "expiry_date", label: "Expiry date" }, { key: "status", label: "Status" },
    { key: "description", label: "Description", multiline: true },
  ] },
  { key: "skills", label: "Skills", singular: "Skill", fields: [
    { key: "name", label: "Name" }, { key: "category", label: "Category" },
  ] },
  { key: "projects", label: "Projects", singular: "Project", fields: [
    { key: "name", label: "Name" }, { key: "description", label: "Description", multiline: true },
    { key: "skills", label: "Skills (comma separated)", commaList: true },
  ] },
  { key: "achievements", label: "Achievements", singular: "Achievement", fields: [
    { key: "text", label: "Achievement", multiline: true },
  ] },
];

type FormValues = Record<keyof EditableProfile, string>;
const profileFormValues = (value: EditableProfile | null): FormValues => Object.fromEntries(
  profileFields.map(([key]) => [key, value?.[key] ?? ""]),
) as FormValues;
const profilePayload = (values: FormValues): EditableProfile => Object.fromEntries(
  profileFields.map(([key]) => [key, values[key] === "" ? null : values[key]]),
) as EditableProfile;
const emptyStructured = (): EditableStructuredProfile => ({ employment: [], education: [], credentials: [], skills: [], projects: [], achievements: [] });
const cloneStructured = (value: EditableStructuredProfile | null): EditableStructuredProfile => value ? structuredClone(value) : emptyStructured();

function emptyRecord(section: StructuredKey): Record<string, unknown> {
  switch (section) {
    case "employment": return { employer: "", title: "", start_date: null, end_date: null, location: null, description: "" } satisfies Employment;
    case "education": return { institution: "", qualification: "", field_of_study: null, description: "" } satisfies Education;
    case "credentials": return { name: "", credential_type: "certification", issuer: null, issued_date: null, expiry_date: null, status: null, description: "" } satisfies Credential;
    case "skills": return { name: "", category: null } satisfies CandidateSkill;
    case "projects": return { name: "", description: "", skills: [] } satisfies CandidateProject;
    case "achievements": return { text: "" } satisfies CandidateAchievement;
  }
}

function display(value: string | null | undefined): string {
  return value === null || value === undefined || value === "" ? "Not set" : value;
}

function structuredSummary(section: StructuredKey, raw: unknown): string[] {
  const rows = raw as Array<Record<string, unknown>>;
  return rows.map((item) => {
    switch (section) {
      case "employment": return [item.title, item.employer].filter(Boolean).join(" at ") + [item.start_date, item.end_date, item.location, item.description].filter(Boolean).map(String).map((part) => ` · ${part}`).join("");
      case "education": return [item.qualification, item.institution].filter(Boolean).join(" — ") + [item.field_of_study, item.description].filter(Boolean).map(String).map((part) => ` · ${part}`).join("");
      case "credentials": return [item.name, String(item.credential_type ?? "").replaceAll("_", " "), item.issuer, item.status, item.issued_date, item.expiry_date, item.description].filter(Boolean).join(" · ");
      case "skills": return [item.name, item.category].filter(Boolean).join(" · ");
      case "projects": return [item.name, item.description, ...(Array.isArray(item.skills) ? item.skills as string[] : [])].filter(Boolean).join(" · ");
      case "achievements": return String(item.text ?? "");
    }
  });
}

function CurrentProposedReview({ revision, snapshot }: { revision: ProfileRevision; snapshot: CanonicalCandidateReadSnapshot | undefined }) {
  if (!snapshot) return <p className="muted" role="status">Load the current Profile to compare saved and proposed information.</p>;
  if (revision.changed_authorities.length === 0) return <p className="notice">No changes from your current information.</p>;
  const currentProfile = snapshot.profile;
  const proposedProfile = revision.proposed_profile;
  const changedProfileFields = revision.changed_authorities.includes("profile")
    ? profileFields.filter(([key]) => (currentProfile?.[key] ?? null) !== (proposedProfile?.[key] ?? null))
    : [];
  const currentStructured = snapshot.structured_profile;
  const proposedStructured = revision.proposed_structured;
  const changedSections = revision.changed_authorities.includes("structured")
    ? structuredSections.filter(({ key }) => JSON.stringify(currentStructured?.[key] ?? []) !== JSON.stringify(proposedStructured?.[key] ?? []))
    : [];
  const comparisons = [...(revision.structured_comparisons ?? [])];
  const proposedRows = (section: StructuredKey) => (proposedStructured?.[section] ?? []).map((item, index) => {
    const foundIndex = comparisons.findIndex((entry) => entry.comparison.section === section && JSON.stringify(entry.comparison.incoming_item) === JSON.stringify(item));
    const found = foundIndex >= 0 ? comparisons.splice(foundIndex, 1)[0] : undefined;
    return <li key={`${index}-${JSON.stringify(item)}`}>{structuredSummary(section, [item])[0] || "Not set"}{found && <span className="relationship-badge">{relationshipLabel(found.comparison.relationship)}</span>}{found && ["refinement", "conflict", "ambiguous"].includes(found.comparison.relationship) && <p className="muted">You edited this information directly. Confirming the Profile revision will make the proposed version current.</p>}{found?.comparison.relationship === "reinforcement" && <p className="muted">Same fact — representation updated.</p>}</li>;
  });
  const proposedBySection = new Map(changedSections.map(({ key }) => [key, proposedRows(key)]));
  return <div className="revision-review" aria-label="Current and proposed changes">
    {changedProfileFields.length > 0 && <section className="revision-review-section"><h3>Profile details</h3>{changedProfileFields.map(([key, label]) => <div className="revision-field-diff" key={key}><h4>{label}</h4><div className="revision-sides"><p><strong>Current</strong><span>{display(currentProfile?.[key])}</span></p><p><strong>Proposed</strong><span>{display(proposedProfile?.[key])}</span></p></div></div>)}</section>}
    {changedSections.map(({ key, label }) => <section className="revision-review-section" key={key}><h3>{label}</h3><div className="revision-sides"><section><h4>Current</h4><ul>{structuredSummary(key, currentStructured?.[key] ?? []).length ? structuredSummary(key, currentStructured?.[key] ?? []).map((item, index) => <li key={`${index}-${item}`}>{item || "Not set"}</li>) : <li className="muted">None</li>}</ul></section><section><h4>Proposed</h4><ul>{proposedBySection.get(key)?.length ? proposedBySection.get(key) : <li className="muted">None</li>}</ul></section></div></section>)}
  </div>;
}

export function ProfileRevisionWorkflow({ snapshot, onConfirmed }: Props) {
  const { api } = useAuth();
  const [revision, setRevision] = useState<ProfileRevision | null | undefined>(undefined);
  const [revisionLoading, setRevisionLoading] = useState(true);
  const [revisionError, setRevisionError] = useState("");
  const [mode, setMode] = useState<"closed" | "edit" | "review">("closed");
  const [values, setValues] = useState<FormValues>(() => profileFormValues(null));
  const [structured, setStructured] = useState<EditableStructuredProfile>(emptyStructured);
  const [profileActive, setProfileActive] = useState(false);
  const [structuredActive, setStructuredActive] = useState(false);
  const [profileDirty, setProfileDirty] = useState(false);
  const [structuredDirty, setStructuredDirty] = useState(false);
  const [pending, setPending] = useState<"create" | "save" | "review" | "confirm" | "discard" | "">("");
  const [actionError, setActionError] = useState("");
  const [saveConflict, setSaveConflict] = useState(false);
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const actionGeneration = useRef(0);
  const pendingRef = useRef(false);
  const hasUnsavedChanges = profileDirty || structuredDirty;
  const stale = Boolean(revision?.stale_authorities.length);

  const hydrate = (found: ProfileRevision) => {
    setValues(profileFormValues(found.proposed_profile));
    setStructured(cloneStructured(found.proposed_structured));
    setProfileActive(found.proposed_profile !== null);
    setStructuredActive(found.proposed_structured !== null);
    setProfileDirty(false);
    setStructuredDirty(false);
  };
  const loadActiveRevision = async (): Promise<ProfileRevision | null | undefined> => {
    const request = ++generation.current;
    setRevisionLoading(true);
    setRevisionError("");
    try {
      const found = await api.request<ProfileRevision | null>("/api/v1/profile/revisions/active");
      if (request === generation.current) {
        setRevision(found);
        if (found) hydrate(found);
        setRevisionLoading(false);
        return found;
      }
    } catch {
      if (request === generation.current) {
        setRevisionError("Saved profile changes could not be loaded.");
        setRevisionLoading(false);
      }
    }
    return undefined;
  };

  useEffect(() => {
    void loadActiveRevision();
    return () => { generation.current += 1; actionGeneration.current += 1; };
  }, [api.request]);

  const startEditing = async () => {
    if (pendingRef.current) return;
    if (revision) {
      if (stale) { setMode("review"); return; }
      setMode(revision.state === "review_ready" ? "review" : "edit");
      return;
    }
    if (revision !== null || !snapshot) return;
    pendingRef.current = true;
    const action = ++actionGeneration.current;
    const request = ++generation.current;
    setPending("create"); setActionError(""); setNotice(""); setRevisionError("");
    try {
      const created = await api.request<ProfileRevision>("/api/v1/profile/revisions", { method: "POST" });
      if (request === generation.current && action === actionGeneration.current) {
        setRevision(created); hydrate(created); setRevisionLoading(false); setMode("edit");
      }
    } catch {
      if (request === generation.current && action === actionGeneration.current) setActionError("A profile draft could not be started. Try again.");
    } finally {
      pendingRef.current = false;
      if (action === actionGeneration.current) setPending("");
    }
  };

  const saveDraft = async () => {
    if (!revision || !hasUnsavedChanges || stale || pendingRef.current) return;
    const payload: ProfileRevisionPatch = { expected_revision: revision.revision };
    if (profileDirty) payload.proposed_profile = profilePayload(values);
    if (structuredDirty) payload.proposed_structured = structured;
    pendingRef.current = true;
    const action = ++actionGeneration.current;
    const request = ++generation.current;
    setPending("save"); setActionError(""); setSaveConflict(false); setNotice("");
    try {
      const saved = await api.request<ProfileRevision>(`/api/v1/profile/revisions/${revision.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      if (request === generation.current && action === actionGeneration.current) {
        setRevision(saved); hydrate(saved); setMode("edit");
        setNotice("Draft saved. Your current profile remains in use until you confirm these changes.");
      }
    } catch (error) {
      if (request === generation.current && action === actionGeneration.current) {
        if (error instanceof ApiError && error.status === 409) {
          setSaveConflict(true);
          setActionError("This draft was updated elsewhere. Your unsaved changes are still shown here. Reload the latest saved draft before trying again.");
        } else setActionError("Profile draft could not be saved. Your edits are still shown here.");
      }
    } finally {
      pendingRef.current = false;
      if (action === actionGeneration.current) setPending("");
    }
  };

  const reviewChanges = async () => {
    if (!revision || revision.state !== "draft" || hasUnsavedChanges || stale || pendingRef.current) return;
    pendingRef.current = true;
    const action = ++actionGeneration.current;
    const request = ++generation.current;
    setPending("review"); setActionError(""); setNotice("");
    try {
      const reviewed = await api.request<ProfileRevision>(`/api/v1/profile/revisions/${revision.id}/review`, { method: "POST", body: JSON.stringify({ expected_revision: revision.revision }) });
      if (request === generation.current && action === actionGeneration.current) { setRevision(reviewed); hydrate(reviewed); setMode("review"); }
    } catch (error) {
      if (request === generation.current && action === actionGeneration.current) {
        setActionError(error instanceof ApiError && error.status === 409 && error.detail?.toLowerCase().includes("same-fact duplicate")
          ? "This draft adds a duplicate version of information already in the Profile. Edit the draft and remove the duplicate before review."
          : error instanceof ApiError && error.status === 409 ? "The saved draft changed or is stale. Reload its latest status before continuing." : "Changes could not be reviewed. Try again.");
        void loadActiveRevision();
      }
    } finally {
      pendingRef.current = false;
      if (action === actionGeneration.current) setPending("");
    }
  };

  const confirmChanges = async () => {
    if (!revision || revision.state !== "review_ready" || stale || hasUnsavedChanges || !snapshot || pendingRef.current) return;
    pendingRef.current = true;
    const action = ++actionGeneration.current;
    const request = ++generation.current;
    setPending("confirm"); setActionError(""); setNotice("");
    try {
      await api.request<ProfileRevision>(`/api/v1/profile/revisions/${revision.id}/confirm`, { method: "POST", body: JSON.stringify({ expected_revision: revision.revision }) });
      if (request === generation.current && action === actionGeneration.current) {
        setRevision(undefined); setRevisionLoading(true); setMode("closed"); setProfileDirty(false); setStructuredDirty(false);
        const [currentLoaded, activeRevision] = await Promise.all([onConfirmed(), loadActiveRevision()]);
        if (action === actionGeneration.current) {
          if (currentLoaded && activeRevision === null) setNotice("Profile changes confirmed. The information below is now the current information Career-trans uses.");
          else setActionError("Profile changes were confirmed, but the latest Profile view could not be fully refreshed. Please retry the relevant section.");
        }
      }
    } catch (error) {
      if (request === generation.current && action === actionGeneration.current) {
        setActionError(error instanceof ApiError && error.status === 409 ? "Confirmation conflicted with a newer change. Review the refreshed draft status before continuing." : "Profile changes could not be confirmed. Your current information remains visible.");
        void loadActiveRevision();
      }
    } finally {
      pendingRef.current = false;
      if (action === actionGeneration.current) setPending("");
    }
  };

  const discardChanges = async () => {
    if (!revision || pendingRef.current) return;
    pendingRef.current = true;
    const action = ++actionGeneration.current;
    const request = ++generation.current;
    setPending("discard"); setActionError(""); setNotice("");
    try {
      await api.request<ProfileRevision>(`/api/v1/profile/revisions/${revision.id}/discard`, { method: "POST", body: JSON.stringify({ expected_revision: revision.revision }) });
      if (request === generation.current && action === actionGeneration.current) {
        setRevision(undefined); setRevisionLoading(true); setMode("closed"); setProfileDirty(false); setStructuredDirty(false);
        const activeRevision = await loadActiveRevision();
        if (action === actionGeneration.current && activeRevision === null) setNotice("Draft discarded. Your current profile was not changed.");
      }
    } catch (error) {
      if (request === generation.current && action === actionGeneration.current) {
        setActionError(error instanceof ApiError && error.status === 409 ? "The draft changed before it could be discarded. Reload its latest status and try again." : "The draft could not be discarded. Your current profile remains unchanged.");
        void loadActiveRevision();
      }
    } finally {
      pendingRef.current = false;
      if (action === actionGeneration.current) setPending("");
    }
  };

  const reloadConflict = async () => {
    setSaveConflict(false); setActionError("");
    const found = await loadActiveRevision();
    if (found === undefined) return;
    if (found === null) setMode("closed");
  };

  const changeProfile = (key: keyof EditableProfile, value: string) => {
    setValues((current) => ({ ...current, [key]: value })); setProfileActive(true); setProfileDirty(true); setNotice("");
  };
  const updateStructuredRows = (section: StructuredKey, rows: Array<Record<string, unknown>>) => {
    setStructured((current) => ({ ...current, [section]: rows }) as EditableStructuredProfile);
    setStructuredActive(true); setStructuredDirty(true); setNotice("");
  };

  const affectedLabels = revision?.stale_authorities.map((authority) => authority === "profile" ? "Profile details" : "structured career information").join(" and ");
  const isPending = Boolean(pending);
  return <section className="profile-revision-workflow" aria-label="Profile changes">
    {revisionLoading && revision === undefined && <p className="muted" role="status">Checking for saved profile changes…</p>}
    {revisionError && <div className="revision-error" role="alert"><p>{revisionError}</p><button type="button" className="button-secondary" onClick={() => void loadActiveRevision()}>Retry saved changes</button></div>}
    {revision === null && !revisionLoading && !revisionError && mode === "closed" && <button type="button" onClick={() => void startEditing()} disabled={!snapshot || isPending}>{pending === "create" ? "Starting draft…" : "Edit profile"}</button>}
    {revision && mode === "closed" && <div className={stale ? "profile-warning" : "notice profile-notice"} role={stale ? "alert" : "status"}>
      {stale ? <><h2>Profile draft is out of date</h2><p>Your current information changed after this draft was started. This draft can no longer be confirmed. Discard it and start again from the latest information.</p><p>Affected: {affectedLabels}.</p><div className="revision-actions"><button type="button" className="button-secondary" onClick={() => setMode("review")}>Inspect proposal</button><button type="button" className="button-danger" disabled={isPending} onClick={() => void discardChanges()}>{pending === "discard" ? "Discarding…" : "Discard draft"}</button></div></> : <><p>You have pending profile changes. Your current information remains in use until you confirm them.</p><div className="revision-actions"><button type="button" className="button-secondary" onClick={() => void startEditing()}>{revision.state === "draft" ? "Resume editing" : "Review changes"}</button><button type="button" className="button-danger" disabled={isPending} onClick={() => void discardChanges()}>{pending === "discard" ? "Discarding…" : "Discard draft"}</button></div></>}
    </div>}
    {revision && mode !== "closed" && <section className="card revision-panel">
      <div className="section-heading"><div><h2>{mode === "edit" ? "Edit proposed profile changes" : "Review profile changes"}</h2><p className="muted">Current confirmed information stays in use until you confirm this revision.</p></div><span className="revision-state">{stale ? "Stale" : revision.state === "review_ready" && mode === "review" ? "Ready to confirm" : "Draft"}</span></div>
      {stale && <div className="profile-warning" role="alert"><h3>Current information changed</h3><p>Your proposal is stale in: {affectedLabels}. Discard it and start again from the latest information.</p></div>}
      {revisionLoading && <p role="status">Refreshing saved draft…</p>}
      {actionError && <p role="alert">{actionError}</p>}
      {saveConflict && <button type="button" className="button-secondary" disabled={isPending} onClick={() => void reloadConflict()}>Reload saved draft</button>}
      {notice && <p role="status">{notice}</p>}
      {mode === "edit" && <>
        <section className="revision-editor-section"><h3>Profile details</h3><p className="muted">All fields are optional. This section becomes part of the proposal only after you edit a field.</p><div className="profile-fields">{profileFields.map(([key, label]) => <div className={multilineProfileFields.has(key) ? "field field-wide" : "field"} key={key}><label htmlFor={`revision-profile-${key}`}>{label}</label>{multilineProfileFields.has(key) ? <textarea id={`revision-profile-${key}`} value={values[key]} disabled={isPending || stale} onChange={(event) => changeProfile(key, event.target.value)} /> : <input id={`revision-profile-${key}`} type={key === "preferred_email" ? "email" : "text"} value={values[key]} disabled={isPending || stale} onChange={(event) => changeProfile(key, event.target.value)} />}</div>)}</div>{!profileActive && <p className="muted">No Profile details will be included unless you edit one of these fields.</p>}</section>
        <section className="revision-editor-section"><h3>Career information</h3><p className="muted">Edit career facts here. Career Evidence and source details are read-only and are not part of this editor.</p>{structuredSections.map(({ key, label, singular, fields }) => {
          const rows = structured[key] as unknown as Array<Record<string, unknown>>;
          return <section className="revision-structured-section" key={key} aria-label={label}><div className="section-heading"><h4>{label}</h4><button type="button" className="button-secondary" disabled={isPending || stale} onClick={() => updateStructuredRows(key, [...rows, emptyRecord(key)])}>Add {singular}</button></div>{rows.map((item, index) => <fieldset className="structured-item" key={`${key}-${index}`}><legend>{singular} {index + 1}</legend><div className="profile-fields">{fields.map((field) => <div className={field.multiline || field.commaList ? "field field-wide" : "field"} key={field.key}><label htmlFor={`revision-${key}-${index}-${field.key}`}>{field.label}{field.select ? <select id={`revision-${key}-${index}-${field.key}`} value={String(item[field.key] ?? "certification")} disabled={isPending || stale} onChange={(event) => updateStructuredRows(key, rows.map((row, rowIndex) => rowIndex === index ? { ...row, [field.key]: event.target.value } : row))}>{credentialTypes.map(([value, option]) => <option key={value} value={value}>{option}</option>)}</select> : field.multiline ? <textarea id={`revision-${key}-${index}-${field.key}`} value={String(item[field.key] ?? "")} disabled={isPending || stale} onChange={(event) => updateStructuredRows(key, rows.map((row, rowIndex) => rowIndex === index ? { ...row, [field.key]: event.target.value } : row))} /> : <input id={`revision-${key}-${index}-${field.key}`} value={Array.isArray(item[field.key]) ? (item[field.key] as string[]).join(", ") : String(item[field.key] ?? "")} disabled={isPending || stale} onChange={(event) => {
            const value = field.commaList ? event.target.value.split(",").map((part) => part.trim()).filter(Boolean) : event.target.value === "" ? null : event.target.value;
            updateStructuredRows(key, rows.map((row, rowIndex) => rowIndex === index ? { ...row, [field.key]: value } : row));
          }} />}</label></div>)}</div><button type="button" className="button-danger" disabled={isPending || stale} onClick={() => updateStructuredRows(key, rows.filter((_, rowIndex) => rowIndex !== index))}>Remove {singular.toLowerCase()}</button></fieldset>)}</section>;
        })}{!structuredActive && <p className="muted">No career information will be included unless you add or edit a record.</p>}</section>
        {hasUnsavedChanges && <p className="notice" role="status">You have unsaved changes. Save them before review.</p>}
      </>}
      {mode === "review" && <CurrentProposedReview revision={revision} snapshot={snapshot} />}
      <div className="revision-actions">
        {mode === "edit" ? <>
          <button type="button" disabled={!hasUnsavedChanges || isPending || stale} onClick={() => void saveDraft()}>{pending === "save" ? "Saving draft…" : "Save draft"}</button>
          {revision.state === "draft" ? <button type="button" className="button-secondary" disabled={hasUnsavedChanges || isPending || stale} onClick={() => void reviewChanges()}>{pending === "review" ? "Preparing review…" : "Review changes"}</button> : <button type="button" className="button-secondary" disabled={hasUnsavedChanges || isPending} onClick={() => setMode("review")}>Back to review</button>}
        </> : <>
          <button type="button" className="button-secondary" disabled={isPending || stale || revision.state !== "review_ready"} onClick={() => setMode("edit")}>Edit changes</button>
          {revision.state === "draft" && <button type="button" className="button-secondary" disabled={isPending || stale} onClick={() => void reviewChanges()}>Review changes</button>}
          <button type="button" disabled={isPending || stale || hasUnsavedChanges || revision.state !== "review_ready" || !snapshot} onClick={() => void confirmChanges()}>{pending === "confirm" ? "Confirming…" : "Confirm changes"}</button>
        </>}
        <button type="button" className="button-danger" disabled={isPending} onClick={() => void discardChanges()}>{pending === "discard" ? "Discarding…" : "Discard draft"}</button>
      </div>
    </section>}
    {actionError && !revision && <p role="alert">{actionError}</p>}
    {notice && !revision && <p className="notice" role="status">{notice}</p>}
  </section>;
}

import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "./auth";
import type {
  AdviserProfileProposal,
  AdviserProfileProposalGenerationRead,
  AdviserProfileProposalTransferRead,
  AdviserProfileProposalUpdate,
  AdviserProfileProposalOverlapResolutionRequest,
  CandidateAchievement,
  CandidateProject,
  CandidateSkill,
  Credential,
  Education,
  Employment,
  ProfileRevision,
  StructuredProfileSection,
} from "./api";
import { relationshipLabel, structuredItemLabel, StructuredItemPresentation } from "./StructuredItemPresentation";
import type { SessionApi } from "./api";

type ConfirmedClarification = {
  clarification_id: string;
  question_text: string;
  interpretation: {
    answer_kind: string;
    confirmed_context_summary: string;
    proposed_evidence: Array<{ title: string; text: string; skills: string[] }>;
  } | null;
} | null;

type Props = {
  api: Pick<SessionApi, "request">;
  confirmedClarification?: ConfirmedClarification;
  enrichment?: { clarification_id: string; question_text: string; confirmed_context_summary: string; has_profile_evidence: boolean } | null;
  activeProfileDraft?: boolean;
  onResolved?: () => void;
};
type HistoryState = "idle" | "loading" | "ready" | "unavailable";
type Mutation = { kind: "generation" | "edit" | "reject" | "transfer" | "reload" | "replace" | "resolve"; id: string } | null;

const sections: Record<StructuredProfileSection, { label: string; noun: string }> = {
  employment: { label: "Employment", noun: "employment record" },
  education: { label: "Education", noun: "education record" },
  credentials: { label: "Credentials", noun: "credential" },
  skills: { label: "Skills", noun: "skill" },
  projects: { label: "Projects", noun: "project" },
  achievements: { label: "Achievements", noun: "achievement" },
};
const credentialTypes = [
  "certification", "professional_qualification", "professional_registration",
  "formal_training", "professional_membership", "other",
] as const;
type ItemField = { key: string; label: string; required?: boolean; multiline?: boolean; nullable?: boolean; commaList?: boolean; select?: boolean };
const itemFields: Record<StructuredProfileSection, ItemField[]> = {
  employment: [
    { key: "employer", label: "Employer", required: true }, { key: "title", label: "Title", required: true },
    { key: "start_date", label: "Start date", nullable: true }, { key: "end_date", label: "End date", nullable: true },
    { key: "location", label: "Location", nullable: true }, { key: "description", label: "Description", multiline: true },
  ],
  education: [
    { key: "institution", label: "Institution", required: true }, { key: "qualification", label: "Qualification", required: true },
    { key: "field_of_study", label: "Field of study", nullable: true }, { key: "description", label: "Description", multiline: true },
  ],
  credentials: [
    { key: "name", label: "Name", required: true }, { key: "credential_type", label: "Credential type", required: true, select: true },
    { key: "issuer", label: "Issuer", nullable: true }, { key: "issued_date", label: "Issued date", nullable: true },
    { key: "expiry_date", label: "Expiry date", nullable: true }, { key: "status", label: "Status", nullable: true },
    { key: "description", label: "Description", multiline: true },
  ],
  skills: [{ key: "name", label: "Name", required: true }, { key: "category", label: "Category", nullable: true }],
  projects: [
    { key: "name", label: "Name", required: true }, { key: "description", label: "Description", multiline: true },
    { key: "skills", label: "Skills (comma separated)", commaList: true },
  ],
  achievements: [{ key: "text", label: "Achievement", required: true, multiline: true }],
};

function cloneUpdate(update: AdviserProfileProposalUpdate): AdviserProfileProposalUpdate {
  return structuredClone(update);
}

function mergeProposals(current: AdviserProfileProposal[], incoming: AdviserProfileProposal[]): AdviserProfileProposal[] {
  const byId = new Map(current.map((proposal) => [proposal.id, proposal]));
  for (const proposal of incoming) {
    const known = byId.get(proposal.id);
    if (!known || proposal.revision > known.revision) byId.set(proposal.id, proposal);
  }
  return [...byId.values()].sort((left, right) => right.created_at.localeCompare(left.created_at));
}

function reconcileHistory(current: AdviserProfileProposal[], incoming: AdviserProfileProposal[]): AdviserProfileProposal[] {
  const known = new Map(current.map((proposal) => [proposal.id, proposal]));
  return mergeProposals([], incoming.map((proposal) => {
    const local = known.get(proposal.id);
    return local && local.revision > proposal.revision ? local : proposal;
  }));
}

function sameUpdate(left: AdviserProfileProposalUpdate, right: AdviserProfileProposalUpdate): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function itemLines(update: AdviserProfileProposalUpdate): string[] {
  const item = update.item;
  switch (update.section) {
    case "employment": {
      const value = item as Employment;
      return [value.title && value.employer ? `${value.title} at ${value.employer}` : value.title || value.employer, value.start_date, value.end_date, value.location, value.description].filter((part): part is string => Boolean(part));
    }
    case "education": {
      const value = item as Education;
      return [[value.qualification, value.institution].filter(Boolean).join(" — "), value.field_of_study, value.description].filter((part): part is string => Boolean(part));
    }
    case "credentials": {
      const value = item as Credential;
      return [[value.name, value.credential_type.replaceAll("_", " "), value.issuer, value.status].filter(Boolean).join(" · "), value.issued_date, value.expiry_date, value.description].filter((part): part is string => Boolean(part));
    }
    case "skills": {
      const value = item as CandidateSkill;
      return [[value.name, value.category].filter(Boolean).join(" · ")];
    }
    case "projects": {
      const value = item as CandidateProject;
      return [value.name, value.description, value.skills.length ? `Skills: ${value.skills.join(", ")}` : ""].filter(Boolean);
    }
    case "achievements": return [(item as CandidateAchievement).text];
  }
}

function ItemSummary({ label, update }: { label: string; update: AdviserProfileProposalUpdate }) {
  return <section className="suggestion-item-summary"><h4>{label}</h4><ul>{itemLines(update).map((line, index) => <li key={`${index}-${line}`}>{line}</li>)}</ul></section>;
}

function ProposalEditor({
  update,
  onChange,
}: {
  update: AdviserProfileProposalUpdate;
  onChange: (next: AdviserProfileProposalUpdate) => void;
}) {
  const fields = itemFields[update.section];
  const item = update.item as unknown as Record<string, string | null | string[]>;
  const setField = (field: ItemField, value: string) => {
    const nextValue: string | null | string[] = field.commaList
      ? value.split(",").map((part) => part.trim()).filter(Boolean)
      : field.nullable ? value === "" ? null : value : value;
    const updatedItem = { ...item, [field.key]: nextValue };
    onChange({ ...update, item: updatedItem } as AdviserProfileProposalUpdate);
  };
  return <fieldset className="proposal-editor"><legend>Edit {sections[update.section].label} suggestion</legend>
    <div className="proposal-editor-fields">{fields.map((field) => {
      const value = field.commaList
        ? Array.isArray(item[field.key]) ? (item[field.key] as string[]).join(", ") : ""
        : item[field.key] ?? "";
      const control = field.select
        ? <select aria-label={field.label} value={String(value)} onChange={(event) => setField(field, event.target.value)}>
          {credentialTypes.map((credentialType) => <option key={credentialType} value={credentialType}>{credentialType.replaceAll("_", " ")}</option>)}
        </select>
        : field.multiline
          ? <textarea aria-label={field.label} value={String(value)} onChange={(event) => setField(field, event.target.value)} />
          : <input aria-label={field.label} value={String(value)} onChange={(event) => setField(field, event.target.value)} />;
      return <label className="proposal-editor-field" key={field.key}>{field.label}{field.required ? " *" : ""}{control}</label>;
    })}</div>
    <p className="muted">Section and suggestion operation are preserved when you save.</p>
  </fieldset>;
}

function requiredItemError(update: AdviserProfileProposalUpdate): string | null {
  const item = update.item as unknown as Record<string, unknown>;
  const required = itemFields[update.section].filter((field) => field.required);
  return required.some((field) => typeof item[field.key] !== "string" || !(item[field.key] as string).trim())
    ? `Complete the required ${sections[update.section].label.toLowerCase()} fields before saving.`
    : null;
}

function proposalStateText(state: AdviserProfileProposal["state"]): string {
  if (state === "pending") return "Pending suggestion — this is not part of your current Profile.";
  if (state === "rejected") return "Rejected — your Profile was not changed.";
  return "Sent to the Profile workflow. This suggestion does not become current information until the linked Profile revision is confirmed.";
}

export function ProfileSuggestions({ api, confirmedClarification = null, enrichment = null, activeProfileDraft = false, onResolved }: Props) {
  const [historyState, setHistoryState] = useState<HistoryState>("idle");
  const [proposals, setProposals] = useState<AdviserProfileProposal[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [mutation, setMutation] = useState<Mutation>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<AdviserProfileProposalUpdate | null>(null);
  const [editConflict, setEditConflict] = useState(false);
  const [linkedRevisionAtTransfer, setLinkedRevisionAtTransfer] = useState<Record<string, ProfileRevision>>({});
  const requestGeneration = useRef(0);
  const mutationSequence = useRef(0);
  const mutationLock = useRef(false);
  const source = enrichment ? {
    clarification_id: enrichment.clarification_id,
    question_text: enrichment.question_text,
    durableEnrichment: true,
    interpretation: {
      answer_kind: "career_fact",
      confirmed_context_summary: enrichment.confirmed_context_summary,
      proposed_evidence: enrichment.has_profile_evidence ? [{ title: "Confirmed career information", text: enrichment.confirmed_context_summary, skills: [] }] : [],
    },
  } : confirmedClarification;

  const upsert = (incoming: AdviserProfileProposal[]) => setProposals((current) => mergeProposals(current, incoming));
  const loadHistory = async (): Promise<AdviserProfileProposal[] | null> => {
    const request = ++requestGeneration.current;
    setHistoryState("loading");
    setHistoryError("");
    try {
      const found = await api.request<AdviserProfileProposal[]>("/api/v1/candidate-adviser/profile-proposals");
      if (request !== requestGeneration.current) return null;
      setProposals((current) => reconcileHistory(current, found));
      setHistoryState("ready");
      return found;
    } catch {
      if (request === requestGeneration.current) {
        setHistoryState("unavailable");
        setHistoryError("Profile suggestions could not be loaded.");
      }
      return null;
    }
  };

  const beginMutation = (next: Exclude<Mutation, null>): number | null => {
    if (mutationLock.current) return null;
    mutationLock.current = true;
    requestGeneration.current += 1;
    setHistoryState((current) => current === "loading" ? (proposals.length ? "ready" : "idle") : current);
    const sequence = ++mutationSequence.current;
    setMutation(next);
    setActionError("");
    setNotice("");
    return sequence;
  };
  const finishMutation = (sequence: number) => {
    if (sequence !== mutationSequence.current) return;
    mutationLock.current = false;
    setMutation(null);
  };
  const refreshHistoryAfterMutation = () => {
    setHistoryState("ready");
    void loadHistory();
  };

  const generate = async () => {
    if (!source || !source.interpretation || !["career_fact", "mixed"].includes(source.interpretation.answer_kind) || (source.interpretation.proposed_evidence.length === 0 && !("durableEnrichment" in source))) return;
    const sequence = beginMutation({ kind: "generation", id: source.clarification_id });
    if (sequence === null) return;
    try {
      let currentHistory = proposals;
      if (historyState !== "ready") {
        const loaded = await api.request<AdviserProfileProposal[]>("/api/v1/candidate-adviser/profile-proposals");
        if (sequence !== mutationSequence.current) return;
        currentHistory = loaded;
        setProposals((current) => reconcileHistory(current, loaded));
        setHistoryState("ready");
        setHistoryError("");
      }
      if (currentHistory.some((proposal) => proposal.source_clarification_id === source.clarification_id)) {
        setNotice("Profile suggestions from this clarification are available below.");
        return;
      }
      const result = await api.request<AdviserProfileProposalGenerationRead>(
        `/api/v1/candidate-adviser/clarifications/${encodeURIComponent(source.clarification_id)}/profile-proposals`,
        { method: "POST" },
      );
      if (sequence !== mutationSequence.current) return;
      upsert(result.proposals);
      setHistoryState("ready");
      setNotice(result.proposals.length
        ? "Profile suggestions are ready for review. Your current Profile has not changed."
        : "No structured Profile suggestions were produced from this clarification. Your current Profile is unchanged.");
      refreshHistoryAfterMutation();
      onResolved?.();
    } catch (caught) {
      if (sequence !== mutationSequence.current) return;
      setHistoryState((current) => current === "loading" ? "unavailable" : current);
      if (caught instanceof ApiError && caught.status === 502) setActionError("Profile suggestion generation could not return valid suggestions. Try again.");
      else if (caught instanceof ApiError && caught.status === 503) setActionError("Profile suggestion generation is temporarily unavailable.");
      else if (caught instanceof ApiError && (caught.status === 404 || caught.status === 409)) {
        setActionError("This clarification is no longer available for Profile suggestions. Your current Profile was not changed.");
        refreshHistoryAfterMutation();
      } else if (historyState !== "ready" && proposals.length === 0) {
        setHistoryState("unavailable");
        setHistoryError("Profile suggestions could not be loaded.");
      } else setActionError("Profile suggestion generation could not be completed. Try again.");
    } finally {
      finishMutation(sequence);
    }
  };

  const updateEdit = (proposal: AdviserProfileProposal) => {
    setEditingId(proposal.id);
    setEditValue(cloneUpdate(proposal.proposed_update));
    setEditConflict(false);
    setActionError("");
    setNotice("");
  };
  const cancelEdit = () => {
    setEditingId(null);
    setEditValue(null);
    setEditConflict(false);
    setActionError("");
  };
  const saveEdit = async (proposal: AdviserProfileProposal) => {
    if (!editValue) return;
    const invalid = requiredItemError(editValue);
    if (invalid) { setActionError(invalid); return; }
    const sequence = beginMutation({ kind: "edit", id: proposal.id });
    if (sequence === null) return;
    try {
      const saved = await api.request<AdviserProfileProposal>(
        `/api/v1/candidate-adviser/profile-proposals/${encodeURIComponent(proposal.id)}`,
        { method: "PATCH", body: JSON.stringify({ expected_revision: proposal.revision, proposed_update: editValue }) },
      );
      if (sequence !== mutationSequence.current) return;
      upsert([saved]);
      setEditingId(null);
      setEditValue(null);
      setEditConflict(false);
      setNotice("Suggestion saved. Your current Profile is unchanged.");
      refreshHistoryAfterMutation();
    } catch (caught) {
      if (sequence !== mutationSequence.current) return;
      if (caught instanceof ApiError && caught.status === 409) {
        setEditConflict(true);
        setActionError("This suggestion changed elsewhere. Your edits are still shown. Reload the saved suggestion before trying again.");
      } else if (caught instanceof ApiError && caught.status === 404) {
        setActionError("This suggestion is no longer available. Refresh Profile suggestions.");
        refreshHistoryAfterMutation();
      } else setActionError("Suggestion could not be saved. Your edits are still shown.");
    } finally { finishMutation(sequence); }
  };
  const reloadSaved = async (proposal: AdviserProfileProposal) => {
    const sequence = beginMutation({ kind: "reload", id: proposal.id });
    if (sequence === null) return;
    try {
      const found = await api.request<AdviserProfileProposal>(
        `/api/v1/candidate-adviser/profile-proposals/${encodeURIComponent(proposal.id)}`,
      );
      if (sequence !== mutationSequence.current) return;
      upsert([found]);
      const latest = proposals.find((item) => item.id === proposal.id);
      const authoritative = latest && latest.revision > found.revision ? latest : found;
      setEditValue(cloneUpdate(authoritative.proposed_update));
      setEditConflict(false);
      setActionError("");
      setNotice("Saved suggestion reloaded.");
    } catch (caught) {
      if (sequence !== mutationSequence.current) return;
      setActionError(caught instanceof ApiError && caught.status === 404
        ? "This suggestion is no longer available. Refresh Profile suggestions."
        : "The saved suggestion could not be reloaded.");
      if (caught instanceof ApiError && caught.status === 404) refreshHistoryAfterMutation();
    } finally { finishMutation(sequence); }
  };
  const reject = async (proposal: AdviserProfileProposal) => {
    const sequence = beginMutation({ kind: "reject", id: proposal.id });
    if (sequence === null) return;
    try {
      const rejected = await api.request<AdviserProfileProposal>(
        `/api/v1/candidate-adviser/profile-proposals/${encodeURIComponent(proposal.id)}/reject`,
        { method: "POST", body: JSON.stringify({ expected_revision: proposal.revision }) },
      );
      if (sequence !== mutationSequence.current) return;
      upsert([rejected]);
      setNotice("Suggestion rejected. Your current Profile was not changed.");
      if (editingId === proposal.id) cancelEdit();
      refreshHistoryAfterMutation();
    } catch (caught) {
      if (sequence !== mutationSequence.current) return;
      setActionError(caught instanceof ApiError && caught.status === 404
        ? "This suggestion is no longer available. Refresh Profile suggestions."
        : "This suggestion could not be rejected because its saved state changed. Refresh Profile suggestions and try again.");
      if (caught instanceof ApiError && (caught.status === 404 || caught.status === 409)) refreshHistoryAfterMutation();
    } finally { finishMutation(sequence); }
  };
  const transfer = async (proposal: AdviserProfileProposal) => {
    if (activeProfileDraft || proposal.overlap_resolution_stale) return;
    const sequence = beginMutation({ kind: "transfer", id: proposal.id });
    if (sequence === null) return;
    try {
      const result = await api.request<AdviserProfileProposalTransferRead>(
        `/api/v1/candidate-adviser/profile-proposals/${encodeURIComponent(proposal.id)}/transfer`,
        { method: "POST", body: JSON.stringify({ expected_revision: proposal.revision }) },
      );
      if (sequence !== mutationSequence.current) return;
      upsert([result.proposal]);
      setLinkedRevisionAtTransfer((current) => ({ ...current, [proposal.id]: result.profile_revision }));
      setNotice("Profile draft created. Your current Profile is still unchanged until you review and confirm it.");
      if (editingId === proposal.id) cancelEdit();
      refreshHistoryAfterMutation();
    } catch (caught) {
      if (sequence !== mutationSequence.current) return;
      if (caught instanceof ApiError && caught.status === 409) {
        setActionError("This suggestion could not be sent to the Profile workflow because your saved Profile state or this suggestion changed. Review your current Profile changes and try again.");
        refreshHistoryAfterMutation();
      } else if (caught instanceof ApiError && caught.status === 404) {
        setActionError("This suggestion is no longer available in Profile suggestions. The saved history is being refreshed.");
        refreshHistoryAfterMutation();
      } else setActionError("This suggestion could not be sent to the Profile workflow. Try again.");
    } finally { finishMutation(sequence); }
  };
  const replaceCurrent = async (proposal: AdviserProfileProposal, target: string) => {
    const update: AdviserProfileProposalUpdate = { ...proposal.proposed_update, operation: "replace_exact", target_fingerprint: target } as AdviserProfileProposalUpdate;
    const sequence = beginMutation({ kind: "replace", id: proposal.id }); if (sequence === null) return;
    try {
      const saved = await api.request<AdviserProfileProposal>(`/api/v1/candidate-adviser/profile-proposals/${encodeURIComponent(proposal.id)}`, { method: "PATCH", body: JSON.stringify({ expected_revision: proposal.revision, proposed_update: update }) });
      if (sequence !== mutationSequence.current) return;
      upsert([saved]); setNotice("Suggestion now targets that exact current item. Choose Use in Profile draft when you are ready."); refreshHistoryAfterMutation();
    } catch (caught) { if (sequence === mutationSequence.current) { setActionError(caught instanceof ApiError && caught.status === 409 ? "This suggestion or current Profile changed. Refresh Profile suggestions and compare again." : "The replacement choice could not be saved. Refresh Profile suggestions and try again."); refreshHistoryAfterMutation(); } }
    finally { finishMutation(sequence); }
  };
  const keepAsNew = async (proposal: AdviserProfileProposal) => {
    if (!proposal.comparison_base_fingerprint) return;
    const sequence = beginMutation({ kind: "resolve", id: proposal.id }); if (sequence === null) return;
    const body: AdviserProfileProposalOverlapResolutionRequest = { expected_revision: proposal.revision, expected_comparison_base_fingerprint: proposal.comparison_base_fingerprint, action: "add_as_new" };
    try {
      const saved = await api.request<AdviserProfileProposal>(`/api/v1/candidate-adviser/profile-proposals/${encodeURIComponent(proposal.id)}/resolve-overlap`, { method: "POST", body: JSON.stringify(body) });
      if (sequence !== mutationSequence.current) return;
      upsert([saved]); setNotice("You chose to keep this as a separate Profile item. Review the Profile draft after sending it."); refreshHistoryAfterMutation();
    } catch (caught) { if (sequence === mutationSequence.current) { setActionError(caught instanceof ApiError && caught.status === 409 ? "Your Profile changed after this comparison. Refresh Profile suggestions before choosing again." : "This overlap choice could not be saved. Refresh Profile suggestions and try again."); refreshHistoryAfterMutation(); } }
    finally { finishMutation(sequence); }
  };
  const refreshComparison = async (proposal: AdviserProfileProposal) => {
    const sequence = beginMutation({ kind: "reload", id: proposal.id }); if (sequence === null) return;
    try { const saved = await api.request<AdviserProfileProposal>(`/api/v1/candidate-adviser/profile-proposals/${encodeURIComponent(proposal.id)}`); if (sequence === mutationSequence.current) { upsert([saved]); setNotice("Comparison refreshed from the saved suggestion."); } }
    catch { if (sequence === mutationSequence.current) setActionError("This comparison could not be refreshed. Reload Profile suggestions and try again."); }
    finally { finishMutation(sequence); }
  };

  const interpretation = source?.interpretation;
  const isCareerFact = Boolean(interpretation && ["career_fact", "mixed"].includes(interpretation.answer_kind));
  const hasEvidence = Boolean(interpretation?.proposed_evidence.length);
  const sourceHasSuggestions = Boolean(source && proposals.some(
    (proposal) => proposal.source_clarification_id === source.clarification_id,
  ));
  const actionBusy = Boolean(mutation);

  return <section className="card profile-suggestions" aria-labelledby="profile-suggestions-heading">
    <h2 id="profile-suggestions-heading" tabIndex={-1}>Profile changes to review</h2>
    <p>Career Adviser can suggest structured career information from facts you explicitly confirmed. Suggestions do not change your current Profile until you send one to the Profile workflow and later confirm that Profile draft.</p>
    {source && isCareerFact && <div className="suggestion-generation">
      <h3>This new career information may improve your Profile</h3>
      <p>{source.question_text}</p>
      {interpretation && <p>{interpretation.confirmed_context_summary}</p>}
      <p className="muted">Suggestions use only the career facts you confirmed above. Generating suggestions does not change your current Profile.</p>
        {!hasEvidence
          ? <>{enrichment ? <><p className="muted">No structured Profile update is available from this confirmed information.</p><button className="button-secondary" disabled={actionBusy || historyState === "loading"} onClick={() => void generate()}>{mutation?.kind === "generation" && mutation.id === source?.clarification_id ? "Finishing review…" : "Finish Profile review"}</button></> : <p className="muted">This clarification did not produce a structured Profile suggestion.</p>}</>
        : sourceHasSuggestions
          ? <p role="status">Profile suggestions from this clarification are available below.</p>
          : <><button disabled={actionBusy || historyState === "loading"} onClick={() => void generate()}>
            {mutation?.kind === "generation" && mutation.id === source.clarification_id ? "Preparing Profile changes…" : "Review for Profile"}
          </button>{activeProfileDraft && <p className="notice">You already have Profile changes in progress. You can review, edit, or reject this Adviser suggestion now, but it cannot be sent to the Profile workflow until you finish or discard the existing draft.</p>}{enrichment && <button type="button" className="button-secondary" disabled={actionBusy} onClick={async () => { try { await api.request<void>(`/api/v1/candidate-adviser/clarifications/${encodeURIComponent(enrichment.clarification_id)}/profile-enrichment/defer`, { method: "POST" }); setNotice("You can continue without reviewing this Profile update now."); onResolved?.(); } catch { setActionError("This Profile update could not be deferred. Refresh Career Adviser and try again."); } }}>Do this later</button>}
          </>}
    </div>}
    {historyState === "idle" && <button className="button-secondary" onClick={() => void loadHistory()}>View profile suggestions</button>}
    {historyState === "ready" && <button className="button-secondary" onClick={() => void loadHistory()}>Refresh profile suggestions</button>}
    {historyState === "loading" && <p className="muted" role="status">Loading profile suggestions…</p>}
    {historyError && <div className="proposal-error"><p role="alert">{historyError}</p><button className="button-secondary" onClick={() => void loadHistory()}>Retry</button></div>}
    {notice && <p className="profile-suggestion-notice" role="status">{notice}</p>}
    {actionError && <div className="proposal-error"><p role="alert">{actionError}</p>{actionError.includes("Profile") && <Link to="/profile">Open Profile</Link>}{editConflict && editingId && <button className="button-secondary" disabled={actionBusy} onClick={() => { const current = proposals.find((item) => item.id === editingId); if (current) void reloadSaved(current); }}>Reload saved suggestion</button>}</div>}
    {historyState === "ready" && proposals.length === 0 && <p className="muted">No Profile suggestions yet.</p>}
    {(historyState === "ready" || proposals.length > 0) && <div className="suggestion-list">{proposals.map((proposal) => {
      const section = sections[proposal.proposed_update.section];
      const isEditing = editingId === proposal.id && editValue !== null;
      const changed = !sameUpdate(proposal.original_update, proposal.proposed_update);
      const busy = actionBusy;
      return <article className="suggestion-card" key={proposal.id} aria-labelledby={`suggestion-${proposal.id}`}>
        <div className="section-heading suggestion-heading"><div><h3 id={`suggestion-${proposal.id}`}>{section.label}</h3><p className="muted">{proposal.proposed_update.operation === "add" ? `Suggested new ${section.noun}` : `Suggested refinement to an existing ${section.noun}`}</p></div><span className={`suggestion-state suggestion-state-${proposal.state}`}>{proposal.state.replaceAll("_", " ")}</span></div>
        <p className="muted">Based on a confirmed Candidate Adviser clarification</p>
        <p>{proposalStateText(proposal.state)}</p>
        {proposal.state === "pending" && proposal.comparison && <section className="proposal-overlap" aria-label="Profile comparison">
          <h4><span className="relationship-badge">{relationshipLabel(proposal.comparison.relationship)}</span></h4>
          {proposal.comparison.relationship === "new" && <p className="muted">New information — no overlapping current Profile item was found.</p>}
          {proposal.comparison.relationship === "reinforcement" && <p className="muted">Same fact — Career-trans will keep one current item and record this Adviser suggestion as additional source support after you confirm the Profile draft.</p>}
          {proposal.comparison.relationship === "refinement" && <p>The Adviser suggestion is a more detailed version of a current Profile item.</p>}
          {proposal.comparison.relationship === "conflict" && <p>The suggestion conflicts with an existing Profile item. Choose whether this suggestion should replace that exact item, edit the suggestion, or reject it.</p>}
          {proposal.comparison.relationship === "ambiguous" && <p>This suggestion may overlap multiple current Profile items. Choose a uniquely identifiable target or explicitly keep it separate.</p>}
          {(proposal.comparison.relationship === "refinement" || proposal.comparison.relationship === "conflict") && proposal.comparison.current_item && <div className="overlap-sides"><StructuredItemPresentation section={proposal.comparison.section} item={proposal.comparison.current_item} label="Current Profile" /><StructuredItemPresentation section={proposal.comparison.section} item={proposal.proposed_update.item} label="Adviser suggestion" /></div>}
          {proposal.comparison.relationship === "ambiguous" && <div className="overlap-candidates"><h4>Possible current items</h4>{(() => { const counts = new Map<string, number>(); proposal.comparison!.candidate_matches.forEach((match) => counts.set(match.fingerprint, (counts.get(match.fingerprint) ?? 0) + 1)); return proposal.comparison!.candidate_matches.map((match, index) => <article className="overlap-candidate" key={`${match.fingerprint}-${index}`}><StructuredItemPresentation section={proposal.comparison!.section} item={match.item} label={`Current item ${index + 1}`} />{counts.get(match.fingerprint) === 1 ? <button type="button" disabled={busy} aria-label={`Replace ${structuredItemLabel(proposal.comparison!.section, match.item)}`} onClick={() => void replaceCurrent(proposal, match.fingerprint)}>Replace this item</button> : <p className="muted">These current items cannot be uniquely targeted yet. You can keep the suggestion separate, edit it, or reject it.</p>}</article>); })()}</div>}
          {proposal.comparison.relationship === "refinement" && proposal.comparison.target_fingerprint && <button type="button" className="button-secondary" disabled={busy} onClick={() => void replaceCurrent(proposal, proposal.comparison!.target_fingerprint!)}>Replace current item</button>}
          {proposal.comparison.relationship === "conflict" && proposal.comparison.target_fingerprint && <button type="button" className="button-secondary" disabled={busy} onClick={() => void replaceCurrent(proposal, proposal.comparison!.target_fingerprint!)}>Replace current item</button>}
          {proposal.comparison.relationship === "ambiguous" && proposal.overlap_resolution === null && <button type="button" className="button-secondary" disabled={busy || !proposal.comparison_base_fingerprint} onClick={() => void keepAsNew(proposal)}>Keep as separate new item</button>}
        </section>}
        {proposal.state === "pending" && proposal.overlap_resolution && <p className="notice" role="status">You chose to keep this as a separate Profile item. Review the Profile draft after sending it.</p>}
        {proposal.state === "pending" && proposal.overlap_resolution_stale && <div className="profile-warning" role="alert"><p>Your earlier overlap choice is no longer current because the Profile changed.</p><button type="button" className="button-secondary" disabled={busy} onClick={() => void refreshComparison(proposal)}>Refresh comparison</button></div>}
        {changed ? <div className="suggestion-comparison"><ItemSummary label="Adviser suggestion" update={proposal.original_update} /><ItemSummary label="Your edited version" update={proposal.proposed_update} /></div> : <ItemSummary label="Proposed item" update={proposal.proposed_update} />}
        {isEditing && <ProposalEditor update={editValue} onChange={setEditValue} />}
        {proposal.state === "pending" && activeProfileDraft && <p className="notice">You can review, edit, or reject this Adviser suggestion now, but it cannot be sent to the Profile workflow until you finish or discard the existing Profile draft.</p>}
        {proposal.state === "pending" && <div className="suggestion-actions">
          {isEditing ? <>
            <button disabled={busy} onClick={() => void saveEdit(proposal)}>{mutation?.kind === "edit" && mutation.id === proposal.id ? "Saving…" : "Save suggestion"}</button>
            <button className="button-secondary" disabled={busy} onClick={cancelEdit}>Cancel edit</button>
          </> : <>
            <button className="button-secondary" disabled={busy} onClick={() => updateEdit(proposal)}>Edit suggestion</button>
            {activeProfileDraft ? <Link className="button-link" to="/profile">Open current Profile draft</Link> : <button disabled={busy || Boolean(proposal.overlap_resolution_stale)} onClick={() => void transfer(proposal)}>{mutation?.kind === "transfer" && mutation.id === proposal.id ? "Sending to Profile draft…" : "Use in Profile draft"}</button>}
            <button className="button-danger" disabled={busy} onClick={() => void reject(proposal)}>{mutation?.kind === "reject" && mutation.id === proposal.id ? "Rejecting…" : "Reject"}</button>
          </>}
        </div>}
        {proposal.state === "transferred" && <div className="suggestion-actions">{linkedRevisionAtTransfer[proposal.id] && <p className="muted">At transfer, the linked Profile revision was created as {linkedRevisionAtTransfer[proposal.id].state}.</p>}<Link className="button-link" to="/profile">Review Profile changes</Link></div>}
      </article>;
    })}</div>}
  </section>;
}

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "./api";
import { ProfileSuggestions } from "./ProfileSuggestions";
import type {
  AdviserProfileProposal,
  AdviserProfileProposalGenerationRead,
  AdviserProfileProposalTransferRead,
  AdviserProfileProposalUpdate,
} from "./api";

const request = vi.fn<(path: string, init?: RequestInit) => Promise<unknown>>();
const api = { request: request as unknown as <T>(path: string, init?: RequestInit) => Promise<T> };
afterEach(() => { cleanup(); request.mockReset(); });

const createdAt = "2026-08-01T00:00:00Z";
function update(section: AdviserProfileProposalUpdate["section"] = "skills", item?: unknown): AdviserProfileProposalUpdate {
  const items: Record<AdviserProfileProposalUpdate["section"], AdviserProfileProposalUpdate["item"]> = {
    employment: { employer: "Example", title: "Engineer", start_date: null, end_date: null, location: null, description: "Built services" },
    education: { institution: "Example University", qualification: "MSc", field_of_study: null, description: "" },
    credentials: { name: "Cloud certificate", credential_type: "certification", issuer: null, issued_date: null, expiry_date: null, status: null, description: "" },
    skills: { name: "Rust", category: "language" },
    projects: { name: "Platform", description: "Built a platform", skills: ["Rust"] },
    achievements: { text: "Improved reliability" },
  };
  return { section, operation: "add", target_fingerprint: null, item: item ?? items[section] } as AdviserProfileProposalUpdate;
}
function proposal(overrides: Partial<AdviserProfileProposal> = {}): AdviserProfileProposal {
  const original = update("skills");
  return {
    id: "proposal-1", state: "pending", revision: 1,
    source_clarification_id: "clarification-1", source_assessment_fingerprint: "a".repeat(64),
    original_update: original, proposed_update: original, created_at: createdAt, updated_at: createdAt,
    rejected_at: null, transferred_at: null, transferred_profile_revision_id: null,
    comparison: null, comparison_base_fingerprint: null, overlap_resolution: null, overlap_resolution_stale: null,
    ...overrides,
  };
}
function clarification(answer_kind = "career_fact", proposed_evidence = [{ title: "Fact", text: "Confirmed fact", skills: ["Rust"] }]) {
  return { clarification_id: "clarification-1", question_text: "Which language did you use?", interpretation: { answer_kind, confirmed_context_summary: "I used Rust.", proposed_evidence } };
}
function mount(source: ReturnType<typeof clarification> | null = null) {
  return render(<MemoryRouter><ProfileSuggestions api={api} confirmedClarification={source} /></MemoryRouter>);
}
function history(rows: AdviserProfileProposal[]) { request.mockResolvedValueOnce(rows); }
function transferRead(record: AdviserProfileProposal): AdviserProfileProposalTransferRead {
  return { proposal: record, profile_revision: {
    id: "revision-1", state: "draft", revision: 1, proposed_profile: null, proposed_structured: null,
    changed_authorities: ["structured"], stale_authorities: [], created_at: createdAt, updated_at: createdAt,
    confirmed_at: null, discarded_at: null, structured_comparisons: [],
  } };
}

it.each(["career_fact", "mixed"])("shows generation only for affirmative confirmed career clarifications (%s)", async (kind) => {
  mount(clarification(kind));
  expect(await screen.findByRole("button", { name: "Generate profile suggestions" })).toBeEnabled();
  expect(request).not.toHaveBeenCalled();
  expect(screen.getByText(/Suggestions use only the career facts you confirmed above/)).toBeInTheDocument();
});

it.each(["eligibility_fact", "preference_intent", "insufficient"])("does not offer Profile generation for non-career clarifications (%s)", (kind) => {
  mount(clarification(kind));
  expect(screen.queryByRole("button", { name: "Generate profile suggestions" })).not.toBeInTheDocument();
  expect(request).not.toHaveBeenCalled();
});

it("does not generate when a career clarification contains no affirmative evidence", () => {
  mount(clarification("career_fact", []));
  expect(screen.getByText("This clarification did not produce a structured Profile suggestion.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Generate profile suggestions" })).not.toBeInTheDocument();
  expect(request).not.toHaveBeenCalled();
});

it("loads history only on request, isolates failures, and recovers on Retry", async () => {
  request.mockRejectedValueOnce(new Error("offline"));
  mount();
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Profile suggestions could not be loaded.");
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  history([]);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("No Profile suggestions yet.")).toBeInTheDocument();
  expect(request.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/candidate-adviser/profile-proposals", "/api/v1/candidate-adviser/profile-proposals",
  ]);
});

it("prevents duplicate generation, sends one no-body request, and handles zero suggestions as a notice", async () => {
  let finishGeneration!: (result: unknown) => void;
  request.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise((resolve) => { finishGeneration = resolve; })).mockResolvedValueOnce([]);
  mount(clarification());
  const generate = await screen.findByRole("button", { name: "Generate profile suggestions" });
  fireEvent.click(generate);
  expect(await screen.findByRole("button", { name: "Generating profile suggestions…" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Generating profile suggestions…" }));
  expect(request.mock.calls.filter(([path]) => String(path).includes("/clarifications/clarification-1/profile-proposals")).length).toBe(1);
  expect(request).toHaveBeenCalledWith(
    "/api/v1/candidate-adviser/clarifications/clarification-1/profile-proposals",
    { method: "POST" },
  );
  await act(async () => finishGeneration({ proposals: [] } satisfies AdviserProfileProposalGenerationRead));
  expect(await screen.findByRole("status")).toHaveTextContent("No structured Profile suggestions were produced from this clarification. Your current Profile is unchanged.");
});

it("renders returned proposals and preserves backend states", async () => {
  const pending = proposal();
  const rejected = proposal({ id: "rejected", state: "rejected", revision: 2, rejected_at: createdAt });
  const transferred = proposal({ id: "transferred", state: "transferred", revision: 2, transferred_at: createdAt, transferred_profile_revision_id: "revision-1" });
  request.mockResolvedValueOnce([]).mockResolvedValueOnce({ proposals: [pending, rejected, transferred] }).mockResolvedValueOnce([pending, rejected, transferred]);
  mount(clarification());
  fireEvent.click(await screen.findByRole("button", { name: "Generate profile suggestions" }));
  expect(await screen.findByText("Profile suggestions are ready for review. Your current Profile has not changed.")).toBeInTheDocument();
  expect(screen.getByText("Pending suggestion — this is not part of your current Profile.")).toBeInTheDocument();
  expect(screen.getByText("Rejected — your Profile was not changed.")).toBeInTheDocument();
  expect(screen.getByText(/Sent to the Profile workflow/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Review Profile changes" })).toHaveAttribute("href", "/profile");
  expect(screen.getAllByText("Based on a confirmed Candidate Adviser clarification")).toHaveLength(3);
  expect(screen.queryByText("a".repeat(64))).not.toBeInTheDocument();
});

it("keeps transferred and rejected proposal cards read-only", async () => {
  const rejected = proposal({ id: "rejected", state: "rejected", revision: 2, rejected_at: createdAt });
  const transferred = proposal({ id: "transferred", state: "transferred", revision: 2, transferred_at: createdAt, transferred_profile_revision_id: "revision-1" });
  history([rejected, transferred]);
  mount();
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  expect(await screen.findByText("Rejected — your Profile was not changed.")).toBeInTheDocument();
  expect(screen.getByText(/Sent to the Profile workflow/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Use in Profile draft" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit suggestion" })).not.toBeInTheDocument();
});

it.each([502, 503])("shows safe generation error copy for HTTP %i without provider details", async (status) => {
  request.mockResolvedValueOnce([]).mockRejectedValueOnce(new ApiError(status, "private provider response"));
  mount(clarification());
  fireEvent.click(await screen.findByRole("button", { name: "Generate profile suggestions" }));
  const message = status === 502
    ? "Profile suggestion generation could not return valid suggestions. Try again."
    : "Profile suggestion generation is temporarily unavailable.";
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.queryByText("private provider response")).not.toBeInTheDocument();
});

it.each([404, 409])("reports a safe source-state generation conflict for HTTP %i", async (status) => {
  request.mockResolvedValueOnce([]).mockRejectedValueOnce(new ApiError(status, "private backend detail")).mockResolvedValueOnce([]);
  mount(clarification());
  fireEvent.click(await screen.findByRole("button", { name: "Generate profile suggestions" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This clarification is no longer available for Profile suggestions.");
  expect(screen.queryByText("private backend detail")).not.toBeInTheDocument();
});

it("does not encourage repeated generation when history already has suggestions for that source", async () => {
  history([proposal()]);
  mount(clarification());
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  expect(await screen.findByText("Profile suggestions from this clarification are available below.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Generate profile suggestions" })).not.toBeInTheDocument();
});

it("displays original and edited suggestion values without exposing target fingerprints", async () => {
  const record = proposal({
    proposed_update: { section: "skills", operation: "replace_exact", target_fingerprint: "b".repeat(64), item: { name: "Rust programming", category: "language" } },
  });
  history([record]); mount();
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  expect(await screen.findByText("Adviser suggestion")).toBeInTheDocument();
  expect(screen.getByText("Your edited version")).toBeInTheDocument();
  expect(screen.getByText("Rust · language")).toBeInTheDocument();
  expect(screen.getByText("Rust programming · language")).toBeInTheDocument();
  expect(screen.queryByText("b".repeat(64))).not.toBeInTheDocument();
});

it.each([
  ["employment", "Employer", "Example"], ["education", "Institution", "Example University"],
  ["credentials", "Credential type", "certification"], ["skills", "Name", "Rust"],
  ["projects", "Skills (comma separated)", "Rust"], ["achievements", "Achievement", "Improved reliability"],
] as const)("edits the typed %s item fields", async (section, label, value) => {
  history([proposal({ original_update: update(section), proposed_update: update(section) })]);
  mount();
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit suggestion" }));
  expect(await screen.findByRole("group", { name: `Edit ${section === "credentials" ? "Credentials" : section[0].toUpperCase() + section.slice(1)} suggestion` })).toBeInTheDocument();
  expect(screen.getByRole(section === "credentials" ? "combobox" : "textbox", { name: label })).toHaveValue(value);
});

it("saves only the edited item while preserving section, operation, and replacement target", async () => {
  const original = proposal({
    proposed_update: { section: "skills", operation: "replace_exact", target_fingerprint: "c".repeat(64), item: { name: "Rust", category: null } },
  });
  const saved = proposal({ ...original, revision: 2, updated_at: "2026-08-02T00:00:00Z", proposed_update: { section: "skills", operation: "replace_exact", target_fingerprint: "c".repeat(64), item: { name: "Rust systems", category: "language" } } as AdviserProfileProposalUpdate });
  history([original]); request.mockResolvedValueOnce(saved).mockResolvedValueOnce([saved]);
  mount();
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit suggestion" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: "Rust systems" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Category" }), { target: { value: "language" } });
  fireEvent.click(screen.getByRole("button", { name: "Save suggestion" }));
  await screen.findByText("Suggestion saved. Your current Profile is unchanged.");
  const [, options] = request.mock.calls.find(([path, init]) => String(path).endsWith("proposal-1") && init?.method === "PATCH") ?? [];
  expect(options).toMatchObject({ method: "PATCH" });
  expect(JSON.parse(String((options as RequestInit).body))).toEqual({
    expected_revision: 1,
    proposed_update: { section: "skills", operation: "replace_exact", target_fingerprint: "c".repeat(64), item: { name: "Rust systems", category: "language" } },
  });
  expect(screen.getByText("Your edited version")).toBeInTheDocument();
  expect(screen.getByText("Rust systems · language")).toBeInTheDocument();
});

it("cancels local edits without an API call and restores the backend-saved value", async () => {
  history([proposal()]); mount();
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit suggestion" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: "Local only" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel edit" }));
  expect(screen.getByText("Rust · language")).toBeInTheDocument();
  expect(screen.queryByText("Local only")).not.toBeInTheDocument();
  expect(request).toHaveBeenCalledTimes(1);
});

it("preserves unsaved edits on 409 and reloads the authoritative saved suggestion on demand", async () => {
  const record = proposal();
  const serverVersion = proposal({ revision: 2, proposed_update: update("skills", { name: "Remote skill", category: "language" }) });
  history([record]); request.mockRejectedValueOnce(new ApiError(409, "raw conflict")).mockResolvedValueOnce(serverVersion);
  mount();
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit suggestion" }));
  const input = screen.getByRole("textbox", { name: "Name" });
  fireEvent.change(input, { target: { value: "Unsaved local edit" } });
  fireEvent.click(screen.getByRole("button", { name: "Save suggestion" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Your edits are still shown");
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Unsaved local edit");
  fireEvent.click(screen.getByRole("button", { name: "Reload saved suggestion" }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Remote skill"));
  expect(screen.getByRole("status")).toHaveTextContent("Saved suggestion reloaded.");
});

it("does not save an item while required fields are blank", async () => {
  history([proposal({ original_update: update("employment"), proposed_update: update("employment") })]); mount();
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit suggestion" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Employer" }), { target: { value: " " } });
  fireEvent.click(screen.getByRole("button", { name: "Save suggestion" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Complete the required employment fields");
  expect(request).toHaveBeenCalledTimes(1);
});

it("rejects with the expected revision, prevents duplicate clicks, and retains read-only history", async () => {
  let finishReject!: (result: unknown) => void;
  const record = proposal();
  const rejected = proposal({ state: "rejected", revision: 2, rejected_at: createdAt });
  history([record]); request.mockReturnValueOnce(new Promise((resolve) => { finishReject = resolve; })).mockResolvedValueOnce([rejected]);
  mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
  expect(await screen.findByRole("button", { name: "Rejecting…" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Rejecting…" }));
  expect(request.mock.calls.filter(([path]) => String(path).endsWith("/reject"))).toHaveLength(1);
  expect(request).toHaveBeenCalledWith("/api/v1/candidate-adviser/profile-proposals/proposal-1/reject", { method: "POST", body: JSON.stringify({ expected_revision: 1 }) });
  await act(async () => finishReject(rejected));
  expect(await screen.findByText("Suggestion rejected. Your current Profile was not changed.")).toBeInTheDocument();
  expect(screen.getByText("Rejected — your Profile was not changed.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit suggestion" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Use in Profile draft" })).not.toBeInTheDocument();
});

it("transfers with the expected revision, displays the returned draft state, and links to Profile", async () => {
  let finishTransfer!: (result: unknown) => void;
  const record = proposal(); const sent = proposal({ state: "transferred", revision: 2, transferred_at: createdAt, transferred_profile_revision_id: "revision-1" });
  history([record]); request.mockReturnValueOnce(new Promise((resolve) => { finishTransfer = resolve; })).mockResolvedValueOnce([sent]);
  mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Use in Profile draft" }));
  expect(await screen.findByRole("button", { name: "Sending to Profile draft…" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Sending to Profile draft…" }));
  expect(request.mock.calls.filter(([path]) => String(path).endsWith("/transfer"))).toHaveLength(1);
  expect(request).toHaveBeenCalledWith("/api/v1/candidate-adviser/profile-proposals/proposal-1/transfer", { method: "POST", body: JSON.stringify({ expected_revision: 1 }) });
  await act(async () => finishTransfer(transferRead(sent)));
  expect(await screen.findByText("Profile draft created. Your current Profile is still unchanged until you review and confirm it.")).toBeInTheDocument();
  expect(screen.getByText(/At transfer, the linked Profile revision was created as draft/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Review Profile changes" })).toHaveAttribute("href", "/profile");
  expect(request.mock.calls.some(([path]) => /profile\/revisions\/.*\/(review|confirm)/.test(String(path)))).toBe(false);
});

it("leaves a pending proposal unchanged on transfer 409 and offers Profile navigation", async () => {
  const record = proposal(); history([record]); request.mockRejectedValueOnce(new ApiError(409, "private conflict")).mockResolvedValueOnce([record]);
  mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Use in Profile draft" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be sent to the Profile workflow");
  expect(screen.getByRole("link", { name: "Open Profile" })).toHaveAttribute("href", "/profile");
  expect(screen.getByText("Pending suggestion — this is not part of your current Profile.")).toBeInTheDocument();
});

it("refreshes history after transfer 404 without fabricating a transferred state", async () => {
  const record = proposal(); history([record]);
  request.mockRejectedValueOnce(new ApiError(404, "private not found")).mockResolvedValueOnce([]);
  mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Use in Profile draft" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer available in Profile suggestions");
  await waitFor(() => expect(screen.getByText("No Profile suggestions yet.")).toBeInTheDocument());
  expect(screen.queryByText("Sent to the Profile workflow")).not.toBeInTheDocument();
});

it.each(["transfer", "reject"])("does not let a delayed history response resurrect pending after a successful %s", async (action) => {
  let finishOldHistory!: (result: unknown) => void;
  const record = proposal();
  const terminal = action === "transfer"
    ? proposal({ state: "transferred", revision: 2, transferred_at: createdAt, transferred_profile_revision_id: "revision-1" })
    : proposal({ state: "rejected", revision: 2, rejected_at: createdAt });
  history([record]); mount();
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  expect(await screen.findByRole("button", { name: "Refresh profile suggestions" })).toBeInTheDocument();
  request.mockReturnValueOnce(new Promise((resolve) => { finishOldHistory = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Refresh profile suggestions" }));
  request.mockResolvedValueOnce(action === "transfer" ? transferRead(terminal) : terminal).mockResolvedValueOnce([terminal]);
  fireEvent.click(screen.getByRole("button", { name: action === "transfer" ? "Use in Profile draft" : "Reject" }));
  await waitFor(() => expect(screen.getByText(action === "transfer" ? /Sent to the Profile workflow/ : "Rejected — your Profile was not changed.")).toBeInTheDocument());
  await act(async () => finishOldHistory([record]));
  expect(screen.getByText(action === "transfer" ? /Sent to the Profile workflow/ : "Rejected — your Profile was not changed.")).toBeInTheDocument();
  expect(screen.queryByText("Pending suggestion — this is not part of your current Profile.")).not.toBeInTheDocument();
});

it("keeps a newer history record when an older edit response arrives later", async () => {
  let finishEdit!: (result: unknown) => void;
  let finishRefresh!: (result: unknown) => void;
  const record = proposal();
  const oldSaved = proposal({ revision: 2, proposed_update: update("skills", { name: "Older save", category: "language" }) });
  const current = proposal({ state: "rejected", revision: 3, rejected_at: createdAt });
  history([record]); mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit suggestion" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: "Older save" } });
  request.mockReturnValueOnce(new Promise((resolve) => { finishEdit = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Save suggestion" }));
  request.mockReturnValueOnce(new Promise((resolve) => { finishRefresh = resolve; })).mockResolvedValueOnce([current]);
  fireEvent.click(screen.getByRole("button", { name: "Refresh profile suggestions" }));
  await act(async () => finishRefresh([current]));
  await act(async () => finishEdit(oldSaved));
  expect(screen.getByText("Rejected — your Profile was not changed.")).toBeInTheDocument();
  expect(screen.queryByText("Older save · language")).not.toBeInTheDocument();
});

it("does not let a delayed generation response replace a newer rejected record", async () => {
  let finishGeneration!: (result: unknown) => void;
  const rejected = proposal({ state: "rejected", revision: 2, rejected_at: createdAt });
  request.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise((resolve) => { finishGeneration = resolve; })).mockResolvedValueOnce([rejected]).mockResolvedValueOnce([rejected]);
  mount(clarification());
  fireEvent.click(await screen.findByRole("button", { name: "Generate profile suggestions" }));
  expect(await screen.findByRole("button", { name: "Generating profile suggestions…" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Refresh profile suggestions" }));
  expect(await screen.findByText("Rejected — your Profile was not changed.")).toBeInTheDocument();
  await act(async () => finishGeneration({ proposals: [proposal()] }));
  expect(screen.getByText("Rejected — your Profile was not changed.")).toBeInTheDocument();
  expect(screen.queryByText("Pending suggestion — this is not part of your current Profile.")).not.toBeInTheDocument();
});

const comparison = (relationship: "new" | "reinforcement" | "refinement" | "conflict" | "ambiguous", candidates = [{ fingerprint: "f".repeat(64), item: { name: "Rust", category: "language" } }]) => ({
  section: "skills" as const, relationship, incoming_item: { name: "Rust", category: "systems" }, incoming_fingerprint: "e".repeat(64),
  candidate_matches: candidates, target_fingerprint: relationship === "refinement" || relationship === "conflict" ? "f".repeat(64) : null,
  current_item: relationship === "refinement" || relationship === "conflict" ? candidates[0].item : null,
});

it("offers exact replacement for Adviser refinement/conflict and keeps transfer as a separate action", async () => {
  for (const relation of ["refinement", "conflict"] as const) {
    cleanup(); request.mockReset();
    const current = proposal({ comparison: comparison(relation), comparison_base_fingerprint: "a".repeat(64) });
    const replaced = proposal({ revision: 2, proposed_update: { section: "skills", operation: "replace_exact", target_fingerprint: "f".repeat(64), item: { name: "Rust", category: "systems" } }, comparison: comparison("reinforcement") });
    request.mockResolvedValueOnce([current]);
    mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
    expect(await screen.findByText(relation === "refinement" ? "More detailed version" : "Conflicting information")).toBeInTheDocument();
    expect(screen.getByText("Current Profile")).toBeInTheDocument(); expect(screen.getByText("Adviser suggestion")).toBeInTheDocument();
    request.mockResolvedValueOnce(replaced).mockResolvedValueOnce([replaced]);
    fireEvent.click(screen.getByRole("button", { name: "Replace current item" }));
    await screen.findByText(/Suggestion now targets that exact current item/);
    expect(request).toHaveBeenCalledWith("/api/v1/candidate-adviser/profile-proposals/proposal-1", expect.objectContaining({ method: "PATCH", body: expect.stringContaining('"operation":"replace_exact"') }));
    expect(screen.getByRole("button", { name: "Use in Profile draft" })).toBeEnabled();
  }
});

it("shows every Adviser ambiguous candidate and persists explicit add-as-new without transferring", async () => {
  const candidates = ["f".repeat(64), "1".repeat(64)].map((fingerprint, index) => ({ fingerprint, item: { name: `Rust ${index + 1}`, category: "language" } }));
  const current = proposal({ comparison: comparison("ambiguous", candidates), comparison_base_fingerprint: "a".repeat(64) });
  const resolved = proposal({ revision: 2, comparison: current.comparison, comparison_base_fingerprint: current.comparison_base_fingerprint, overlap_resolution: { action: "add_as_new", base_structured_fingerprint: "a".repeat(64), incoming_fingerprint: "e".repeat(64), candidate_fingerprints: candidates.map((item) => item.fingerprint), resolved_at: createdAt } });
  request.mockResolvedValueOnce([current]); mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  expect(await screen.findByText("Possible duplicate")).toBeInTheDocument(); expect(screen.getByText("Rust 1 · language")).toBeInTheDocument(); expect(screen.getByText("Rust 2 · language")).toBeInTheDocument();
  request.mockResolvedValueOnce(resolved).mockResolvedValueOnce([resolved]);
  fireEvent.click(screen.getByRole("button", { name: "Keep as separate new item" }));
  expect((await screen.findAllByText(/You chose to keep this as a separate Profile item/)).length).toBeGreaterThan(0);
  expect(request).toHaveBeenCalledWith("/api/v1/candidate-adviser/profile-proposals/proposal-1/resolve-overlap", expect.objectContaining({ method: "POST", body: JSON.stringify({ expected_revision: 1, expected_comparison_base_fingerprint: "a".repeat(64), action: "add_as_new" }) }));
  expect(request.mock.calls.some(([path]) => String(path).endsWith("/transfer"))).toBe(false);
});

it("does not offer non-unique Adviser replacement targets and disables stale-choice transfer", async () => {
  const duplicate = "f".repeat(64);
  const candidates = [1, 2].map((index) => ({ fingerprint: duplicate, item: { name: `Rust ${index}`, category: "language" } }));
  const stale = proposal({ comparison: comparison("ambiguous", candidates), comparison_base_fingerprint: "a".repeat(64), overlap_resolution: { action: "add_as_new", base_structured_fingerprint: "a".repeat(64), incoming_fingerprint: "e".repeat(64), candidate_fingerprints: [duplicate, duplicate], resolved_at: createdAt }, overlap_resolution_stale: true });
  request.mockResolvedValueOnce([stale]); mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  await waitFor(() => expect(screen.getAllByText(/cannot be uniquely targeted yet/)).toHaveLength(2));
  expect(screen.queryByRole("button", { name: /Replace this item/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Use in Profile draft" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent(/earlier overlap choice is no longer current/);
  expect(screen.getByRole("button", { name: "Refresh comparison" })).toBeEnabled();
});

it.each(["new", "reinforcement"] as const)("allows normal Adviser draft transfer for %s comparisons", async (relation) => {
  const current = proposal({ comparison: comparison(relation), comparison_base_fingerprint: "a".repeat(64) });
  const transferred = proposal({ state: "transferred", revision: 2, transferred_at: createdAt, transferred_profile_revision_id: "revision-1", comparison: current.comparison });
  request.mockResolvedValueOnce([current]); mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  expect(await screen.findByText(relation === "new" ? /no overlapping current Profile item was found/ : /keep one current item/)).toBeInTheDocument();
  request.mockResolvedValueOnce(transferRead(transferred)).mockResolvedValueOnce([transferred]);
  fireEvent.click(screen.getByRole("button", { name: "Use in Profile draft" }));
  expect(await screen.findByText(/Sent to the Profile workflow/)).toBeInTheDocument();
  expect(request).toHaveBeenCalledWith("/api/v1/candidate-adviser/profile-proposals/proposal-1/transfer", expect.objectContaining({ method: "POST" }));
});

it("does not let an old Adviser history GET erase a newer saved add-as-new decision", async () => {
  const candidates = [{ fingerprint: "f".repeat(64), item: { name: "Rust", category: "language" } }, { fingerprint: "1".repeat(64), item: { name: "Rust", category: "platform" } }];
  const record = proposal({ comparison: comparison("ambiguous", candidates), comparison_base_fingerprint: "a".repeat(64) });
  const resolved = proposal({ revision: 2, comparison: record.comparison, comparison_base_fingerprint: record.comparison_base_fingerprint, overlap_resolution: { action: "add_as_new", base_structured_fingerprint: "a".repeat(64), incoming_fingerprint: "e".repeat(64), candidate_fingerprints: candidates.map((item) => item.fingerprint), resolved_at: createdAt } });
  let finishOldHistory!: (value: unknown) => void;
  history([record]); mount(); fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  await screen.findByText("Possible duplicate");
  request.mockReturnValueOnce(new Promise((resolve) => { finishOldHistory = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Refresh profile suggestions" }));
  request.mockResolvedValueOnce(resolved).mockResolvedValueOnce([resolved]);
  fireEvent.click(screen.getByRole("button", { name: "Keep as separate new item" }));
  expect((await screen.findAllByText(/You chose to keep this as a separate Profile item/)).length).toBeGreaterThan(0);
  await act(async () => finishOldHistory([record]));
  expect(screen.getAllByText(/You chose to keep this as a separate Profile item/).length).toBeGreaterThan(0);
  expect(screen.queryByText(/New information — no overlapping current Profile item/)).not.toBeInTheDocument();
});

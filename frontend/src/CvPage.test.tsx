import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "./auth";
import { CvPage } from "./CvPage";

const request = vi.fn();
vi.mock("./auth", () => ({
  ApiError: class ApiError extends Error { constructor(public status: number, message = "") { super(message); } },
  useAuth: () => ({ api: { request } }),
}));
afterEach(() => { cleanup(); request.mockReset(); });

const noDraft = { profile_exists: true, candidate_context_ready: false, latest_cv_draft: null, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 0, journey: { candidate_context_ready: false, job_search_ready: false, intake_exists: false, assessment_status: null, confirmed_guidance_active: false, current_follow_up_available: false, clarification_interpretation_awaiting_confirmation: false, unresolved_profile_enrichment_count: 0, next_enrichment_clarification_id: null, next_enrichment: null, active_profile_draft: false, next_action: "complete_profile", status_category: "setup", confirmed_clarification_count: 0 } } };
type TestCVData = {
  employment: Array<Record<string, unknown>>;
  education: Array<Record<string, unknown>>;
  credentials: Array<Record<string, unknown>>;
  skills: Array<Record<string, unknown>>;
  projects: Array<Record<string, unknown>>;
  achievements: Array<Record<string, unknown>>;
  evidence: Array<Record<string, unknown>>;
};
const mergedEmpty: TestCVData = { employment: [], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] };
const emptyOverlap = (draftId = "draft-test") => ({ draft_id: draftId, revision: 0, base_structured_fingerprint: "a".repeat(64), draft_fingerprint: "b".repeat(64), stale: false, items: [], incoming_duplicates: [] });
const statusWith = (id: string, state: "uploaded" | "review_ready" | "confirmed", candidate_context_ready = false) => ({
  ...noDraft,
  candidate_context_ready,
  latest_cv_draft: { id, state, created_at: "", updated_at: "" },
});
const draftWith = (id: string, state: "uploaded" | "review_ready" | "confirmed", merged: TestCVData | null = null) => ({
  id, state, documents: [{ provenance: { filename: "cv.md" } }], merged, created_at: "", updated_at: "",
  runtime_attribution: state === "uploaded" ? null : { status: "legacy_unavailable", provider: null, operations: {} },
});
const sizedFile = (name: string, size: number) => {
  const file = new File(["x"], name, { type: "text/markdown" });
  Object.defineProperty(file, "size", { value: size });
  return file;
};

it("waits for authoritative status before rendering first upload and validates local selection", async () => {
  let resolve!: (value: unknown) => void;
  request.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  render(<CvPage />);
  expect(screen.getByText("Loading CV onboarding…")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Upload your CV" })).not.toBeInTheDocument();
  resolve(noDraft);
  expect(await screen.findByRole("heading", { name: "Upload your CV" })).toBeInTheDocument();
  const input = screen.getByLabelText("CV files");
  fireEvent.change(input, { target: { files: [new File(["x"], "unsupported.exe")] } });
  expect(screen.getByRole("alert")).toHaveTextContent("PDF, DOCX, Markdown, or JSON");
});

it("status failure stays unavailable and never masquerades as no draft", async () => {
  request.mockRejectedValueOnce(new Error("offline"));
  render(<CvPage />);
  expect(await screen.findByRole("alert")).toHaveTextContent("CV status is unavailable");
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Upload your CV" })).not.toBeInTheDocument();
});

it("keeps uploaded hashes and extracted source references in a wrapping container", async () => {
  const hash = "a".repeat(64);
  const segmentId = "cv-segment-" + "b".repeat(64);
  request
    .mockResolvedValueOnce(statusWith("draft-source", "uploaded"))
    .mockResolvedValueOnce({
      id: "draft-source", state: "uploaded", merged: null, created_at: "", updated_at: "", runtime_attribution: null,
      documents: [{
        provenance: { filename: "synthetic.md", media_type: "text/markdown", document_sha256: hash, segment_ids: [segmentId] },
        segments: [{ segment_id: segmentId, heading: "Synthetic source", page_number: 1, text: "Synthetic extracted content" }],
      }],
    });
  render(<CvPage />);
  const sourceHeading = await screen.findByRole("heading", { name: "Uploaded source representation" });
  const source = sourceHeading.closest("section")!;
  expect(source).toHaveClass("cv-source-representation");
  const hashReference = source.querySelector("li");
  expect(hashReference).toHaveClass("cv-source-reference");
  expect(hashReference).toHaveTextContent(hash);
  expect(within(source).getByText("Synthetic extracted content").closest("pre")).toHaveClass("cv-source-text");
});

it("does not render first-upload UI while a known latest draft is still loading", async () => {
  let finishDraft!: (value: unknown) => void;
  request
    .mockResolvedValueOnce(statusWith("draft-1", "uploaded"))
    .mockImplementationOnce(() => new Promise((resolve) => { finishDraft = resolve; }));
  render(<CvPage />);
  expect(await screen.findByRole("heading", { name: "Loading your latest CV" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Upload your CV" })).not.toBeInTheDocument();
  finishDraft(draftWith("draft-1", "uploaded"));
  expect(await screen.findByRole("button", { name: "Interpret CV" })).toBeEnabled();
});

it("validates file count, individual size, and total size locally", async () => {
  request.mockResolvedValueOnce(noDraft);
  render(<CvPage />);
  const input = await screen.findByLabelText("CV files");

  fireEvent.change(input, { target: { files: Array.from({ length: 6 }, (_, index) => sizedFile(`cv-${index}.md`, 10)) } });
  expect(screen.getByRole("alert")).toHaveTextContent("no more than 5");

  fireEvent.change(input, { target: { files: [sizedFile("large.md", 5 * 1024 * 1024 + 1)] } });
  expect(screen.getByRole("alert")).toHaveTextContent("no larger than 5 MB");

  fireEvent.change(input, { target: { files: Array.from({ length: 4 }, (_, index) => sizedFile(`batch-${index}.md`, 4 * 1024 * 1024)) } });
  expect(screen.getByRole("alert")).toHaveTextContent("15 MB total");
});


it("removes selected local files before upload", async () => {
  request.mockResolvedValueOnce(noDraft);
  render(<CvPage />);
  const input = await screen.findByLabelText("CV files");
  fireEvent.change(input, { target: { files: [new File(["a"], "a.md"), new File(["b"], "b.md")] } });
  expect(screen.getByText("a.md")).toBeInTheDocument();
  expect(screen.getByText("b.md")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
  expect(screen.queryByText("a.md")).not.toBeInTheDocument();
  expect(screen.getByText("b.md")).toBeInTheDocument();
});

it("uploads FormData and resumes the authoritative newly-created draft", async () => {
  const uploaded = draftWith("draft-new", "uploaded");
  request
    .mockResolvedValueOnce(noDraft)
    .mockResolvedValueOnce(uploaded)
    .mockResolvedValueOnce(statusWith("draft-new", "uploaded"))
    .mockResolvedValueOnce(uploaded);
  render(<CvPage />);
  const input = await screen.findByLabelText("CV files");
  fireEvent.change(input, { target: { files: [new File(["cv"], "cv.md", { type: "text/markdown" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload CV" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/cv-ingestion/upload", expect.objectContaining({ method: "POST", body: expect.any(FormData) })));
  expect(await screen.findByRole("button", { name: "Interpret CV" })).toBeEnabled();
});

it("resumes uploaded drafts explicitly and warns before superseding an unfinished draft", async () => {
  request.mockResolvedValueOnce(statusWith("draft-1", "uploaded")).mockResolvedValueOnce(draftWith("draft-1", "uploaded"));
  render(<CvPage />);
  expect(await screen.findByRole("button", { name: "Interpret CV" })).toBeEnabled();
  expect(request.mock.calls.some(([path]) => String(path).includes("/confirm"))).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "Start new CV upload" }));
  expect(screen.getByRole("heading", { name: "Start a newer CV upload?" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Upload your CV" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Continue with new upload" }));
  expect(screen.getByRole("heading", { name: "Upload your CV" })).toBeInTheDocument();
});

it("confirmed resume exposes Update CV and preserves the active-context message during a newer upload", async () => {
  request.mockResolvedValueOnce(statusWith("draft-c", "confirmed", true)).mockResolvedValueOnce(draftWith("draft-c", "confirmed", mergedEmpty));
  render(<CvPage />);
  expect(await screen.findByRole("heading", { name: "CV confirmed" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "View current Profile" })).toHaveAttribute("href", "/profile");
  fireEvent.click(screen.getByRole("button", { name: "Update CV / Upload newer CV" }));
  expect(screen.getByRole("heading", { name: "Upload your CV" })).toBeInTheDocument();
  expect(screen.getByText(/current structured career information remains in use/)).toBeInTheDocument();
});

it("keeps semantic evidence read-only and blocks confirmation until exclusions are saved", async () => {
  const merged = { ...mergedEmpty, evidence: [{ evidence_type: "project", title: "Claim", text: "Source-supported claim", skills: ["Python"], provenance: [{ source_kind: "cv" as const, document_sha256: "hash", segment_ids: ["hash:1"] }] }] };
  const draft = draftWith("draft-2", "review_ready", merged);
  request
    .mockResolvedValueOnce(statusWith("draft-2", "review_ready", true))
    .mockResolvedValueOnce(draft)
    .mockResolvedValueOnce(emptyOverlap("draft-2"))
    .mockResolvedValueOnce({ ...draft, merged: { ...merged, evidence: [] } })
    .mockResolvedValueOnce(emptyOverlap("draft-2"));
  render(<CvPage />);
  expect(await screen.findByText(/From uploaded CV/)).toBeInTheDocument();
  expect(screen.queryByDisplayValue("Source-supported claim")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Exclude" }));
  expect(screen.getByText(/unsaved review changes/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/cv-ingestion/draft-2", expect.objectContaining({ method: "PATCH" })));
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeEnabled());
  expect(request.mock.calls.some(([path]) => String(path).includes("/confirm"))).toBe(false);
});

it("makes upload pending state visible and blocks duplicate upload requests", async () => {
  let finishUpload!: (value: unknown) => void;
  request.mockResolvedValueOnce(noDraft).mockImplementationOnce(() => new Promise((resolve) => { finishUpload = resolve; })).mockResolvedValueOnce(noDraft);
  render(<CvPage />);
  const input = await screen.findByLabelText("CV files");
  fireEvent.change(input, { target: { files: [new File(["synthetic"], "cv.md", { type: "text/markdown" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload CV" }));
  expect(await screen.findByRole("button", { name: "Uploading…" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Uploading CV…");
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/cv-ingestion/upload")).toHaveLength(1);
  await act(async () => { finishUpload(draftWith("draft-uploading", "uploaded")); });
});

it("shows CV review save pending state and success only after the PATCH resolves", async () => {
  const draft = draftWith("draft-save", "review_ready", mergedEmpty);
  const saved = { ...draft, merged: mergedEmpty };
  let finishSave!: (value: unknown) => void;
  request
    .mockResolvedValueOnce(statusWith("draft-save", "review_ready"))
    .mockResolvedValueOnce(draft)
    .mockResolvedValueOnce(emptyOverlap("draft-save"))
    .mockImplementationOnce(() => new Promise((resolve) => { finishSave = resolve; }))
    .mockResolvedValueOnce(emptyOverlap("draft-save"));
  render(<CvPage />);
  await screen.findByRole("heading", { name: "Review your CV" });
  fireEvent.click(screen.getByRole("button", { name: "Add Credential" }));
  fireEvent.change(screen.getByLabelText("name"), { target: { value: "Synthetic credential" } });

  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(screen.getByText("Saving CV changes…")).toHaveAttribute("role", "status");
  expect(screen.queryByText("CV changes saved.")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Saving…" }));
  expect(request.mock.calls.filter(([path, init]) => path === "/api/v1/cv-ingestion/draft-save" && init?.method === "PATCH")).toHaveLength(1);

  await act(async () => { finishSave(saved); });
  expect(await screen.findByText("CV changes saved.")).toBeInTheDocument();
  expect(screen.queryByText(/unsaved review changes/i)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeEnabled();
  expect(request.mock.calls.filter(([path, init]) => path === "/api/v1/cv-ingestion/draft-save" && init?.method === "PATCH")).toHaveLength(1);
});

it("locks every CV review mutation while save owns the snapshot and restores edits after failure", async () => {
  const merged: TestCVData = {
    ...mergedEmpty,
    employment: [{ employer: "Synthetic", title: "Engineer", start_date: "", end_date: "", location: "", description: "Original description" }],
    credentials: [{ name: "Synthetic credential", credential_type: "certification", issuer: "", issued_date: "", expiry_date: "", status: "", description: "" }],
    evidence: [{ evidence_type: "project", title: "Synthetic evidence", text: "Retained fact", skills: ["Python"], provenance: [] }],
  };
  const draft = draftWith("draft-locked", "review_ready", merged);
  let rejectSave!: (error: Error) => void;
  request
    .mockResolvedValueOnce(statusWith("draft-locked", "review_ready"))
    .mockResolvedValueOnce(draft)
    .mockResolvedValueOnce(emptyOverlap("draft-locked"))
    .mockImplementationOnce(() => new Promise((_, reject) => { rejectSave = reject; }));
  render(<CvPage />);
  await screen.findByRole("heading", { name: "Review your CV" });
  const employment = screen.getByRole("group", { name: "Employment 1" });
  const description = within(employment).getByLabelText("description");
  fireEvent.change(description, { target: { value: "Submitted description" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled();

  expect(within(employment).getByLabelText("description")).toBeDisabled();
  expect(screen.getByLabelText("credential type")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Add Employment" })).toBeDisabled();
  expect(within(employment).getByRole("button", { name: "Remove" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Exclude" })).toBeDisabled();
  fireEvent.change(within(employment).getByLabelText("description"), { target: { value: "Later edit that must not stick" } });
  fireEvent.change(screen.getByLabelText("credential type"), { target: { value: "formal_training" } });
  expect(within(employment).getByLabelText("description")).toHaveValue("Submitted description");
  expect(screen.getByLabelText("credential type")).toHaveValue("certification");
  const patches = request.mock.calls.filter(([path, init]) => path === "/api/v1/cv-ingestion/draft-locked" && init?.method === "PATCH");
  expect(patches).toHaveLength(1);
  expect(JSON.parse(String(patches[0][1]?.body)).employment[0].description).toBe("Submitted description");

  await act(async () => { rejectSave(new Error("offline")); });
  expect(await screen.findByRole("alert")).toHaveTextContent("CV review changes could not be saved.");
  expect(screen.getByText(/unsaved review changes/i)).toBeInTheDocument();
  expect(within(employment).getByLabelText("description")).toHaveValue("Submitted description");
  expect(within(employment).getByLabelText("description")).toBeEnabled();
  expect(screen.getByLabelText("credential type")).toBeEnabled();
  expect(screen.getByRole("button", { name: "Add Employment" })).toBeEnabled();
  expect(within(employment).getByRole("button", { name: "Remove" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Exclude" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

it("keeps a failed CV save dirty, accessible, and explicitly retryable", async () => {
  const draft = draftWith("draft-retry", "review_ready", mergedEmpty);
  request
    .mockResolvedValueOnce(statusWith("draft-retry", "review_ready"))
    .mockResolvedValueOnce(draft)
    .mockResolvedValueOnce(emptyOverlap("draft-retry"))
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce({ ...draft, merged: mergedEmpty })
    .mockResolvedValueOnce(emptyOverlap("draft-retry"));
  render(<CvPage />);
  await screen.findByRole("heading", { name: "Review your CV" });
  fireEvent.click(screen.getByRole("button", { name: "Add Credential" }));
  fireEvent.change(screen.getByLabelText("name"), { target: { value: "Retryable credential" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("CV review changes could not be saved.");
  expect(screen.getByText(/unsaved review changes/i)).toBeInTheDocument();
  expect(screen.queryByText("CV changes saved.")).not.toBeInTheDocument();
  expect(screen.getByLabelText("name")).toHaveValue("Retryable credential");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();

  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("CV changes saved.")).toBeInTheDocument();
  expect(screen.queryByText(/unsaved review changes/i)).not.toBeInTheDocument();
  expect(request.mock.calls.filter(([path, init]) => path === "/api/v1/cv-ingestion/draft-retry" && init?.method === "PATCH")).toHaveLength(2);
});


it("removes an ordinary structured item and saves the corrected review", async () => {
  const merged = {
    ...mergedEmpty,
    employment: [{ employer: "Example", title: "Engineer", start_date: "", end_date: "", location: "", description: "Built systems" }],
  };
  const draft = draftWith("draft-remove", "review_ready", merged);
  request
    .mockResolvedValueOnce(statusWith("draft-remove", "review_ready"))
    .mockResolvedValueOnce(draft)
    .mockResolvedValueOnce(emptyOverlap("draft-remove"))
    .mockResolvedValueOnce({ ...draft, merged: mergedEmpty })
    .mockResolvedValueOnce(emptyOverlap("draft-remove"));
  render(<CvPage />);
  await screen.findByRole("heading", { name: "Review your CV" });
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await vi.waitFor(() => {
    const patch = request.mock.calls.find(([path, init]) => path === "/api/v1/cv-ingestion/draft-remove" && init?.method === "PATCH");
    expect(patch).toBeTruthy();
    expect(JSON.parse(String(patch?.[1]?.body)).employment).toEqual([]);
  });
});

it("adds credentials with a valid constrained credential type before save", async () => {
  const draft = draftWith("draft-3", "review_ready", mergedEmpty);
  request
    .mockResolvedValueOnce(statusWith("draft-3", "review_ready"))
    .mockResolvedValueOnce(draft)
    .mockResolvedValueOnce(emptyOverlap("draft-3"))
    .mockResolvedValueOnce({ ...draft, merged: { ...mergedEmpty, credentials: [{ name: "Synthetic Cert", credential_type: "certification", issuer: "", issued_date: "", expiry_date: "", status: "", description: "" }] } })
    .mockResolvedValueOnce(emptyOverlap("draft-3"));
  render(<CvPage />);
  await screen.findByRole("heading", { name: "Review your CV" });

  fireEvent.click(screen.getByRole("button", { name: "Add Credential" }));
  const type = screen.getByLabelText("credential type");
  expect(type).toHaveValue("certification");
  fireEvent.change(screen.getByLabelText("name"), { target: { value: "Synthetic Cert" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  await vi.waitFor(() => {
    const patch = request.mock.calls.find(([path, init]) => path === "/api/v1/cv-ingestion/draft-3" && init?.method === "PATCH");
    expect(patch).toBeTruthy();
    const body = JSON.parse(String(patch?.[1]?.body));
    expect(body.credentials[0]).toMatchObject({ name: "Synthetic Cert", credential_type: "certification" });
  });
});

it("recovers a stale draft 404 exactly once through fresh onboarding status", async () => {
  request
    .mockResolvedValueOnce(statusWith("old", "review_ready"))
    .mockRejectedValueOnce(new ApiError(404, "missing"))
    .mockResolvedValueOnce(statusWith("new", "uploaded"))
    .mockResolvedValueOnce(draftWith("new", "uploaded"));
  render(<CvPage />);
  expect(await screen.findByRole("button", { name: "Interpret CV" })).toBeEnabled();
  expect(request).toHaveBeenCalledTimes(4);
  expect(request.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/onboarding/status",
    "/api/v1/cv-ingestion/old",
    "/api/v1/onboarding/status",
    "/api/v1/cv-ingestion/new",
  ]);
});

it("prevents duplicate interpret requests while the action is pending", async () => {
  let finish!: (value: unknown) => void;
  request
    .mockResolvedValueOnce(statusWith("draft-p", "uploaded"))
    .mockResolvedValueOnce(draftWith("draft-p", "uploaded"))
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(<CvPage />);
  const button = await screen.findByRole("button", { name: "Interpret CV" });
  fireEvent.click(button);
  expect(await screen.findByRole("button", { name: "Interpreting…" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Interpreting CV…");
  fireEvent.click(button);
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/cv-ingestion/draft-p/interpret")).toHaveLength(1);
  finish(draftWith("draft-p", "review_ready", mergedEmpty));
});

it("keeps delayed historical source reads separate from the current latest draft", async () => {
  const current = draftWith("draft-current", "uploaded");
  const history = { limit: 20, truncated: false, items: [
    { id: "draft-old", state: "confirmed", created_at: "2025-01-01T00:00:00Z", updated_at: "2025-01-01T00:00:00Z", filenames: ["old.md"], document_count: 1 },
    { id: "draft-current", state: "uploaded", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", filenames: ["current.md"], document_count: 1 },
  ] };
  let finishOld!: (value: unknown) => void;
  request
    .mockResolvedValueOnce(statusWith("draft-current", "uploaded"))
    .mockResolvedValueOnce(current)
    .mockResolvedValueOnce(history)
    .mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }))
    .mockResolvedValueOnce({ ...current, documents: [{ provenance: { filename: "current.md", media_type: "text/markdown", document_sha256: "current-hash", segment_ids: ["current:1"] }, segments: [{ segment_id: "current:1", text: "CURRENT SOURCE TEXT", page_number: 1, heading: "Current" }] }] });
  render(<CvPage />);
  expect(await screen.findByRole("button", { name: "Interpret CV" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "View CV history" }));
  expect(screen.getByText(/Historical source records are read-only/)).toBeInTheDocument();
  expect(screen.getByText(/Original files are not stored/)).toBeInTheDocument();
  fireEvent.click((await screen.findAllByRole("button", { name: "View source" }))[0]);
  const oldCall = request.mock.calls.findIndex(([path]) => path === "/api/v1/cv-ingestion/draft-old");
  expect(oldCall).toBeGreaterThan(-1);
  fireEvent.click(screen.getAllByRole("button", { name: "View source" })[1]);
  expect(await screen.findByText("CURRENT SOURCE TEXT")).toBeInTheDocument();
  await act(async () => { finishOld({ ...current, id: "draft-old", documents: [{ provenance: { filename: "old.md", media_type: "text/markdown", document_sha256: "old-hash", segment_ids: ["old:1"] }, segments: [{ segment_id: "old:1", text: "OLD SOURCE TEXT", page_number: 1, heading: "Old" }] }] }); });
  expect(screen.getByText("CURRENT SOURCE TEXT")).toBeInTheDocument();
  expect(screen.queryByText("OLD SOURCE TEXT")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Interpret CV" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/cv-ingestion/draft-current/interpret", { method: "POST" }));
});

it("requires explicit replacement confirmation before replacing an existing profile", async () => {
  const review = draftWith("draft-replacement", "review_ready", mergedEmpty);
  request
    .mockResolvedValueOnce(statusWith("draft-replacement", "review_ready", true))
    .mockResolvedValueOnce(review)
    .mockResolvedValueOnce(emptyOverlap("draft-replacement"))
    .mockResolvedValueOnce({ draft_id: review.id, confirmed_evidence_count: 0 })
    .mockResolvedValueOnce(statusWith("draft-replacement", "confirmed", true))
    .mockResolvedValueOnce({ ...review, state: "confirmed" });
  render(<CvPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Confirm reviewed CV" }));
  expect(screen.getByRole("heading", { name: "Replace current structured career information?" })).toBeInTheDocument();
  expect(screen.getByText(/This may replace manual changes previously confirmed in that structured career information\./)).toBeInTheDocument();
  expect(screen.getByText(/Saved Profile details, preferences, eligibility and Career Adviser information are unchanged\./)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /Replace your current career profile/i })).not.toBeInTheDocument();
  expect(request.mock.calls.some(([path]) => String(path).includes("/confirm"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(request.mock.calls.some(([path]) => String(path).includes("/confirm"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Confirm reviewed CV" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm and replace structured career information" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/cv-ingestion/draft-replacement/confirm", { method: "POST" }));
  expect(await screen.findByRole("heading", { name: "CV confirmed" })).toBeInTheDocument();
  expect(screen.getByText(/retained as a historical source and review record/)).toBeInTheDocument();
  expect(screen.getByText(/current structured career information may differ if you later confirmed manual changes or a newer CV/)).toBeInTheDocument();
  expect(screen.queryByText(/structured information now feeds your current career profile/i)).not.toBeInTheDocument();
});

it("refreshes an already-open history panel after a successful upload", async () => {
  const oldDraft = draftWith("draft-old", "uploaded");
  const newDraft = draftWith("draft-new", "uploaded");
  const historyRow = (id: string, filename: string, state: "uploaded" | "review_ready" | "confirmed") => ({
    id, state, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", filenames: [filename], document_count: 1,
  });
  request
    .mockResolvedValueOnce(statusWith("draft-old", "uploaded"))
    .mockResolvedValueOnce(oldDraft)
    .mockResolvedValueOnce({ items: [historyRow("draft-old", "old.md", "uploaded")], limit: 20, truncated: false })
    .mockResolvedValueOnce(newDraft)
    .mockResolvedValueOnce(statusWith("draft-new", "uploaded"))
    .mockResolvedValueOnce(newDraft)
    .mockResolvedValueOnce({ items: [historyRow("draft-new", "new.md", "uploaded"), historyRow("draft-old", "old.md", "uploaded")], limit: 20, truncated: false });
  render(<CvPage />);
  expect(await screen.findByRole("button", { name: "Interpret CV" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "View CV history" }));
  expect(await screen.findByText("old.md")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Start new CV upload" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue with new upload" }));
  fireEvent.change(screen.getByLabelText("CV files"), { target: { files: [new File(["new"], "new.md", { type: "text/markdown" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload CV" }));
  await vi.waitFor(() => expect(request.mock.calls.filter(([path]) => path === "/api/v1/cv-ingestion?limit=20")).toHaveLength(2));
  expect(await screen.findByText("new.md")).toBeInTheDocument();
  expect(screen.getByText(/uploaded · Latest draft/)).toBeInTheDocument();
  expect(screen.queryByText("Loading CV history…")).not.toBeInTheDocument();
});

it("ignores stale history responses after a workflow transition and keeps the newest result", async () => {
  const uploadedDraft = draftWith("draft-race", "uploaded");
  const reviewDraft = draftWith("draft-race", "review_ready", mergedEmpty);
  const historyRow = (filename: string, state: "uploaded" | "review_ready") => ({
    id: "draft-race", state, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", filenames: [filename], document_count: 1,
  });
  let finishOldHistory!: (value: unknown) => void;
  request
    .mockResolvedValueOnce(statusWith("draft-race", "uploaded"))
    .mockResolvedValueOnce(uploadedDraft)
    .mockImplementationOnce(() => new Promise((resolve) => { finishOldHistory = resolve; }))
    .mockResolvedValueOnce(reviewDraft)
    .mockResolvedValueOnce(statusWith("draft-race", "review_ready"))
    .mockResolvedValueOnce(reviewDraft)
    .mockResolvedValueOnce(emptyOverlap("draft-race"))
    .mockResolvedValueOnce({ items: [historyRow("new-history.md", "review_ready")], limit: 20, truncated: false });
  render(<CvPage />);
  expect(await screen.findByRole("button", { name: "Interpret CV" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "View CV history" }));
  expect(await screen.findByText("Loading CV history…")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Interpret CV" }));
  expect(await screen.findByRole("heading", { name: "Review your CV" })).toBeInTheDocument();
  expect(await screen.findByText("new-history.md")).toBeInTheDocument();
  await act(async () => {
    finishOldHistory({ items: [historyRow("old-history.md", "uploaded")], limit: 20, truncated: false });
  });
  expect(screen.getByText("new-history.md")).toBeInTheDocument();
  expect(screen.queryByText("old-history.md")).not.toBeInTheDocument();
  expect(screen.getByText(/review_ready · Latest draft/)).toBeInTheDocument();
  expect(screen.queryByText("Loading CV history…")).not.toBeInTheDocument();
});

const structured = { employer: "Northwind", title: "Engineer", start_date: "2020", end_date: null, location: "Remote", description: "Built resilient services." };
const oneRefinement = (overrides: Record<string, unknown> = {}) => ({
  ...emptyOverlap("overlap-draft"), revision: 3,
  items: [{ item_key: "c".repeat(64), section: "employment", incoming_item: { ...structured, description: "Built resilient and secure services." }, incoming_fingerprint: "d".repeat(64), relationship: "refinement", candidate_matches: [{ fingerprint: "e".repeat(64), item: structured }], target_fingerprint: "e".repeat(64), current_item: structured, saved_resolution: null, resolution_required: true }],
  ...overrides,
});

it("loads saved CV overlap, offers Current versus Reviewed CV decisions, and sends exact target fingerprints", async () => {
  const draft = draftWith("overlap-draft", "review_ready", { ...mergedEmpty, employment: [structured] });
  let patchBody: Record<string, unknown> | null = null;
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/onboarding/status") return statusWith("overlap-draft", "review_ready");
    if (path === "/api/v1/cv-ingestion/overlap-draft") return draft;
    if (path === "/api/v1/cv-ingestion/overlap-draft/overlap-review" && !options) return oneRefinement();
    if (path === "/api/v1/cv-ingestion/overlap-draft/overlap-review" && options?.method === "PATCH") {
      patchBody = JSON.parse(String(options.body));
      return oneRefinement({ revision: 4, items: oneRefinement().items.map((item) => ({ ...item, saved_resolution: { item_key: item.item_key, action: "replace_current", target_fingerprint: "e".repeat(64) }, resolution_required: false })) });
    }
    return {};
  });
  render(<CvPage />);
  expect(await screen.findByText("More detailed version")).toBeInTheDocument();
  expect(screen.getByText("Current Profile")).toBeInTheDocument();
  expect(screen.getByText("Reviewed CV")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Use CV version" }));
  await screen.findByText("Choice saved: replace current.");
  expect(patchBody).toMatchObject({ expected_review_revision: 3, expected_base_structured_fingerprint: "a".repeat(64), expected_draft_fingerprint: "b".repeat(64), resolutions: [{ item_key: "c".repeat(64), action: "replace_current", target_fingerprint: "e".repeat(64) }] });
  expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeEnabled();
});

it("hides saved overlap as out-of-date while CV edits are dirty and reloads after saving", async () => {
  const draft = draftWith("dirty-overlap", "review_ready", mergedEmpty);
  let comparisons = 0;
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/onboarding/status") return statusWith("dirty-overlap", "review_ready");
    if (path === "/api/v1/cv-ingestion/dirty-overlap" && !options) return draft;
    if (path === "/api/v1/cv-ingestion/dirty-overlap/overlap-review") { comparisons += 1; return emptyOverlap("dirty-overlap"); }
    if (path === "/api/v1/cv-ingestion/dirty-overlap" && options?.method === "PATCH") return draft;
    return {};
  });
  render(<CvPage />);
  await screen.findByRole("heading", { name: "Review your CV" });
  await vi.waitFor(() => expect(comparisons).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "Add Credential" }));
  expect(screen.getByText("Save your CV edits before reviewing how they overlap your current Profile.")).toBeInTheDocument();
  expect(screen.queryByText("New information — this CV item will be included when the CV is confirmed.")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await vi.waitFor(() => expect(comparisons).toBe(2));
  expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeEnabled();
});

it("blocks CV confirmation for stale comparisons and duplicate incoming items", async () => {
  const draft = draftWith("blocked-overlap", "review_ready", mergedEmpty);
  const stale = oneRefinement({ stale: true, incoming_duplicates: [{ section: "employment", first_item_key: "1", duplicate_item_key: "2", first_item: structured, duplicate_item: structured }] });
  request.mockImplementation(async (path) => path === "/api/v1/onboarding/status" ? statusWith("blocked-overlap", "review_ready") : path === "/api/v1/cv-ingestion/blocked-overlap" ? draft : path === "/api/v1/cv-ingestion/blocked-overlap/overlap-review" ? stale : {});
  render(<CvPage />);
  await screen.findByText(/same career fact more than once/);
  expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeDisabled();
  expect(screen.getAllByRole("alert").some((node) => node.textContent?.includes("older Profile or CV version"))).toBe(true);
});

it.each([["new", "New information"], ["reinforcement", "Same fact"]] as const)("shows %s CV item as informational and requires no overlap choice", async (relation, label) => {
  const draft = draftWith("automatic-overlap", "review_ready", mergedEmpty);
  const automatic = { ...emptyOverlap("automatic-overlap"), items: [{ item_key: "n".repeat(64), section: "skills", incoming_item: { name: "Rust", category: "language" }, incoming_fingerprint: "i".repeat(64), relationship: relation, candidate_matches: [], target_fingerprint: null, current_item: null, saved_resolution: null, resolution_required: false }] };
  request.mockImplementation(async (path) => path === "/api/v1/onboarding/status" ? statusWith("automatic-overlap", "review_ready") : path === "/api/v1/cv-ingestion/automatic-overlap" ? draft : path === "/api/v1/cv-ingestion/automatic-overlap/overlap-review" ? automatic : {});
  render(<CvPage />);
  expect(await screen.findByText(label)).toBeInTheDocument();
  if (relation === "new") expect(screen.getByText(/will be included when the CV is confirmed/)).toBeInTheDocument();
  else expect(screen.getByText(/confirmation will result in one current item/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /Use CV version|Keep current version/ })).not.toBeInTheDocument();
});

it("shows all ambiguous CV matches, disables duplicate fingerprints, and can resolve another exact candidate", async () => {
  const draft = draftWith("ambiguous-overlap", "review_ready", mergedEmpty);
  const incoming = { name: "Rust", category: "systems" };
  const ambiguous = { ...emptyOverlap("ambiguous-overlap"), items: [{ item_key: "x".repeat(64), section: "skills", incoming_item: incoming, incoming_fingerprint: "y".repeat(64), relationship: "ambiguous", candidate_matches: [
    { fingerprint: "z".repeat(64), item: { name: "Rust", category: "language" } },
    { fingerprint: "z".repeat(64), item: { name: "Rust", category: "platform" } },
    { fingerprint: "w".repeat(64), item: { name: "Rust", category: "runtime" } },
  ], target_fingerprint: null, current_item: null, saved_resolution: null, resolution_required: true }] };
  let body = "";
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/onboarding/status") return statusWith("ambiguous-overlap", "review_ready");
    if (path === "/api/v1/cv-ingestion/ambiguous-overlap" && !options) return draft;
    if (path === "/api/v1/cv-ingestion/ambiguous-overlap/overlap-review" && !options) return ambiguous;
    if (path === "/api/v1/cv-ingestion/ambiguous-overlap/overlap-review" && options?.method === "PATCH") { body = String(options.body); return { ...ambiguous, revision: 1, items: [{ ...ambiguous.items[0], saved_resolution: JSON.parse(body).resolutions[0], resolution_required: false }] }; }
    return {};
  });
  render(<CvPage />);
  expect(await screen.findByText("Current item 3")).toBeInTheDocument();
  expect(screen.getAllByText(/cannot be uniquely targeted yet/)).toHaveLength(2);
  expect(screen.getAllByRole("button", { name: "Replace Rust" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Replace Rust" }));
  await screen.findByText(/Choice saved: replace current/);
  expect(body).toContain('"action":"replace_current"');
  expect(body).toContain('"target_fingerprint":"' + "w".repeat(64) + '"');
  expect(screen.queryByText("w".repeat(64))).not.toBeInTheDocument();
});

it("turns stale CV choice conflicts into refreshable comparison state without losing the draft", async () => {
  const draft = draftWith("stale-choice", "review_ready", mergedEmpty);
  const first = oneRefinement({ draft_id: "stale-choice" });
  let gets = 0;
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/onboarding/status") return statusWith("stale-choice", "review_ready");
    if (path === "/api/v1/cv-ingestion/stale-choice" && !options) return draft;
    if (path === "/api/v1/cv-ingestion/stale-choice/overlap-review" && !options) { gets += 1; return gets === 1 ? first : emptyOverlap("stale-choice"); }
    if (path === "/api/v1/cv-ingestion/stale-choice/overlap-review" && options?.method === "PATCH") throw new ApiError(409, "stale");
    return {};
  });
  render(<CvPage />);
  await screen.findByText("More detailed version");
  fireEvent.click(screen.getByRole("button", { name: "Use CV version" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/changed after this comparison was loaded/);
  expect(screen.getByRole("heading", { name: "Review your CV" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh comparison" }));
  await vi.waitFor(() => { expect(gets).toBe(2); expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeEnabled(); });
});

it("ignores the delayed pre-save CV comparison after saved-draft analysis returns", async () => {
  const draft = draftWith("cv-analysis-race", "review_ready", mergedEmpty);
  let finishOld!: (value: unknown) => void;
  let comparisons = 0;
  const old = oneRefinement({ draft_id: "cv-analysis-race" });
  const fresh = { ...emptyOverlap("cv-analysis-race"), revision: 2, items: [{ item_key: "fresh-key", section: "skills", incoming_item: { name: "Rust", category: "language" }, incoming_fingerprint: "hidden", relationship: "new", candidate_matches: [], target_fingerprint: null, current_item: null, saved_resolution: null, resolution_required: false }] };
  request.mockImplementation(async (path, options) => {
    if (path === "/api/v1/onboarding/status") return statusWith("cv-analysis-race", "review_ready");
    if (path === "/api/v1/cv-ingestion/cv-analysis-race" && !options) return draft;
    if (path === "/api/v1/cv-ingestion/cv-analysis-race" && options?.method === "PATCH") return draft;
    if (path === "/api/v1/cv-ingestion/cv-analysis-race/overlap-review" && !options) { comparisons += 1; return comparisons === 1 ? new Promise((resolve) => { finishOld = resolve; }) : fresh; }
    return {};
  });
  render(<CvPage />);
  await screen.findByRole("heading", { name: "Review your CV" });
  fireEvent.click(screen.getByRole("button", { name: "Add Credential" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByText("New information")).toBeInTheDocument();
  await act(async () => finishOld(old));
  expect(screen.getByText(/New information — this CV item will be included/)).toBeInTheDocument();
  expect(screen.queryByText("More detailed version")).not.toBeInTheDocument();
});

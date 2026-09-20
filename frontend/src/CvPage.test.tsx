import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "./auth";
import { CvPage } from "./CvPage";

const request = vi.fn();
vi.mock("./auth", () => ({
  ApiError: class ApiError extends Error { constructor(public status: number, message = "") { super(message); } },
  useAuth: () => ({ api: { request } }),
}));
afterEach(() => { cleanup(); request.mockReset(); });

const noDraft = { profile_exists: true, candidate_context_ready: false, latest_cv_draft: null, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 0 } };
const mergedEmpty = { employment: [], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [] };
const statusWith = (id: string, state: "uploaded" | "review_ready" | "confirmed", candidate_context_ready = false) => ({
  ...noDraft,
  candidate_context_ready,
  latest_cv_draft: { id, state, created_at: "", updated_at: "" },
});
const draftWith = (id: string, state: "uploaded" | "review_ready" | "confirmed", merged: typeof mergedEmpty | null = null) => ({
  id, state, documents: [{ provenance: { filename: "cv.md" } }], merged, created_at: "", updated_at: "",
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
  fireEvent.click(screen.getByRole("button", { name: "Update CV / Upload newer CV" }));
  expect(screen.getByRole("heading", { name: "Upload your CV" })).toBeInTheDocument();
  expect(screen.getByText(/currently confirmed candidate profile remains active/)).toBeInTheDocument();
});

it("keeps semantic evidence read-only and blocks confirmation until exclusions are saved", async () => {
  const merged = { ...mergedEmpty, evidence: [{ evidence_type: "project", title: "Claim", text: "Source-supported claim", skills: ["Python"], provenance: [{ source_kind: "cv" as const, document_sha256: "hash", segment_ids: ["hash:1"] }] }] };
  const draft = draftWith("draft-2", "review_ready", merged);
  request
    .mockResolvedValueOnce(statusWith("draft-2", "review_ready", true))
    .mockResolvedValueOnce(draft)
    .mockResolvedValueOnce({ ...draft, merged: { ...merged, evidence: [] } });
  render(<CvPage />);
  expect(await screen.findByText(/From uploaded CV/)).toBeInTheDocument();
  expect(screen.queryByDisplayValue("Source-supported claim")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Exclude" }));
  expect(screen.getByText(/unsaved review changes/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/cv-ingestion/draft-2", expect.objectContaining({ method: "PATCH" })));
  expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeEnabled();
  expect(request.mock.calls.some(([path]) => String(path).includes("/confirm"))).toBe(false);
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
    .mockResolvedValueOnce({ ...draft, merged: mergedEmpty });
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
    .mockResolvedValueOnce({ ...draft, merged: { ...mergedEmpty, credentials: [{ name: "Synthetic Cert", credential_type: "certification", issuer: "", issued_date: "", expiry_date: "", status: "", description: "" }] } });
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
  fireEvent.click(button);
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/cv-ingestion/draft-p/interpret")).toHaveLength(1);
  finish(draftWith("draft-p", "review_ready", mergedEmpty));
});

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CvPage } from "./CvPage";

const request = vi.fn();
vi.mock("./auth", () => ({
  ApiError: class ApiError extends Error { constructor(public status: number) { super(); } },
  useAuth: () => ({ api: { request } }),
}));
afterEach(() => { cleanup(); request.mockReset(); });

const noDraft = { profile_exists: true, candidate_context_ready: false, latest_cv_draft: null, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 0 } };

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

it("resumes uploaded drafts with explicit interpret rather than auto-confirming", async () => {
  const draft = { id: "draft-1", state: "uploaded", documents: [{ provenance: { filename: "cv.md" } }], merged: null, created_at: "", updated_at: "" };
  request.mockResolvedValueOnce({ ...noDraft, latest_cv_draft: { id: "draft-1", state: "uploaded", created_at: "", updated_at: "" } }).mockResolvedValueOnce(draft);
  render(<CvPage />);
  expect(await screen.findByRole("button", { name: "Interpret CV" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Start new CV upload" })).toBeEnabled();
  expect(request).toHaveBeenCalledTimes(2);
  expect(request.mock.calls.some(([path]) => String(path).includes("/confirm"))).toBe(false);
});

it("renders review evidence read-only with exclusion and a deliberate confirmation", async () => {
  const merged = { employment: [], education: [], credentials: [], skills: [], projects: [], achievements: [], evidence: [{ evidence_type: "project", title: "Claim", text: "Source-supported claim", skills: ["Python"], provenance: [{ source_kind: "cv", document_sha256: "hash", segment_ids: ["hash:1"] }] }] };
  const draft = { id: "draft-2", state: "review_ready", documents: [], merged, created_at: "", updated_at: "" };
  request.mockResolvedValueOnce({ ...noDraft, candidate_context_ready: true, latest_cv_draft: { id: "draft-2", state: "review_ready", created_at: "", updated_at: "" } }).mockResolvedValueOnce(draft).mockResolvedValueOnce({ ...draft, merged: { ...merged, evidence: [] } });
  render(<CvPage />);
  expect(await screen.findByText(/From uploaded CV/)).toBeInTheDocument();
  expect(screen.queryByDisplayValue("Source-supported claim")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Exclude" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/cv-ingestion/draft-2", expect.objectContaining({ method: "PATCH" })));
  expect(screen.getByRole("button", { name: "Confirm reviewed CV" })).toBeInTheDocument();
});

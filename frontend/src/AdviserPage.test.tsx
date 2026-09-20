import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AdviserPage } from "./AdviserPage";

const request = vi.fn();
vi.mock("./auth", () => ({ ApiError: class ApiError extends Error { constructor(public status: number) { super(); } }, useAuth: () => ({ api: { request } }) }));
afterEach(() => { cleanup(); request.mockReset(); });
const status = (ready = true) => ({ profile_exists: true, candidate_context_ready: ready, latest_cv_draft: null, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 2 } });
const intake = { career_direction: "Direction", work_preferences: [], constraints: [], self_assessment: [], motivations: [], tradeoffs: [], eligibility: { work_authorisation: [], security_clearances: [], locations: [] }, updated_at: "2026-01-01" };

it("gates direct Adviser access before confirmed candidate context without Adviser calls", async () => {
  request.mockResolvedValueOnce(status(false)); render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Complete your CV first" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to CV onboarding" })).toHaveAttribute("href", "/cv");
  expect(request).toHaveBeenCalledTimes(1);
});

it("treats authoritative intake 404 as first-time editable intake and strips updated_at on save", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, "")).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); expect(await screen.findByRole("button", { name: "Save intake" })).toBeEnabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Career direction" }), { target: { value: "New direction" } });
  expect(screen.getByText("Unsaved intake changes")).toBeInTheDocument();
  request.mockResolvedValueOnce(intake).mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Save intake" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/candidate-adviser/intake", expect.objectContaining({ method: "PUT" })));
  const [, options] = request.mock.calls.find((call) => call[0] === "/api/v1/candidate-adviser/intake" && (call[1] as { method?: string } | undefined)?.method === "PUT") ?? [];
  expect(String((options as { body: string }).body)).not.toContain("updated_at");
});

it("loads an existing intake before editing and preserves untouched values on PUT", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const existing = { ...intake, work_preferences: ["Hybrid"], constraints: ["UK only"] };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(existing).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const direction = await screen.findByRole("textbox", { name: "Career direction" });
  expect(direction).toHaveValue("Direction");
  expect(screen.getByRole("textbox", { name: "work preferences 1" })).toHaveValue("Hybrid");
  fireEvent.change(direction, { target: { value: "Edited direction" } });
  request.mockResolvedValueOnce({ ...existing, career_direction: "Edited direction" }).mockResolvedValueOnce(status()).mockResolvedValueOnce({ ...existing, career_direction: "Edited direction" }).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Save intake" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/candidate-adviser/intake", expect.objectContaining({ method: "PUT" })));
  const [, options] = request.mock.calls.find((call) => call[0] === "/api/v1/candidate-adviser/intake" && (call[1] as { method?: string } | undefined)?.method === "PUT") ?? [];
  expect(JSON.parse((options as { body: string }).body)).toEqual({
    career_direction: "Edited direction", work_preferences: ["Hybrid"], constraints: ["UK only"], self_assessment: [], motivations: [], tradeoffs: [],
    eligibility: { work_authorisation: [], security_clearances: [], locations: [] },
  });
});

it("does not render a blank authoritative intake when intake retrieval is unavailable", async () => {
  request.mockResolvedValueOnce(status()).mockRejectedValueOnce(new Error("offline"));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByText("Adviser intake is unavailable.")).toBeInTheDocument();
  expect(screen.getByText("Loading Adviser intake…")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Career direction" })).not.toBeInTheDocument();
});

it("renders confirmed assessment empty clarification state and persistent count", async () => {
  const assessment = { status: "confirmed", content: { professional_positioning: { text: "Position", source_references: [{ source_type: "intake", reference: "career_direction" }] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Transition", source_references: [{ source_type: "intake", reference: "career_direction" }] }, open_questions: [], career_strategy_summary: { text: "Strategy", source_references: [{ source_type: "intake", reference: "career_direction" }] }, job_search_strategy_summary: { text: "Search", source_references: [{ source_type: "intake", reference: "career_direction" }] } } };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment).mockResolvedValueOnce([]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByText("No current clarification questions. Adviser enrichment is current.")).toBeInTheDocument();
  expect(screen.getByText("Confirmed clarifications: 2")).toBeInTheDocument();
});

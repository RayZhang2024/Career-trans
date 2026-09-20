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

it("requires first intake Save before Generate and retains PUT authority without another intake GET", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, "")).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const generate = await screen.findByRole("button", { name: "Generate assessment" });
  expect(generate).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Career direction" }), { target: { value: "First saved direction" } });
  request.mockResolvedValueOnce({ ...intake, career_direction: "First saved direction" }).mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Save intake" }));
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "Generate assessment" })).toBeEnabled());
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/intake" && !call[1]).length).toBe(1);
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

it("keeps an authoritative recovery notice after an assessment conflict refresh", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Generate assessment" });
  request.mockRejectedValueOnce(new ApiError(409, "")).mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Generate assessment" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Adviser state changed. The current state has been refreshed.");
  expect(screen.getByRole("button", { name: "Generate assessment" })).toBeEnabled();
});

it("renders confirmed assessment empty clarification state and persistent count", async () => {
  const assessment = { status: "confirmed", content: { professional_positioning: { text: "Position", source_references: [{ source_type: "intake", reference: "career_direction" }] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Transition", source_references: [{ source_type: "intake", reference: "career_direction" }] }, open_questions: [], career_strategy_summary: { text: "Strategy", source_references: [{ source_type: "intake", reference: "career_direction" }] }, job_search_strategy_summary: { text: "Search", source_references: [{ source_type: "intake", reference: "career_direction" }] } } };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment).mockResolvedValueOnce([]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByText("No current clarification questions. Adviser enrichment is current.")).toBeInTheDocument();
  expect(screen.getByText("Confirmed clarifications: 2")).toBeInTheDocument();
});

it("keeps an assessment retrieval failure unavailable instead of showing Generate", async () => {
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new Error("offline"));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByText("Adviser assessment is unavailable.")).toBeInTheDocument();
  expect(screen.getByText("Loading assessment…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Generate assessment" })).not.toBeInTheDocument();
});

it("restores the deterministic review-ready clarification, normalized answer, and locks siblings", async () => {
  const assessment = { status: "confirmed", content: { professional_positioning: { text: "Position", source_references: [] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Transition", source_references: [] }, open_questions: [], career_strategy_summary: { text: "Strategy", source_references: [] }, job_search_strategy_summary: { text: "Search", source_references: [] } } };
  const second = { clarification_id: "b", question_text: "Later question", priority_index: 2, status: "review_ready", answer_text: "Later answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Later interpretation", proposed_evidence: [] } };
  const first = { clarification_id: "a", question_text: "First question", priority_index: 1, status: "review_ready", answer_text: "  Normalized answer  ", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Current interpretation", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment).mockResolvedValueOnce([second, first]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const answer = await screen.findByRole("textbox", { name: "Clarification answer" });
  expect(answer).toHaveValue("  Normalized answer  ");
  expect(screen.getByText("Current interpretation")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm clarification" })).toBeEnabled();
  expect(screen.getAllByRole("button", { name: "Work on this question" })[1]).toBeDisabled();
  fireEvent.change(answer, { target: { value: "Edited answer" } });
  expect(screen.getByText("Answer changed — re-interpret before confirmation.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm clarification" })).toBeDisabled();
});

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AdviserPage } from "./AdviserPage";

const request = vi.fn();
vi.mock("./auth", () => ({ ApiError: class ApiError extends Error { constructor(public status: number) { super(); } }, useAuth: () => ({ api: { request } }) }));
afterEach(() => { cleanup(); request.mockReset(); });
const status = (ready = true) => ({ profile_exists: true, candidate_context_ready: ready, latest_cv_draft: null, adviser: { intake_exists: false, assessment_status: null, confirmed_clarification_count: 2 } });
const intake = { career_direction: "Direction", work_preferences: [], constraints: [], self_assessment: [], motivations: [], tradeoffs: [], eligibility: { work_authorisation: [], security_clearances: [], locations: [] }, updated_at: "2026-01-01" };
const content = { professional_positioning: { text: "Position", source_references: [{ source_type: "intake", reference: "career_direction" }] }, transferable_strengths: [{ text: "Strength", source_references: [{ source_type: "career_evidence", reference: "safe" }] }], development_gaps: [{ text: "Gap", source_references: [] }], role_hypotheses: [{ text: "Role", source_references: [] }], transition_assessment: { text: "Transition", source_references: [] }, open_questions: [{ text: "Question", source_references: [{ source_type: "clarification", reference: "safe" }] }], career_strategy_summary: { text: "Strategy", source_references: [] }, job_search_strategy_summary: { text: "Search", source_references: [] } };
const assessment = (state: "review_ready" | "confirmed" | "stale") => ({ status: state, content });
const unanswered = (id = "question", priority_index = 0) => ({ clarification_id: id, question_text: `Question ${id}`, priority_index, status: "unanswered" as const, answer_text: null, interpretation: null });

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

it("refreshes assessment independently when onboarding fails after a changed intake Save", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const content = { professional_positioning: { text: "Position", source_references: [] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Transition", source_references: [] }, open_questions: [], career_strategy_summary: { text: "Strategy", source_references: [] }, job_search_strategy_summary: { text: "Search", source_references: [] } };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce({ status: "confirmed", content }).mockResolvedValueOnce([]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByText("Assessment is current.")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Career direction" }), { target: { value: "Changed direction" } });
  request.mockResolvedValueOnce({ ...intake, career_direction: "Changed direction" }).mockRejectedValueOnce(new ApiError(503, "")).mockResolvedValueOnce({ status: "stale", content });
  fireEvent.click(screen.getByRole("button", { name: "Save intake" }));
  expect(await screen.findByRole("button", { name: "Reassess" })).toBeEnabled();
  expect(screen.getByText("Adviser status is unavailable.")).toBeInTheDocument();
  expect(screen.queryByText("Assessment is current.")).not.toBeInTheDocument();
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/clarifications").length).toBe(1);
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

it("keeps a confirmed clarification read-only through dependent refresh until reassessment replaces it", async () => {
  const content = { professional_positioning: { text: "Position", source_references: [] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Transition", source_references: [] }, open_questions: [], career_strategy_summary: { text: "Strategy", source_references: [] }, job_search_strategy_summary: { text: "Search", source_references: [] } };
  const reviewing = { clarification_id: "question", question_text: "What delivery fact?", priority_index: 0, status: "review_ready", answer_text: "Normalized answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Confirmed context", proposed_evidence: [{ title: "Synthetic delivery", text: "Delivered a system.", skills: [] }] } };
  const confirmed = { ...reviewing, status: "confirmed" };
  let resolveStatus!: (value: unknown) => void; let resolveAssessment!: (value: unknown) => void;
  const delayedStatus = new Promise<unknown>((resolve) => { resolveStatus = resolve; });
  const delayedAssessment = new Promise<unknown>((resolve) => { resolveAssessment = resolve; });
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce({ status: "confirmed", content }).mockResolvedValueOnce([reviewing]).mockResolvedValueOnce(confirmed).mockReturnValueOnce(delayedStatus).mockReturnValueOnce(delayedAssessment);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Confirm clarification" });
  fireEvent.click(screen.getByRole("button", { name: "Confirm clarification" }));
  expect(await screen.findByRole("heading", { name: "Clarification confirmed" })).toBeInTheDocument();
  expect(screen.getByText("Normalized answer")).toBeInTheDocument();
  expect(screen.getByText("Confirmed context")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Clarification answer" })).not.toBeInTheDocument();
  resolveStatus(status()); resolveAssessment({ status: "stale", content });
  expect(await screen.findByRole("button", { name: "Reassess" })).toBeEnabled();
  expect(screen.getByRole("heading", { name: "Clarification confirmed" })).toBeInTheDocument();
  request.mockResolvedValueOnce({ status: "review_ready", content });
  fireEvent.click(screen.getByRole("button", { name: "Reassess" }));
  await vi.waitFor(() => expect(screen.queryByRole("heading", { name: "Clarification confirmed" })).not.toBeInTheDocument());
});

it("round-trips every intake list, exposes dirty state, and discards to its authoritative baseline", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Generate assessment" });
  for (const label of ["work preferences", "constraints", "self assessment", "motivations", "tradeoffs", "work authorisation", "security clearances", "locations"]) {
    const group = screen.getByRole("group", { name: label });
    fireEvent.click(within(group).getByRole("button", { name: "Add" }));
    const value = screen.getByRole("textbox", { name: `${label} 1` });
    fireEvent.change(value, { target: { value: `${label} value` } });
    expect(value).toHaveValue(`${label} value`);
    fireEvent.click(within(group).getByRole("button", { name: "Remove" }));
    expect(screen.queryByRole("textbox", { name: `${label} 1` })).not.toBeInTheDocument();
  }
  fireEvent.change(screen.getByRole("textbox", { name: "Career direction" }), { target: { value: "Changed" } });
  expect(screen.getByText("Unsaved intake changes")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Generate assessment" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(screen.getByRole("textbox", { name: "Career direction" })).toHaveValue("Direction");
  expect(screen.queryByText("Unsaved intake changes")).not.toBeInTheDocument();
});

it("drives generate, review, confirm, stale reassessment, and all assessment sections without fetching clarifications early", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const generate = await screen.findByRole("button", { name: "Generate assessment" });
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/clarifications")).toHaveLength(0);
  request.mockResolvedValueOnce(assessment("review_ready")); fireEvent.click(generate);
  await screen.findByText("Assessment ready for your review.");
  for (const title of ["Professional positioning", "Transferable strengths", "Development gaps", "Role hypotheses", "Transition assessment", "Open questions", "Career strategy summary", "Job-search strategy summary"]) expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
  expect(screen.getByText("Based on your adviser intake")).toBeInTheDocument();
  request.mockResolvedValueOnce(assessment("review_ready")); fireEvent.click(screen.getByRole("button", { name: "Regenerate assessment" }));
  await screen.findByText("Assessment ready for your review.");
  request.mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Confirm assessment" }));
  await screen.findByRole("button", { name: "Reassess" });
  request.mockResolvedValueOnce(assessment("review_ready")); fireEvent.click(screen.getByRole("button", { name: "Reassess" }));
  await screen.findByText("Assessment ready for your review.");
});

it("adopts normalized interpretation answers, re-interprets edited answers, and only renders returned evidence", async () => {
  const first = { ...unanswered(), status: "review_ready" as const, answer_text: "Normalized answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Fact context", proposed_evidence: [] } };
  const second = { ...first, answer_text: "Second normalized answer", interpretation: { answer_kind: "mixed", confirmed_context_summary: "Mixed context", proposed_evidence: [{ title: "Returned evidence", text: "Only returned evidence", skills: ["safe"] }] } };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([unanswered()]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Work on this question" });
  fireEvent.click(screen.getByRole("button", { name: "Work on this question" }));
  const editor = await screen.findByRole("textbox", { name: "Clarification answer" });
  fireEvent.change(editor, { target: { value: "  raw answer  " } });
  request.mockResolvedValueOnce(first); fireEvent.click(screen.getByRole("button", { name: "Interpret answer" }));
  await vi.waitFor(() => expect(editor).toHaveValue("Normalized answer"));
  expect(screen.getByText("Fact context")).toBeInTheDocument();
  expect(screen.queryByText("Returned evidence")).not.toBeInTheDocument();
  fireEvent.change(editor, { target: { value: "edited" } });
  expect(screen.getByText("Answer changed — re-interpret before confirmation.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm clarification" })).toBeDisabled();
  request.mockResolvedValueOnce(second); fireEvent.click(screen.getByRole("button", { name: "Re-interpret answer" }));
  await vi.waitFor(() => expect(editor).toHaveValue("Second normalized answer"));
  expect(screen.getByText("Returned evidence: Only returned evidence")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm clarification" })).toBeEnabled();
});

it("keeps provider failures safe and never runs state recovery for failed semantic mutations", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Generate assessment" });
  request.mockRejectedValueOnce(new ApiError(503, "private provider body")); fireEvent.click(screen.getByRole("button", { name: "Generate assessment" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Assessment generation is temporarily unavailable.");
  expect(screen.queryByText("private provider body")).not.toBeInTheDocument();
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/onboarding/status")).toHaveLength(1);
});

it("locks cross-operation controls while Save, assessment, interpretation, and clarification confirmation are pending", async () => {
  const ApiError = (await import("./auth")).ApiError;
  let resolveSave!: (value: unknown) => void;
  const delayedSave = new Promise<unknown>((resolve) => { resolveSave = resolve; });
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Generate assessment" });
  fireEvent.change(screen.getByRole("textbox", { name: "Career direction" }), { target: { value: "Dirty" } });
  request.mockReturnValueOnce(delayedSave).mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, "")); fireEvent.click(screen.getByRole("button", { name: "Save intake" }));
  expect(screen.getByRole("button", { name: "Generate assessment" })).toBeDisabled();
  resolveSave(intake); await vi.waitFor(() => expect(screen.getByRole("button", { name: "Save intake" })).toBeEnabled());
});

it("does not let a confirmed transition survive a 409 recovery whose authoritative assessment moved to review-ready", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const reviewing = { ...unanswered(), status: "review_ready" as const, answer_text: "Answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Context", proposed_evidence: [] } };
  const confirmed = { ...reviewing, status: "confirmed" as const };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]).mockResolvedValueOnce(confirmed).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Confirm clarification" });
  fireEvent.click(screen.getByRole("button", { name: "Confirm clarification" })); await screen.findByRole("button", { name: "Reassess" });
  expect(screen.getByRole("heading", { name: "Clarification confirmed" })).toBeInTheDocument();
  request.mockRejectedValueOnce(new ApiError(409, "")).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("review_ready"));
  fireEvent.click(screen.getByRole("button", { name: "Reassess" }));
  await screen.findByText("Assessment ready for your review.");
  expect(screen.queryByRole("heading", { name: "Clarification confirmed" })).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Adviser state changed. The current state has been refreshed.");
});

it("restores persisted review-ready clarification after unchanged Save, but clears it when the refreshed assessment is stale", async () => {
  const reviewing = { ...unanswered(), status: "review_ready" as const, answer_text: "Persisted", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Persisted context", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("textbox", { name: "Clarification answer" });
  request.mockResolvedValueOnce(intake).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  fireEvent.click(screen.getByRole("button", { name: "Save intake" }));
  expect(await screen.findByRole("textbox", { name: "Clarification answer" })).toHaveValue("Persisted");
  fireEvent.change(screen.getByRole("textbox", { name: "Career direction" }), { target: { value: "Changed" } });
  request.mockResolvedValueOnce({ ...intake, career_direction: "Changed" }).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Save intake" }));
  expect(await screen.findByRole("button", { name: "Reassess" })).toBeEnabled();
  expect(screen.queryByRole("textbox", { name: "Clarification answer" })).not.toBeInTheDocument();
});

it("recovers clarification answer conflicts without retrying the failed mutation", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const item = unanswered();
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([item]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Work on this question" });
  fireEvent.click(screen.getByRole("button", { name: "Work on this question" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Clarification answer" }), { target: { value: "Answer" } });
  request.mockRejectedValueOnce(new ApiError(409, "")).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Interpret answer" }));
  expect(await screen.findByRole("button", { name: "Reassess" })).toBeEnabled();
  expect(screen.queryByRole("textbox", { name: "Clarification answer" })).not.toBeInTheDocument();
  expect(request.mock.calls.filter((call) => String(call[0]).endsWith("/answer"))).toHaveLength(1);
});

it("recovers clarification confirmation conflicts without retrying the failed mutation", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const reviewing = { ...unanswered(), status: "review_ready" as const, answer_text: "Answer", interpretation: { answer_kind: "eligibility_fact", confirmed_context_summary: "Eligibility", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Confirm clarification" });
  request.mockRejectedValueOnce(new ApiError(404, "")).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Confirm clarification" }));
  expect(await screen.findByRole("button", { name: "Reassess" })).toBeEnabled();
  expect(screen.queryByRole("textbox", { name: "Clarification answer" })).not.toBeInTheDocument();
  expect(request.mock.calls.filter((call) => String(call[0]).endsWith("/confirm"))).toHaveLength(1);
});

it("locks Save, confirmation, and sibling selection while an interpretation is in flight", async () => {
  let resolveInterpret!: (value: unknown) => void;
  const delayedInterpret = new Promise<unknown>((resolve) => { resolveInterpret = resolve; });
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([unanswered("a", 0), unanswered("b", 1)]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findAllByRole("button", { name: "Work on this question" });
  fireEvent.click(screen.getAllByRole("button", { name: "Work on this question" })[0]);
  fireEvent.change(screen.getByRole("textbox", { name: "Clarification answer" }), { target: { value: "Answer" } });
  request.mockReturnValueOnce(delayedInterpret); fireEvent.click(screen.getByRole("button", { name: "Interpret answer" }));
  expect(screen.getByRole("button", { name: "Save intake" })).toBeDisabled();
  expect(screen.getAllByRole("button", { name: "Work on this question" })[1]).toBeDisabled();
  expect(screen.getByRole("button", { name: "Confirm clarification" })).toBeDisabled();
  resolveInterpret({ ...unanswered("a", 0), status: "review_ready", answer_text: "Answer", interpretation: { answer_kind: "preference_intent", confirmed_context_summary: "Preference", proposed_evidence: [] } });
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "Confirm clarification" })).toBeEnabled());
});

it("ignores a delayed pre-save clarification list after a changed intake makes the assessment stale", async () => {
  let resolveClarifications!: (value: unknown) => void;
  const delayedClarifications = new Promise<unknown>((resolve) => { resolveClarifications = resolve; });
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockReturnValueOnce(delayedClarifications);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Save intake" });
  fireEvent.change(screen.getByRole("textbox", { name: "Career direction" }), { target: { value: "Changed" } });
  request.mockResolvedValueOnce({ ...intake, career_direction: "Changed" }).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Save intake" }));
  expect(await screen.findByRole("button", { name: "Reassess" })).toBeEnabled();
  resolveClarifications([unanswered("late")]);
  await vi.waitFor(() => expect(screen.queryByRole("textbox", { name: "Clarification answer" })).not.toBeInTheDocument());
  expect(screen.queryByText("Question late")).not.toBeInTheDocument();
});

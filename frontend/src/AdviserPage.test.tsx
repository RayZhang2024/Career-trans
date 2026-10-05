import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { OnboardingStatus } from "./api";
import { AdviserPage } from "./AdviserPage";

const request = vi.fn();
vi.mock("./auth", () => ({ ApiError: class ApiError extends Error { constructor(public status: number) { super(); } }, useAuth: () => ({ api: { request } }) }));
afterEach(() => { cleanup(); request.mockReset(); });
const status = (ready = true, assessmentStatus: "review_ready" | "confirmed" | "stale" | null = null, sessionActive = false): OnboardingStatus => ({ profile_exists: true, candidate_context_ready: ready, latest_cv_draft: null, adviser: { intake_exists: true, assessment_status: assessmentStatus, confirmed_clarification_count: 2, journey: { candidate_context_ready: ready, job_search_ready: ready, intake_exists: true, assessment_status: assessmentStatus, confirmed_guidance_active: assessmentStatus === "confirmed" || sessionActive, clarification_session_active: sessionActive, current_follow_up_available: sessionActive, clarification_interpretation_awaiting_confirmation: sessionActive && assessmentStatus === "review_ready", unresolved_profile_enrichment_count: 0, next_enrichment_clarification_id: null, next_enrichment: null, active_profile_draft: false, next_action: !ready ? "complete_profile" : assessmentStatus === "review_ready" ? "review_assessment" : sessionActive ? "answer_clarification" : assessmentStatus === "confirmed" ? "find_jobs" : assessmentStatus === "stale" ? "update_assessment" : "create_assessment", status_category: !ready ? "setup" : assessmentStatus === "review_ready" || (assessmentStatus === "stale" && sessionActive) ? "review" : assessmentStatus === "stale" ? "update" : assessmentStatus === "confirmed" ? "up_to_date" : "setup", confirmed_clarification_count: 2 } } });
const intake = { career_direction: "Direction", work_preferences: [], constraints: [], self_assessment: [], motivations: [], tradeoffs: [], eligibility: { work_authorisation: [], security_clearances: [], locations: [] }, updated_at: "2026-01-01" };
const content = { professional_positioning: { text: "Position", source_references: [{ source_type: "intake", reference: "career_direction" }] }, transferable_strengths: [{ text: "Strength", source_references: [{ source_type: "career_evidence", reference: "safe" }] }], development_gaps: [{ text: "Gap", source_references: [] }], role_hypotheses: [{ text: "Role", source_references: [] }], transition_assessment: { text: "Transition", source_references: [] }, open_questions: [{ text: "Question", source_references: [{ source_type: "clarification", reference: "safe" }] }], career_strategy_summary: { text: "Strategy", source_references: [] }, job_search_strategy_summary: { text: "Search", source_references: [] } };
const assessment = (state: "review_ready" | "confirmed" | "stale") => ({ status: state, content });
const unanswered = (id = "question", priority_index = 0) => ({ clarification_id: id, question_text: `Question ${id}`, priority_index, status: "unanswered" as const, answer_text: null, session_active: true, interpretation: null });
const optionClarification = (statusValue: "unanswered" | "review_ready" = "unanswered", structured_response: { selected_option_ids: string[]; custom_answer_text: string; special_selection: "not_sure" | null } | null = null) => ({
  ...unanswered(),
  status: statusValue,
  answer_text: structured_response?.special_selection === "not_sure" ? "I'm not sure / I don't have enough information to answer this yet" : null,
  suggested_answers: [
    { option_id: "a".repeat(64), text: "I led the synthetic project" },
    { option_id: "b".repeat(64), text: "I contributed to implementation" },
    { option_id: "c".repeat(64), text: "I supported testing and release" },
  ],
  structured_response,
  interpretation: statusValue === "review_ready" ? { answer_kind: "career_fact" as const, confirmed_context_summary: "Reviewed selected synthetic work.", proposed_evidence: [] } : null,
});

it("gates direct Adviser access before confirmed candidate context without Adviser calls", async () => {
  request.mockResolvedValueOnce(status(false)); render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Set up Career Adviser" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Confirm your CV" })).toHaveAttribute("href", "/profile/cv");
  expect(request).toHaveBeenCalledTimes(1);
});

it("treats authoritative intake 404 as first-time editable intake and strips updated_at on save", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, "")).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); expect(await screen.findByRole("button", { name: "Save career direction" })).toBeEnabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "New direction" } });
  expect(screen.getByText("You have unsaved changes. Save or discard them before continuing to assessment, clarification, or Profile review.")).toBeInTheDocument();
  request.mockResolvedValueOnce(intake).mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/candidate-adviser/intake", expect.objectContaining({ method: "PUT" })));
  const [, options] = request.mock.calls.find((call) => call[0] === "/api/v1/candidate-adviser/intake" && (call[1] as { method?: string } | undefined)?.method === "PUT") ?? [];
  expect(String((options as { body: string }).body)).not.toContain("updated_at");
});

it("shows pending and confirmed feedback for the Adviser intake save", async () => {
  const ApiError = (await import("./auth")).ApiError;
  let finishSave!: (value: unknown) => void;
  let failStatusRefresh!: (error: Error) => void;
  const delayedSave = new Promise<unknown>((resolve) => { finishSave = resolve; });
  const delayedStatusRefresh = new Promise<unknown>((_, reject) => { failStatusRefresh = reject; });
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const direction = await screen.findByRole("textbox", { name: "Where would you like your career to go?" });
  fireEvent.change(direction, { target: { value: "Updated synthetic direction" } });
  request.mockReturnValueOnce(delayedSave).mockReturnValueOnce(delayedStatusRefresh).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(screen.getByText("Saving your career direction…")).toBeInTheDocument();
  expect(screen.queryByText("Adviser intake saved.")).not.toBeInTheDocument();
  await act(async () => { finishSave({ ...intake, career_direction: "Updated synthetic direction" }); });
  expect(await screen.findByText("Adviser intake saved.")).toBeInTheDocument();
  expect(await screen.findByText("Refreshing career context…")).toBeInTheDocument();
  expect(screen.queryByText("Saving adviser intake…")).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Where would you like your career to go?" })).toHaveValue("Updated synthetic direction");
  await act(async () => { failStatusRefresh(new ApiError(503, "")); });
  expect(await screen.findByRole("alert")).toHaveTextContent("Adviser status is unavailable.");
  expect(screen.getByText("Adviser intake saved.")).toBeInTheDocument();
});

it("requires first intake Save before Generate and retains PUT authority without another intake GET", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, "")).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const generate = await screen.findByRole("button", { name: "Tell Career Adviser what you’re looking for" });
  expect(generate).toBeEnabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "First saved direction" } });
  request.mockResolvedValueOnce({ ...intake, career_direction: "First saved direction" }).mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "Create my career assessment" })).toBeEnabled());
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/intake" && !call[1]).length).toBe(1);
});

it("loads an existing intake before editing and preserves untouched values on PUT", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const existing = { ...intake, work_preferences: ["Hybrid"], constraints: ["UK only"] };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(existing).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const direction = await screen.findByRole("textbox", { name: "Where would you like your career to go?" });
  expect(direction).toHaveValue("Direction");
  expect(screen.getByRole("textbox", { name: "What do you want in your next role? 1" })).toHaveValue("Hybrid");
  fireEvent.change(direction, { target: { value: "Edited direction" } });
  request.mockResolvedValueOnce({ ...existing, career_direction: "Edited direction" }).mockResolvedValueOnce(status()).mockResolvedValueOnce({ ...existing, career_direction: "Edited direction" }).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
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
  expect(screen.getByText("Loading your saved career direction…")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Where would you like your career to go?" })).not.toBeInTheDocument();
});

it("keeps an authoritative recovery notice after an assessment conflict refresh", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Create my career assessment" });
  request.mockRejectedValueOnce(new ApiError(409, "")).mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Create my career assessment" }));
  expect(await screen.findByText("Adviser state changed. The current state has been refreshed.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create my career assessment" })).toBeEnabled();
});

it("refreshes assessment independently when onboarding fails after a changed intake Save", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const content = { professional_positioning: { text: "Position", source_references: [] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Transition", source_references: [] }, open_questions: [], career_strategy_summary: { text: "Strategy", source_references: [] }, job_search_strategy_summary: { text: "Search", source_references: [] } };
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce({ status: "confirmed", content }).mockResolvedValueOnce([]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByText(/Career Adviser is up to date\./)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "Changed direction" } });
  request.mockResolvedValueOnce({ ...intake, career_direction: "Changed direction" }).mockRejectedValueOnce(new ApiError(503, "")).mockResolvedValueOnce({ status: "stale", content });
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  expect(await screen.findByRole("button", { name: "Update my assessment" })).toBeEnabled();
  expect(screen.getByText("Adviser status is unavailable.")).toBeInTheDocument();
  expect(screen.queryByText(/Career Adviser is up to date\./)).not.toBeInTheDocument();
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/clarifications").length).toBe(1);
});

it("renders confirmed assessment empty clarification state and persistent count", async () => {
  const assessment = { status: "confirmed", content: { professional_positioning: { text: "Position", source_references: [{ source_type: "intake", reference: "career_direction" }] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Transition", source_references: [{ source_type: "intake", reference: "career_direction" }] }, open_questions: [], career_strategy_summary: { text: "Strategy", source_references: [{ source_type: "intake", reference: "career_direction" }] }, job_search_strategy_summary: { text: "Search", source_references: [{ source_type: "intake", reference: "career_direction" }] } } };
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment).mockResolvedValueOnce([]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByText("No optional follow-up is available right now.")).toBeInTheDocument();

});

it("keeps an assessment retrieval failure unavailable instead of showing Generate", async () => {
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new Error("offline"));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByText("Adviser assessment is unavailable.")).toBeInTheDocument();
  expect(screen.getByText("Loading your career assessment…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create my career assessment" })).not.toBeInTheDocument();
});

it("restores the deterministic review-ready clarification, normalized answer, and locks siblings", async () => {
  const assessment = { status: "confirmed", content: { professional_positioning: { text: "Position", source_references: [] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Transition", source_references: [] }, open_questions: [], career_strategy_summary: { text: "Strategy", source_references: [] }, job_search_strategy_summary: { text: "Search", source_references: [] } } };
  const second = { clarification_id: "b", question_text: "Later question", priority_index: 2, status: "review_ready", answer_text: "Later answer", session_active: true, interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Later interpretation", proposed_evidence: [] } };
  const first = { clarification_id: "a", question_text: "First question", priority_index: 1, status: "review_ready", answer_text: "  Normalized answer  ", session_active: true, interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Current interpretation", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status(true, "confirmed", true)).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment).mockResolvedValueOnce([second, first]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const answer = await screen.findByRole("textbox", { name: "Your answer" });
  expect(answer).toHaveValue("  Normalized answer  ");
  expect(screen.getByText("Current interpretation")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Yes, that’s right" })).toBeEnabled();
  expect(screen.queryByText("Later question")).not.toBeInTheDocument();
  fireEvent.change(answer, { target: { value: "Edited answer" } });
  expect(screen.getByText("You changed your answer. Review it again before confirming.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Yes, that’s right" })).not.toBeInTheDocument();
});

it("keeps a confirmed clarification read-only through dependent refresh until reassessment replaces it", async () => {
  const content = { professional_positioning: { text: "Position", source_references: [] }, transferable_strengths: [], development_gaps: [], role_hypotheses: [], transition_assessment: { text: "Transition", source_references: [] }, open_questions: [], career_strategy_summary: { text: "Strategy", source_references: [] }, job_search_strategy_summary: { text: "Search", source_references: [] } };
  const reviewing = { clarification_id: "question", question_text: "What delivery fact?", priority_index: 0, status: "review_ready", answer_text: "Normalized answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Confirmed context", proposed_evidence: [{ title: "Synthetic delivery", text: "Delivered a system.", skills: [] }] } };
  const confirmed = { ...reviewing, status: "confirmed" };
  let resolveStatus!: (value: unknown) => void; let resolveAssessment!: (value: unknown) => void;
  const delayedStatus = new Promise<unknown>((resolve) => { resolveStatus = resolve; });
  const delayedAssessment = new Promise<unknown>((resolve) => { resolveAssessment = resolve; });
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce({ status: "confirmed", content }).mockResolvedValueOnce([reviewing]).mockResolvedValueOnce(confirmed).mockReturnValueOnce(delayedStatus).mockReturnValueOnce(delayedAssessment);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Yes, that’s right" });
  fireEvent.click(screen.getByRole("button", { name: "Yes, that’s right" }));
  expect(await screen.findByRole("heading", { name: "Your new career information is ready for your review." })).toBeInTheDocument();
  expect(screen.getByText("Normalized answer")).toBeInTheDocument();
  expect(screen.getAllByText("Confirmed context")).toHaveLength(2);
  expect(screen.queryByRole("textbox", { name: "Your answer" })).not.toBeInTheDocument();
  resolveStatus(status()); resolveAssessment({ status: "stale", content });
  expect(await screen.findByRole("button", { name: "Update my assessment" })).toBeEnabled();
  expect(screen.getByRole("heading", { name: "Your new career information is ready for your review." })).toBeInTheDocument();
  request.mockResolvedValueOnce({ status: "review_ready", content });
  fireEvent.click(screen.getByRole("button", { name: "Update my assessment" }));
  await vi.waitFor(() => expect(screen.queryByRole("heading", { name: "Your new career information is ready for your review." })).not.toBeInTheDocument());
});

it("round-trips every intake list, exposes dirty state, and discards to its authoritative baseline", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Create my career assessment" });
  for (const label of ["What do you want in your next role?", "What should we account for?", "What are your strengths or areas to build?", "What motivates you?", "What trade-offs matter most?", "Where can you work?", "Any required clearances?", "Which locations work for you?"]) {
    const group = screen.getByRole("group", { name: label });
    fireEvent.click(within(group).getByRole("button", { name: "Add" }));
    const value = screen.getByRole("textbox", { name: `${label} 1` });
    fireEvent.change(value, { target: { value: `${label} value` } });
    expect(value).toHaveValue(`${label} value`);
    fireEvent.click(within(group).getByRole("button", { name: "Remove" }));
    expect(screen.queryByRole("textbox", { name: `${label} 1` })).not.toBeInTheDocument();
  }
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "Changed" } });
  expect(screen.getByText("You have unsaved changes. Save or discard them before continuing to assessment, clarification, or Profile review.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(screen.getByRole("textbox", { name: "Where would you like your career to go?" })).toHaveValue("Direction");
  expect(screen.queryByText("You have unsaved changes. Save or discard them before continuing to assessment, clarification, or Profile review.")).not.toBeInTheDocument();
});

it("drives generate, review, confirm, stale reassessment, and all assessment sections without fetching clarifications early", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const generate = await screen.findByRole("button", { name: "Create my career assessment" });
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/clarifications")).toHaveLength(0);
  request.mockResolvedValueOnce(assessment("review_ready")); fireEvent.click(generate);
  await screen.findByRole("heading", { name: "Review your assessment" });
  for (const title of ["Professional positioning", "Transferable strengths", "Development gaps", "Role hypotheses", "Transition assessment", "Open questions", "Career strategy summary", "Job-search strategy summary"]) expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
  expect(screen.getByText("Based on your adviser intake")).toBeInTheDocument();
  request.mockResolvedValueOnce(assessment("review_ready")); fireEvent.click(screen.getByRole("button", { name: "Create a different assessment" }));
  await screen.findByRole("heading", { name: "Review your assessment" });
  request.mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Review this assessment" }));
  expect(screen.getByRole("button", { name: "Looks right — use this" })).toBeEnabled();
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/assessment/confirm")).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Looks right — use this" }));
  await screen.findByRole("button", { name: "Update my assessment" });
  request.mockResolvedValueOnce(assessment("review_ready")); fireEvent.click(screen.getByRole("button", { name: "Update my assessment" }));
  await screen.findByRole("heading", { name: "Review your assessment" });
});

it("adopts normalized interpretation answers, re-interprets edited answers, and only renders returned evidence", async () => {
  const first = { ...unanswered(), status: "review_ready" as const, answer_text: "Normalized answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Fact context", proposed_evidence: [] } };
  const second = { ...first, answer_text: "Second normalized answer", interpretation: { answer_kind: "mixed", confirmed_context_summary: "Mixed context", proposed_evidence: [{ title: "Returned evidence", text: "Only returned evidence", skills: ["safe"] }] } };
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([unanswered()]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Continue with this follow-up" });
  fireEvent.click(screen.getByRole("button", { name: "Continue with this follow-up" }));
  const editor = await screen.findByRole("textbox", { name: "Your answer" });
  fireEvent.change(editor, { target: { value: "  raw answer  " } });
  request.mockResolvedValueOnce(first); fireEvent.click(screen.getByRole("button", { name: "Review my answer" }));
  const legacyAnswerCall = request.mock.calls.find(([path]) => String(path).endsWith("/answer"));
  expect(JSON.parse(String((legacyAnswerCall?.[1] as { body: string }).body))).toEqual({ answer_text: "  raw answer  " });
  await vi.waitFor(() => expect(editor).toHaveValue("Normalized answer"));
  expect(screen.getByText("Fact context")).toBeInTheDocument();
  expect(screen.queryByText("Returned evidence")).not.toBeInTheDocument();
  fireEvent.change(editor, { target: { value: "edited" } });
  expect(screen.getByText("You changed your answer. Review it again before confirming.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Yes, that’s right" })).not.toBeInTheDocument();
  request.mockResolvedValueOnce(second); fireEvent.click(screen.getByRole("button", { name: "Review my answer" }));
  await vi.waitFor(() => expect(editor).toHaveValue("Second normalized answer"));
  expect(screen.queryByText("Returned evidence")).not.toBeInTheDocument(); expect(screen.queryByText("Only returned evidence")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Yes, that’s right" })).toBeEnabled();
});

it("keeps provider failures safe and never runs state recovery for failed semantic mutations", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Create my career assessment" });
  request.mockRejectedValueOnce(new ApiError(503, "private provider body")); fireEvent.click(screen.getByRole("button", { name: "Create my career assessment" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Assessment generation is temporarily unavailable.");
  expect(screen.queryByText("private provider body")).not.toBeInTheDocument();
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/onboarding/status")).toHaveLength(1);
});

it("locks cross-operation controls while Save, assessment, interpretation, and clarification confirmation are pending", async () => {
  const ApiError = (await import("./auth")).ApiError;
  let resolveSave!: (value: unknown) => void;
  const delayedSave = new Promise<unknown>((resolve) => { resolveSave = resolve; });
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Create my career assessment" });
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "Dirty" } });
  request.mockReturnValueOnce(delayedSave).mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, "")); fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  resolveSave(intake); await vi.waitFor(() => expect(screen.getByRole("button", { name: "Save career direction" })).toBeEnabled());
});

it("keeps Profile enrichment ahead of assessment updating after a career clarification", async () => {
  const reviewing = { ...unanswered(), status: "review_ready" as const, answer_text: "Answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Context", proposed_evidence: [] } };
  const confirmed = { ...reviewing, status: "confirmed" as const };
  const activeEnrichment = status(true, "stale", true);
  activeEnrichment.adviser.journey.unresolved_profile_enrichment_count = 1;
  activeEnrichment.adviser.journey.next_enrichment_clarification_id = "question";
  activeEnrichment.adviser.journey.next_enrichment = { clarification_id: "question", question_text: "What delivery fact?", confirmed_context_summary: "Context", has_profile_evidence: true };
  activeEnrichment.adviser.journey.next_action = "review_profile_enrichment";
  request.mockResolvedValueOnce(status(true, "confirmed", true)).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]).mockResolvedValueOnce(confirmed).mockResolvedValueOnce(activeEnrichment).mockResolvedValueOnce(assessment("stale"));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Yes, that’s right" });
  fireEvent.click(screen.getByRole("button", { name: "Yes, that’s right" })); await screen.findAllByRole("button", { name: "Review for Profile" });
  expect(screen.getByRole("heading", { name: "Your new career information is ready for your review." })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Update my assessment" })).not.toBeInTheDocument();
  expect(screen.getByText("There is 1 optional Profile update to review. You can do this now or continue to your assessment.")).toBeInTheDocument();
});

it("restores persisted review-ready clarification after unchanged Save, but clears it when the refreshed assessment is stale", async () => {
  const reviewing = { ...unanswered(), status: "review_ready" as const, answer_text: "Persisted", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Persisted context", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("textbox", { name: "Your answer" });
  request.mockResolvedValueOnce(intake).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  expect(await screen.findByRole("textbox", { name: "Your answer" })).toHaveValue("Persisted");
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "Changed" } });
  request.mockResolvedValueOnce({ ...intake, career_direction: "Changed" }).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  expect(await screen.findByRole("button", { name: "Update my assessment" })).toBeEnabled();
  expect(screen.queryByRole("textbox", { name: "Your answer" })).not.toBeInTheDocument();
});

it("recovers clarification answer conflicts without retrying the failed mutation", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const item = unanswered();
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([item]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Continue with this follow-up" });
  fireEvent.click(screen.getByRole("button", { name: "Continue with this follow-up" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Your answer" }), { target: { value: "Answer" } });
  request.mockRejectedValueOnce(new ApiError(409, "")).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Review my answer" }));
  expect(await screen.findByRole("button", { name: "Update my assessment" })).toBeEnabled();
  expect(screen.queryByRole("textbox", { name: "Your answer" })).not.toBeInTheDocument();
  expect(request.mock.calls.filter((call) => String(call[0]).endsWith("/answer"))).toHaveLength(1);
});

it("recovers clarification confirmation conflicts without retrying the failed mutation", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const reviewing = { ...unanswered(), status: "review_ready" as const, answer_text: "Answer", interpretation: { answer_kind: "eligibility_fact", confirmed_context_summary: "Eligibility", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Yes, that’s right" });
  request.mockRejectedValueOnce(new ApiError(404, "")).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Yes, that’s right" }));
  expect(await screen.findByRole("button", { name: "Update my assessment" })).toBeEnabled();
  expect(screen.queryByRole("textbox", { name: "Your answer" })).not.toBeInTheDocument();
  expect(request.mock.calls.filter((call) => String(call[0]).endsWith("/confirm"))).toHaveLength(1);
});

it("locks Save, confirmation, and sibling selection while an interpretation is in flight", async () => {
  let resolveInterpret!: (value: unknown) => void;
  const delayedInterpret = new Promise<unknown>((resolve) => { resolveInterpret = resolve; });
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([unanswered("a", 0), unanswered("b", 1)]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findAllByRole("button", { name: "Continue with this follow-up" });
  fireEvent.click(screen.getAllByRole("button", { name: "Continue with this follow-up" })[0]);
  fireEvent.change(screen.getByRole("textbox", { name: "Your answer" }), { target: { value: "Answer" } });
  request.mockReturnValueOnce(delayedInterpret); fireEvent.click(screen.getByRole("button", { name: "Review my answer" }));
  expect(screen.getByRole("button", { name: "Save career direction" })).toBeDisabled();
  expect(screen.queryByText("Later question")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Continue with this follow-up" })).toBeDisabled();
  resolveInterpret({ ...unanswered("a", 0), status: "review_ready", answer_text: "Answer", interpretation: { answer_kind: "preference_intent", confirmed_context_summary: "Preference", proposed_evidence: [] } });
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "Yes, that’s right" })).toBeEnabled());
});

it("ignores a delayed pre-save clarification list after a changed intake makes the assessment stale", async () => {
  let resolveClarifications!: (value: unknown) => void;
  const delayedClarifications = new Promise<unknown>((resolve) => { resolveClarifications = resolve; });
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockReturnValueOnce(delayedClarifications);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Save career direction" });
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "Changed" } });
  request.mockResolvedValueOnce({ ...intake, career_direction: "Changed" }).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  expect(await screen.findByRole("button", { name: "Update my assessment" })).toBeEnabled();
  resolveClarifications([unanswered("late")]);
  await vi.waitFor(() => expect(screen.queryByRole("textbox", { name: "Your answer" })).not.toBeInTheDocument());
  expect(screen.queryByText("Question late")).not.toBeInTheDocument();
});

it("saves every edited intake list and eligibility value, then keeps the normalized authority visible", async () => {
  const ApiError = (await import("./auth")).ApiError;
  const labels = [["What do you want in your next role?", "work preferences"], ["What should we account for?", "constraints"], ["What are your strengths or areas to build?", "self assessment"], ["What motivates you?", "motivations"], ["What trade-offs matter most?", "tradeoffs"], ["Where can you work?", "work authorisation"], ["Any required clearances?", "security clearances"], ["Which locations work for you?", "locations"]] as const;
  const normalized = {
    career_direction: "Saved direction", work_preferences: ["work preferences saved"], constraints: ["constraints saved"], self_assessment: ["self assessment saved"], motivations: ["motivations saved"], tradeoffs: ["tradeoffs saved"],
    eligibility: { work_authorisation: ["work authorisation saved"], security_clearances: ["security clearances saved"], locations: ["locations saved"] }, updated_at: "2026-01-02",
  };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Save career direction" });
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "Saved direction" } });
  for (const [label, value] of labels) {
    const group = screen.getByRole("group", { name: label }); fireEvent.click(within(group).getByRole("button", { name: "Add" }));
    fireEvent.change(screen.getByRole("textbox", { name: `${label} 1` }), { target: { value: `${value} saved` } });
  }
  request.mockResolvedValueOnce(normalized).mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  await vi.waitFor(() => expect(screen.getByRole("textbox", { name: "Where would you like your career to go?" })).toHaveValue("Saved direction"));
  const [, options] = request.mock.calls.find((call) => call[0] === "/api/v1/candidate-adviser/intake" && (call[1] as { method?: string } | undefined)?.method === "PUT") ?? [];
  const payload = JSON.parse((options as { body: string }).body);
  expect(payload).toEqual(expect.objectContaining({ work_preferences: ["work preferences saved"], constraints: ["constraints saved"], self_assessment: ["self assessment saved"], motivations: ["motivations saved"], tradeoffs: ["tradeoffs saved"] }));
  expect(payload.eligibility).toEqual(normalized.eligibility);
  for (const [label, value] of labels) expect(screen.getByRole("textbox", { name: `${label} 1` })).toHaveValue(`${value} saved`);
});

it("prevents duplicate Interpret while its request is pending", async () => {
  let resolveInterpret!: (value: unknown) => void; const delayed = new Promise<unknown>((resolve) => { resolveInterpret = resolve; });
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([unanswered()]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Continue with this follow-up" });
  fireEvent.click(screen.getByRole("button", { name: "Continue with this follow-up" })); fireEvent.change(screen.getByRole("textbox", { name: "Your answer" }), { target: { value: "Answer" } });
  request.mockReturnValueOnce(delayed); const interpret = screen.getByRole("button", { name: "Review my answer" }); fireEvent.click(interpret); fireEvent.click(interpret);
  expect(interpret).toBeDisabled(); expect(request.mock.calls.filter((call) => String(call[0]).endsWith("/answer"))).toHaveLength(1);
  resolveInterpret({ ...unanswered(), status: "review_ready", answer_text: "Answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Context", proposed_evidence: [] } });
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "Yes, that’s right" })).toBeEnabled());
});

it("prevents duplicate clarification confirmation and retires its editor after one successful confirmation", async () => {
  let resolveConfirmation!: (value: unknown) => void; const delayed = new Promise<unknown>((resolve) => { resolveConfirmation = resolve; });
  const reviewing = { ...unanswered(), status: "review_ready" as const, answer_text: "Answer", interpretation: { answer_kind: "preference_intent", confirmed_context_summary: "Preference", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); const confirm = await screen.findByRole("button", { name: "Yes, that’s right" });
  request.mockReturnValueOnce(delayed); fireEvent.click(confirm); fireEvent.click(confirm);
  expect(confirm).toBeDisabled(); expect(request.mock.calls.filter((call) => String(call[0]).endsWith("/confirm"))).toHaveLength(1);
  request.mockResolvedValueOnce(status(true, "stale", true)).mockResolvedValueOnce(assessment("stale")); resolveConfirmation({ ...reviewing, status: "confirmed" });
  await screen.findByRole("heading", { name: "Your new career information is ready for your review." });
  expect(screen.queryByRole("button", { name: "Yes, that’s right" })).not.toBeInTheDocument();
});

it("blocks every lifecycle and clarification mutation while the authoritative intake is dirty", async () => {
  const reviewing = { ...unanswered(), status: "review_ready" as const, answer_text: "Answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Context", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("review_ready"));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Review this assessment" });
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "Dirty" } });
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Create a different assessment" })).toBeDisabled();
  request.mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  fireEvent.click(screen.getByRole("button", { name: "Review this assessment" }));
  fireEvent.click(screen.getByRole("button", { name: "Looks right — use this" }));
  await screen.findByRole("button", { name: "Yes, that’s right" });
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "Dirty again" } });
  expect(screen.getByRole("textbox", { name: "Your answer" })).toBeDisabled(); expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

it.each([502, 503])("keeps clarification provider failure %s safe and outside conflict recovery", async (code) => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([unanswered()]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Continue with this follow-up" }); fireEvent.click(screen.getByRole("button", { name: "Continue with this follow-up" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Your answer" }), { target: { value: "Answer" } }); request.mockRejectedValueOnce(new ApiError(code, "private provider detail"));
  fireEvent.click(screen.getByRole("button", { name: "Review my answer" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Clarification interpretation is temporarily unavailable.");
  expect(screen.queryByText("private provider detail")).not.toBeInTheDocument(); expect(request.mock.calls.filter((call) => call[0] === "/api/v1/onboarding/status")).toHaveLength(1);
});

it("retains unanswered sibling clarifications after confirming one answer", async () => {
  const first = { ...unanswered("first", 0), status: "review_ready" as const, answer_text: "Answer", interpretation: { answer_kind: "preference_intent", confirmed_context_summary: "Preference", proposed_evidence: [] } };
  const sibling = unanswered("sibling", 1);
  const confirmed = { ...first, status: "confirmed" as const };
  request.mockResolvedValueOnce(status(true, "confirmed", true)).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([first, sibling]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Yes, that’s right" });
  request.mockResolvedValueOnce(confirmed).mockResolvedValueOnce(status(true, "stale", true)).mockResolvedValueOnce(assessment("stale")).mockResolvedValueOnce([confirmed, sibling]);
  fireEvent.click(screen.getByRole("button", { name: "Yes, that’s right" }));
  expect(await screen.findByRole("button", { name: "Continue with this follow-up" })).toBeEnabled(); expect(screen.getByText("Question sibling")).toBeInTheDocument();
  expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/clarifications")).toHaveLength(2);
});

it("keeps clarification authority loading until its real GET resolves", async () => {
  let resolveClarifications!: (value: unknown) => void;
  const delayedClarifications = new Promise<unknown>((resolve) => { resolveClarifications = resolve; });
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockReturnValueOnce(delayedClarifications);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByText("Loading an optional follow-up…")).toBeInTheDocument();
  expect(screen.queryByText("No optional follow-up is available right now.")).not.toBeInTheDocument();
  resolveClarifications([]);
  expect(await screen.findByText("No optional follow-up is available right now.")).toBeInTheDocument();
});

it("keeps failed clarification authority unavailable until a successful real GET retry", async () => {
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockRejectedValueOnce(new Error("offline"));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Your optional follow-up is unavailable right now.");
  expect(screen.queryByText("No optional follow-up is available right now.")).not.toBeInTheDocument();
  request.mockResolvedValueOnce([unanswered("recovered")]);
  fireEvent.click(screen.getByRole("button", { name: "Retry follow-up" }));
  expect(await screen.findByText("Question recovered")).toBeInTheDocument();
  expect(screen.queryByText("Your optional follow-up is unavailable right now.")).not.toBeInTheDocument();
});

it("locks intake text and list editing while Save is pending", async () => {
  const existing = { ...intake, work_preferences: ["Saved preference"] };
  let resolveSave!: (value: unknown) => void;
  const delayedSave = new Promise<unknown>((resolve) => { resolveSave = resolve; });
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(existing).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const direction = await screen.findByRole("textbox", { name: "Where would you like your career to go?" });
  fireEvent.change(direction, { target: { value: "Authoritative B" } });
  request.mockReturnValueOnce(delayedSave);
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  const preferences = screen.getByRole("group", { name: "What do you want in your next role?" });
  expect(direction).toBeDisabled();
  expect(within(preferences).getByRole("textbox", { name: "What do you want in your next role? 1" })).toBeDisabled();
  expect(within(preferences).getByRole("button", { name: "Add" })).toBeDisabled();
  expect(within(preferences).getByRole("button", { name: "Remove" })).toBeDisabled();
  fireEvent.change(direction, { target: { value: "Dropped C" } });
  resolveSave({ ...existing, career_direction: "Authoritative B" });
  request.mockResolvedValueOnce(status()).mockRejectedValueOnce(new ApiError(404, ""));
  await vi.waitFor(() => expect(screen.getByRole("textbox", { name: "Where would you like your career to go?" })).toHaveValue("Authoritative B"));
});

it("locks intake editing during an assessment mutation", async () => {
  let resolveAssessment!: (value: unknown) => void;
  const delayedAssessment = new Promise<unknown>((resolve) => { resolveAssessment = resolve; });
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const direction = await screen.findByRole("textbox", { name: "Where would you like your career to go?" });
  request.mockReturnValueOnce(delayedAssessment);
  fireEvent.click(screen.getByRole("button", { name: "Create my career assessment" }));
  expect(direction).toBeDisabled();
  resolveAssessment(assessment("review_ready"));
  await screen.findByRole("heading", { name: "Review your assessment" });
});

it("locks intake editing during a clarification mutation", async () => {
  let resolveInterpret!: (value: unknown) => void;
  const delayedInterpret = new Promise<unknown>((resolve) => { resolveInterpret = resolve; });
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([unanswered()]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const direction = await screen.findByRole("textbox", { name: "Where would you like your career to go?" });
  fireEvent.click(screen.getByRole("button", { name: "Continue with this follow-up" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Your answer" }), { target: { value: "Answer" } });
  request.mockReturnValueOnce(delayedInterpret);
  fireEvent.click(screen.getByRole("button", { name: "Review my answer" }));
  expect(direction).toBeDisabled();
  resolveInterpret({ ...unanswered(), status: "review_ready", answer_text: "Answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Context", proposed_evidence: [] } });
  await vi.waitFor(() => expect(screen.getByRole("textbox", { name: "Where would you like your career to go?" })).toBeEnabled());
});

it("clears an obsolete mutation error after a successful assessment retry", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const generate = await screen.findByRole("button", { name: "Create my career assessment" });
  request.mockRejectedValueOnce(new ApiError(502, ""));
  fireEvent.click(generate);
  expect(await screen.findByRole("alert")).toHaveTextContent("Assessment generation is temporarily unavailable.");
  request.mockResolvedValueOnce(assessment("review_ready"));
  fireEvent.click(screen.getByRole("button", { name: "Create my career assessment" }));
  expect(await screen.findByRole("heading", { name: "Review your assessment" })).toBeInTheDocument();
  expect(screen.queryByText("Assessment generation is temporarily unavailable.")).not.toBeInTheDocument();
});

it("ignores a real delayed older intake Retry GET after newer Retry authority", async () => {
  const ApiError = (await import("./auth")).ApiError;
  let resolveOlderIntake!: (value: unknown) => void;
  const olderIntake = new Promise<unknown>((resolve) => { resolveOlderIntake = resolve; });
  const old = { ...intake, career_direction: "Old retry intake" };
  const current = { ...intake, career_direction: "New retry intake" };
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(503, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const retry = await screen.findByRole("button", { name: "Retry" });
  request.mockResolvedValueOnce(status()).mockReturnValueOnce(olderIntake);
  fireEvent.click(retry);
  await vi.waitFor(() => expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/intake" && !call[1])).toHaveLength(2));
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(current).mockRejectedValueOnce(new ApiError(404, ""));
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("textbox", { name: "Where would you like your career to go?" })).toHaveValue("New retry intake");
  resolveOlderIntake(old);
  await vi.waitFor(() => expect(screen.getByRole("textbox", { name: "Where would you like your career to go?" })).toHaveValue("New retry intake"));
});

it("ignores a real delayed old assessment GET after Save refreshes authority", async () => {
  let resolveOldAssessment!: (value: unknown) => void;
  const oldAssessment = new Promise<unknown>((resolve) => { resolveOldAssessment = resolve; });
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockReturnValueOnce(oldAssessment);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const direction = await screen.findByRole("textbox", { name: "Where would you like your career to go?" });
  fireEvent.change(direction, { target: { value: "New intake" } });
  request.mockResolvedValueOnce({ ...intake, career_direction: "New intake" }).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Save career direction" }));
  expect(await screen.findByRole("button", { name: "Update my assessment" })).toBeEnabled();
  resolveOldAssessment(assessment("confirmed"));
  await vi.waitFor(() => expect(screen.getByRole("button", { name: "Update my assessment" })).toBeEnabled());
  expect(screen.queryByText(/Career Adviser is up to date\./)).not.toBeInTheDocument();
});

it("keeps non-career confirmation out of intake and disables Update my assessment while intake is dirty", async () => {
  const item = { ...unanswered(), status: "review_ready" as const, answer_text: "Answer", interpretation: { answer_kind: "eligibility_fact", confirmed_context_summary: "Eligibility", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([item]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Yes, that’s right" });
  request.mockResolvedValueOnce({ ...item, status: "confirmed" }).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(screen.getByRole("button", { name: "Yes, that’s right" })); await screen.findByRole("button", { name: "Update my assessment" });
  expect(screen.getByRole("textbox", { name: "Where would you like your career to go?" })).toHaveValue("Direction"); expect(request.mock.calls.filter((call) => (call[1] as { method?: string } | undefined)?.method === "PUT")).toHaveLength(0);
  fireEvent.change(screen.getByRole("textbox", { name: "Where would you like your career to go?" }), { target: { value: "Dirty" } }); expect(screen.getByText("You have unsaved changes. Save or discard them before continuing to assessment, clarification, or Profile review.")).toBeInTheDocument(); expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

it.each([[404, "stale"], [409, "confirmed"]] as const)("recovers answer conflict %s to authoritative %s without an intake read", async (code, recovered) => {
  const ApiError = (await import("./auth")).ApiError; const restored = unanswered("restored");
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([unanswered()]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>); await screen.findByRole("button", { name: "Continue with this follow-up" }); fireEvent.click(screen.getByRole("button", { name: "Continue with this follow-up" })); fireEvent.change(screen.getByRole("textbox", { name: "Your answer" }), { target: { value: "Answer" } });
  request.mockRejectedValueOnce(new ApiError(code, "")).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment(recovered)); if (recovered === "confirmed") request.mockResolvedValueOnce([restored]);
  fireEvent.click(screen.getByRole("button", { name: "Review my answer" }));
  if (recovered === "stale") await screen.findByRole("button", { name: "Update my assessment" }); else await screen.findByText("Question restored");
  expect(request.mock.calls.filter((call) => String(call[0]).endsWith("/answer"))).toHaveLength(1); expect(request.mock.calls.filter((call) => call[0] === "/api/v1/candidate-adviser/intake" && !call[1])).toHaveLength(1); expect(screen.getByText("Adviser state changed. The current state has been refreshed.")).toBeInTheDocument();
});

it("offers profile suggestions only after a confirmed career clarification and waits for an explicit generation click", async () => {
  const reviewing = { ...unanswered("career-fact"), status: "review_ready" as const, answer_text: "Delivered a synthetic service", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Confirmed delivery experience", proposed_evidence: [{ title: "Service delivery", text: "Delivered a synthetic service.", skills: ["planning"] }] } };
  const confirmed = { ...reviewing, status: "confirmed" as const };
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  request.mockResolvedValueOnce(confirmed).mockResolvedValueOnce(status(true, "stale", true)).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(await screen.findByRole("button", { name: "Yes, that’s right" }));
  await screen.findByRole("heading", { name: "Your new career information is ready for your review." });
  const generate = await screen.findByRole("button", { name: "Review for Profile" });
  expect(screen.getByText("Suggestions use only the career facts you confirmed above. Generating suggestions does not change your current Profile.")).toBeInTheDocument();
  expect(request.mock.calls.filter(([url]) => String(url).includes("/profile-proposals"))).toHaveLength(0);

  request.mockResolvedValueOnce([]).mockResolvedValueOnce({ proposals: [] }).mockResolvedValueOnce([]).mockResolvedValueOnce(status()).mockResolvedValueOnce(assessment("stale"));
  fireEvent.click(generate);
  expect(await screen.findByText("No structured Profile suggestions were produced from this clarification. Your current Profile is unchanged.")).toBeInTheDocument();
  expect(request).toHaveBeenCalledWith("/api/v1/candidate-adviser/clarifications/career-fact/profile-proposals", { method: "POST" });
});

it("isolates profile suggestion history failures from the Adviser assessment lifecycle", async () => {
  const ApiError = (await import("./auth")).ApiError;
  request.mockResolvedValueOnce(status()).mockResolvedValueOnce(intake).mockRejectedValueOnce(new ApiError(404, ""));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  await screen.findByRole("button", { name: "Create my career assessment" });
  request.mockRejectedValueOnce(new Error("history offline"));
  fireEvent.click(screen.getByRole("button", { name: "View profile suggestions" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Profile suggestions could not be loaded.");
  expect(screen.getByRole("textbox", { name: "Where would you like your career to go?" })).toBeEnabled();
  request.mockResolvedValueOnce(assessment("review_ready"));
  fireEvent.click(screen.getByRole("button", { name: "Create my career assessment" }));
  expect(await screen.findByRole("heading", { name: "Review your assessment" })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Profile suggestions could not be loaded.");
});


it("honors persisted Profile enrichment priority after a fresh Adviser page load", async () => {
  const base = status(true, "stale");
  const persisted = {
    ...base,
    adviser: {
      ...base.adviser,
      assessment_status: "stale",
      journey: {
        ...base.adviser.journey,
        assessment_status: "stale",
        confirmed_guidance_active: false,
        unresolved_profile_enrichment_count: 1,
        next_enrichment_clarification_id: "career-fact",
        next_enrichment: {
          clarification_id: "career-fact",
          question_text: "What delivery outcome should be added?",
          confirmed_context_summary: "Delivered a production system.",
          has_profile_evidence: true,
        },
        next_action: "review_profile_enrichment",
        status_category: "review",
      },
    },
  };
  request.mockResolvedValueOnce(persisted).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("stale"));
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const journey = await screen.findByRole("region", { name: "Career Adviser" });
  expect(await within(journey).findByRole("button", { name: "Review for Profile" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Update my assessment" })).not.toBeInTheDocument();
});

it("keeps clarification confirmation beside the reviewed interpretation", async () => {
  const reviewing = { ...unanswered(), status: "review_ready" as const, answer_text: "Answer", interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Reviewed context", proposed_evidence: [] } };
  request.mockResolvedValueOnce(status(true, "confirmed")).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([reviewing]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const review = await screen.findByRole("button", { name: "Review what I understood" });
  fireEvent.click(review);
  expect(request.mock.calls.filter((call) => String(call[0]).endsWith("/confirm"))).toHaveLength(0);
  expect(screen.getByRole("heading", { name: "Here’s what I understood" }).closest("article")).toHaveFocus();
  expect(screen.getByRole("button", { name: "Yes, that’s right" })).toBeEnabled();
});

it("supports multi-select plus custom detail and posts the structured response without flattening it", async () => {
  const question = optionClarification();
  request.mockResolvedValueOnce(status(true, "confirmed", true)).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([question]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const first = await screen.findByRole("checkbox", { name: "I led the synthetic project" });
  const second = screen.getByRole("checkbox", { name: "I contributed to implementation" });
  fireEvent.click(first); fireEvent.click(second);
  const details = screen.getByRole("textbox", { name: "Add details (optional)" });
  fireEvent.change(details, { target: { value: "A little extra synthetic detail." } });
  expect(screen.getByRole("button", { name: "Review my answer" })).toBeEnabled();
  const response = { selected_option_ids: ["a".repeat(64), "b".repeat(64)], custom_answer_text: "A little extra synthetic detail.", special_selection: null };
  const reviewed = { ...question, status: "review_ready", answer_text: "projection", structured_response: response, interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Reviewed selections.", proposed_evidence: [] } };
  request.mockResolvedValueOnce(reviewed);
  fireEvent.click(screen.getByRole("button", { name: "Review my answer" }));
  await screen.findByText("Reviewed selections.");
  const answerCall = request.mock.calls.find(([path]) => String(path).endsWith("/answer"));
  expect(JSON.parse(String((answerCall?.[1] as { body: string }).body))).toEqual(response);
  expect(screen.getByRole("button", { name: "Yes, that’s right" })).toBeEnabled();
  expect(screen.getByText("I supported testing and release")).toBeInTheDocument();

  fireEvent.click(second);
  expect(screen.getByText("You changed your answer. Review it again before confirming.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Yes, that’s right" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review my answer" })).toBeEnabled();
});

it("restores structured selections after reload and enforces not-sure exclusivity", async () => {
  const structured = { selected_option_ids: ["b".repeat(64)], custom_answer_text: "Saved synthetic detail", special_selection: null };
  const question = optionClarification("review_ready", structured);
  request.mockResolvedValueOnce(status(true, "confirmed", true)).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([question]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  expect(await screen.findByRole("checkbox", { name: "I contributed to implementation" })).toBeChecked();
  expect(screen.getByRole("textbox", { name: "Add details (optional)" })).toHaveValue("Saved synthetic detail");
  expect(screen.getByRole("button", { name: "Yes, that’s right" })).toBeEnabled();
  const notSure = screen.getByRole("checkbox", { name: "I’m not sure / I don’t have enough information to answer this yet" });
  fireEvent.click(notSure);
  expect(notSure).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "I contributed to implementation" })).not.toBeChecked();
  expect(screen.getByRole("textbox", { name: "Add details (optional)" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Add details (optional)" })).toHaveValue("");
  expect(screen.getByRole("button", { name: "Review my answer" })).toBeEnabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "I led the synthetic project" }));
  expect(notSure).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "I led the synthetic project" })).toBeChecked();
  expect(screen.getByRole("textbox", { name: "Add details (optional)" })).toBeEnabled();
  expect(screen.getByRole("group", { name: "Choose any that apply" })).toHaveClass("adviser-answer-options");
});

it("allows custom detail alone for an option-backed question", async () => {
  const question = optionClarification();
  request.mockResolvedValueOnce(status(true, "confirmed", true)).mockResolvedValueOnce(intake).mockResolvedValueOnce(assessment("confirmed")).mockResolvedValueOnce([question]);
  render(<MemoryRouter><AdviserPage /></MemoryRouter>);
  const detail = await screen.findByRole("textbox", { name: "Add details (optional)" });
  expect(screen.getByRole("button", { name: "Review my answer" })).toBeDisabled();
  fireEvent.change(detail, { target: { value: "A custom-only synthetic answer." } });
  expect(screen.getByRole("button", { name: "Review my answer" })).toBeEnabled();
  const response = { selected_option_ids: [], custom_answer_text: "A custom-only synthetic answer.", special_selection: null };
  request.mockResolvedValueOnce({ ...question, status: "review_ready", answer_text: "display projection", structured_response: response, interpretation: { answer_kind: "career_fact", confirmed_context_summary: "Custom answer reviewed.", proposed_evidence: [] } });
  fireEvent.click(screen.getByRole("button", { name: "Review my answer" }));
  await screen.findByText("Custom answer reviewed.");
  const answerCall = request.mock.calls.find(([path]) => String(path).endsWith("/answer"));
  expect(JSON.parse(String((answerCall?.[1] as { body: string }).body))).toEqual(response);
});

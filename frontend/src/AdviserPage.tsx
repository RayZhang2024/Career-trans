import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, useAuth } from "./auth";
import type { OnboardingStatus } from "./api";
import { ProfileSuggestions } from "./ProfileSuggestions";

type Intake = { career_direction: string; work_preferences: string[]; constraints: string[]; self_assessment: string[]; motivations: string[]; tradeoffs: string[]; eligibility: { work_authorisation: string[]; security_clearances: string[]; locations: string[] } };
type Insight = { text: string; source_references: Array<{ source_type: string; reference: string }> };
type SuggestedAnswer = { option_id: string; text: string };
type StructuredResponse = { selected_option_ids: string[]; custom_answer_text: string; special_selection: "not_sure" | null };
type Assessment = { input_fingerprint: string; assessment_authority_token: string; status: "review_ready" | "confirmed" | "stale"; contract_version?: "legacy_questions" | "clarification_areas_v1"; content: { professional_positioning: Insight; transferable_strengths: Insight[]; development_gaps: Insight[]; role_hypotheses: Insight[]; transition_assessment: Insight; open_questions: Array<Insight & { suggested_answers?: string[] }>; clarification_areas?: Array<{ area_key: string; title: string; rationale: string; source_references: Insight["source_references"] }>; assessment_limitations?: string[]; career_strategy_summary: Insight; job_search_strategy_summary: Insight } };
type Clarification = { clarification_id: string; question_text: string; priority_index: number; parent_area_key?: string | null; parent_area_title?: string | null; round_number?: 1 | 2 | null; status: "unanswered" | "review_ready" | "confirmed"; answer_text: string | null; suggested_answers?: SuggestedAnswer[]; structured_response?: StructuredResponse | null; session_active?: boolean; interpretation: { answer_kind: "career_fact" | "mixed" | "eligibility_fact" | "preference_intent" | "insufficient"; confirmed_context_summary: string; proposed_evidence: Array<{ title: string; text: string; skills: string[] }> } | null };
type AssessmentMutation = "create" | "update" | "regenerate";
type IntakeRead = Intake & { updated_at?: string };
type Journey = NonNullable<OnboardingStatus["adviser"]["journey"]>;
type Enrichment = Journey["next_enrichment"];
const blank = (): Intake => ({ career_direction: "", work_preferences: [], constraints: [], self_assessment: [], motivations: [], tradeoffs: [], eligibility: { work_authorisation: [], security_clearances: [], locations: [] } });
const intakeLists = ["work_preferences", "constraints", "self_assessment", "motivations", "tradeoffs"] as const;
const eligibilityLists = ["work_authorisation", "security_clearances", "locations"] as const;
const friendly = () => "This Adviser state changed or is unavailable. Refresh and try again.";
const sourceLabel = (source: string) => source === "intake" ? "Based on your adviser intake" : source === "clarification" ? "Based on a confirmed clarification" : "Based on confirmed career evidence";

function ListInput({ label, values, onChange, disabled }: { label: string; values: string[]; onChange: (next: string[]) => void; disabled: boolean }) {
  return <fieldset className="adviser-list"><legend>{label}</legend>{values.map((value, index) => <div className="inline-control" key={index}><input aria-label={`${label} ${index + 1}`} value={value} disabled={disabled} onChange={(event) => onChange(values.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} /><button type="button" className="button-secondary" disabled={disabled} onClick={() => onChange(values.filter((_, itemIndex) => itemIndex !== index))}>Remove</button></div>)}<button type="button" className="button-secondary" disabled={disabled} onClick={() => onChange([...values, ""])}>Add</button></fieldset>;
}

function AssessmentView({ assessment }: { assessment: Assessment }) {
  const sections: Array<[string, Insight[]]> = [["Professional positioning", [assessment.content.professional_positioning]], ["Transferable strengths", assessment.content.transferable_strengths], ["Development gaps", assessment.content.development_gaps], ["Role hypotheses", assessment.content.role_hypotheses], ["Transition assessment", [assessment.content.transition_assessment]], ["Open questions", assessment.content.open_questions], ["Career strategy summary", [assessment.content.career_strategy_summary]], ["Job-search strategy summary", [assessment.content.job_search_strategy_summary]]];
  return <section className="card adviser-assessment"><h2>Career assessment</h2><div className="adviser-assessment-summary" aria-label="Assessment summary"><section><h3>How you’re positioned</h3><p>{assessment.content.professional_positioning.text}</p></section>{assessment.content.role_hypotheses.length > 0 && <section><h3>Directions to explore</h3><ul>{assessment.content.role_hypotheses.slice(0, 3).map((item, index) => <li key={index}>{item.text}</li>)}</ul></section>}{assessment.content.transferable_strengths.length > 0 && <section><h3>Your strongest advantages</h3><ul>{assessment.content.transferable_strengths.slice(0, 3).map((item, index) => <li key={index}>{item.text}</li>)}</ul></section>}{assessment.content.development_gaps.length > 0 && <section><h3>Areas to build</h3><ul>{assessment.content.development_gaps.slice(0, 3).map((item, index) => <li key={index}>{item.text}</li>)}</ul></section>}</div>{Boolean(assessment.content.assessment_limitations?.length) && <section><h3>Assessment limitations</h3><ul>{assessment.content.assessment_limitations?.map((item) => <li key={item}>{item}</li>)}</ul></section>}<details className="adviser-assessment-details"><summary>Read full assessment and sources</summary>{sections.map(([title, insights]) => <section className="cv-section" key={title}><h3>{title}</h3>{insights.map((insight, index) => <article key={index}><p>{insight.text}</p><p className="muted">{[...new Set(insight.source_references.map((reference) => sourceLabel(reference.source_type)))].join(" · ")}</p></article>)}</section>)}</details></section>;
}

export function AdviserPage() {
  const { api } = useAuth(); const navigate = useNavigate(); const [onboarding, setOnboarding] = useState<OnboardingStatus | undefined>(); const [saved, setSaved] = useState<Intake | null | undefined>(undefined); const [form, setForm] = useState<Intake>(blank()); const [assessment, setAssessment] = useState<Assessment | null | undefined>(undefined); const [clarifications, setClarifications] = useState<Clarification[]>([]); const [clarificationAuthority, setClarificationAuthority] = useState<"idle" | "loading" | "ready" | "unavailable">("idle"); const [clarificationError, setClarificationError] = useState(""); const [active, setActive] = useState<string | null>(null); const [answer, setAnswer] = useState(""); const [selectedOptionIds, setSelectedOptionIds] = useState<string[]>([]); const [customAnswer, setCustomAnswer] = useState(""); const [notSure, setNotSure] = useState(false); const [interpretedAnswer, setInterpretedAnswer] = useState(""); const [interpretedStructuredKey, setInterpretedStructuredKey] = useState(""); const [confirmedTransition, setConfirmedTransition] = useState<Clarification | null>(null); const [followUpDismissed, setFollowUpDismissed] = useState(false); const [focusClarificationWhenReady, setFocusClarificationWhenReady] = useState(false); const [focusAreaSelectorWhenReady, setFocusAreaSelectorWhenReady] = useState(false); const [selectedAreaKeys, setSelectedAreaKeys] = useState<string[]>([]); const [pending, setPending] = useState(""); const [assessmentMutation, setAssessmentMutation] = useState<AssessmentMutation | null>(null); const [intakeRefreshPending, setIntakeRefreshPending] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const generation = useRef(0); const clarificationAreaRef = useRef<HTMLElement>(null); const areaSelectionRef = useRef<HTMLElement>(null);
  const responseKey = (response: StructuredResponse) => JSON.stringify({ selected_option_ids: response.selected_option_ids, custom_answer_text: response.custom_answer_text, special_selection: response.special_selection });
  const normalize = (value: IntakeRead): Intake => ({ career_direction: value.career_direction, work_preferences: value.work_preferences, constraints: value.constraints, self_assessment: value.self_assessment, motivations: value.motivations, tradeoffs: value.tradeoffs, eligibility: value.eligibility });
  const resetClarifications = (authority: "idle" | "loading" | "ready" | "unavailable" = "idle") => { setClarifications([]); setClarificationAuthority(authority); setActive(null); setAnswer(""); setSelectedOptionIds([]); setCustomAnswer(""); setNotSure(false); setInterpretedAnswer(""); setInterpretedStructuredKey(""); };
  const dirty = saved === undefined ? false : JSON.stringify(saved ?? blank()) !== JSON.stringify(form); const locked = Boolean(pending) || dirty || saved === undefined || saved === null;
  const load = async () => { const request = ++generation.current; setError(""); setSaved(undefined); setAssessment(undefined); resetClarifications(); try { const status = await api.request<OnboardingStatus>("/api/v1/onboarding/status"); if (request !== generation.current) return; setOnboarding(status); if (!status.candidate_context_ready) return; try { const intake = normalize(await api.request<IntakeRead>("/api/v1/candidate-adviser/intake")); if (request === generation.current) { setSaved(intake); setForm(intake); } } catch (caught) { if (request !== generation.current) return; if (caught instanceof ApiError && caught.status === 404) { setSaved(null); setForm(blank()); } else { setSaved(undefined); setError("Adviser intake is unavailable."); return; } } await loadAssessment(request, status.adviser.journey.confirmed_guidance_active); } catch { if (request === generation.current) setError("Adviser status is unavailable."); } };
  const loadAssessment = async (request = ++generation.current, sessionActive = onboarding?.adviser.journey.confirmed_guidance_active ?? false) => { setAssessment(undefined); resetClarifications(); try { const found = await api.request<Assessment>("/api/v1/candidate-adviser/assessment"); if (request !== generation.current) return; setAssessment(found); if (found.status === "confirmed" || sessionActive) await loadClarifications(request); } catch (caught) { if (request !== generation.current) return; if (caught instanceof ApiError && caught.status === 404) setAssessment(null); else { setAssessment(undefined); setError("Adviser assessment is unavailable."); } } };
  const loadClarifications = async (request = ++generation.current) => { resetClarifications("loading"); setClarificationError(""); try { const found = await api.request<Clarification[]>("/api/v1/candidate-adviser/clarifications"); if (request !== generation.current) return; const sorted = [...found].sort((a,b) => a.priority_index - b.priority_index || a.clarification_id.localeCompare(b.clarification_id)); const sessionActive = sorted.some((item) => item.session_active); setClarifications(sorted); setOnboarding((current) => current ? { ...current, adviser: { ...current.adviser, journey: { ...current.adviser.journey, clarification_session_active: sessionActive, confirmed_guidance_active: sessionActive || current.adviser.journey.confirmed_guidance_active } } } : current); setClarificationAuthority("ready"); setFollowUpDismissed(false); const currentQuestion = sorted.find((item) => item.status === "review_ready") ?? sorted.find((item) => item.status === "unanswered"); if (currentQuestion) { setActive(currentQuestion.clarification_id); const structured = currentQuestion.structured_response; setAnswer(currentQuestion.answer_text ?? ""); setSelectedOptionIds(structured?.selected_option_ids ?? []); setCustomAnswer(structured?.custom_answer_text ?? ""); setNotSure(structured?.special_selection === "not_sure"); setInterpretedAnswer(currentQuestion.status === "review_ready" && !currentQuestion.suggested_answers?.length ? currentQuestion.answer_text ?? "" : ""); setInterpretedStructuredKey(currentQuestion.status === "review_ready" && structured ? responseKey(structured) : ""); } } catch { if (request === generation.current) { resetClarifications("unavailable"); setClarificationError("Your optional follow-up is unavailable right now."); } } };
  // Intake PUT and lifecycle conflict recovery already have a known-good
  // intake baseline. Invalidate old dependent authority immediately and load
  // status/assessment independently: an onboarding outage must not leave an
  // old confirmed assessment actionable.
  const refreshDependentAuthority = async () => {
    const request = ++generation.current; setError(""); setAssessment(undefined); resetClarifications();
    const [statusResult, assessmentResult] = await Promise.allSettled([
      api.request<OnboardingStatus>("/api/v1/onboarding/status"),
      api.request<Assessment>("/api/v1/candidate-adviser/assessment"),
    ]);
    if (request !== generation.current) return;
    if (statusResult.status === "fulfilled") setOnboarding(statusResult.value);
    else setError("Adviser status is unavailable.");
    if (assessmentResult.status === "fulfilled") {
      setAssessment(assessmentResult.value);
      if (assessmentResult.value.status !== "stale") setConfirmedTransition(null);
      if (assessmentResult.value.status === "confirmed" || (statusResult.status === "fulfilled" && statusResult.value.adviser.journey.confirmed_guidance_active)) await loadClarifications(request);
      return;
    }
    const caught = assessmentResult.reason;
    if (caught instanceof ApiError && caught.status === 404) { setAssessment(null); setConfirmedTransition(null); }
    else { setAssessment(undefined); setError("Adviser assessment is unavailable."); }
  };
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!focusAreaSelectorWhenReady || onboarding?.adviser.journey.next_action !== "select_clarification_areas") return;
    areaSelectionRef.current?.focus();
    setFocusAreaSelectorWhenReady(false);
  }, [focusAreaSelectorWhenReady, onboarding?.adviser.journey.next_action]);
  const save = async () => { if (pending || saved === undefined) return; setPending("save"); setIntakeRefreshPending(false); setError(""); setNotice(""); try { const found = normalize(await api.request<IntakeRead>("/api/v1/candidate-adviser/intake", { method: "PUT", body: JSON.stringify(form) })); setSaved(found); setForm(found); setNotice("Adviser intake saved."); resetClarifications(); setIntakeRefreshPending(true); await refreshDependentAuthority(); } catch { setError("Adviser intake could not be saved."); } finally { setIntakeRefreshPending(false); setPending(""); } };
  const mutateAssessment = async (action: AssessmentMutation) => { if (locked || pending) return; setAssessmentMutation(action); setPending("assessment"); setError(""); setNotice(""); try { const url = action === "regenerate" ? "/api/v1/candidate-adviser/assessment?replace_review_draft=true" : "/api/v1/candidate-adviser/assessment"; const found = await api.request<Assessment>(url, { method: "POST" }); setConfirmedTransition(null); setAssessment(found); resetClarifications(); } catch (caught) { setError(caught instanceof ApiError && (caught.status === 502 || caught.status === 503) ? "Assessment generation is temporarily unavailable." : friendly()); if (caught instanceof ApiError && caught.status === 409) { setNotice("Adviser state changed. The current state has been refreshed."); await refreshDependentAuthority(); } } finally { setPending(""); setAssessmentMutation(null); } };
  const confirmAssessment = async () => { if (locked || pending) return; setPending("confirm-assessment"); setError(""); setNotice(""); setFocusAreaSelectorWhenReady(true); try { await api.request<Assessment>("/api/v1/candidate-adviser/assessment/confirm", { method: "POST" }); await refreshDependentAuthority(); } catch (caught) { setError(caught instanceof ApiError && (caught.status === 502 || caught.status === 503) ? "Assessment confirmation is temporarily unavailable." : friendly()); if (caught instanceof ApiError && caught.status === 409) { setNotice("Adviser state changed. The current state has been refreshed."); await refreshDependentAuthority(); } } finally { setPending(""); } };
  const generateCommittedQuestions = async (commitSelection: boolean) => {
    if (locked || pending || !onboarding) return;
    const journey = onboarding.adviser.journey;
    if (!journey.refinement_journey_id || !journey.refinement_round_number || !assessment?.input_fingerprint || !assessment.assessment_authority_token) {
      setError(friendly());
      setNotice("Adviser state changed. The current state is being refreshed.");
      await refreshDependentAuthority();
      return;
    }
    const authority = {
      expected_refinement_journey_id: journey.refinement_journey_id,
      expected_round_number: journey.refinement_round_number,
      expected_assessment_fingerprint: assessment.input_fingerprint,
      expected_assessment_authority_token: assessment.assessment_authority_token,
    };
    setPending("generate-questions"); setError(""); setNotice(""); setFocusClarificationWhenReady(true);
    try {
      if (commitSelection) await api.request("/api/v1/candidate-adviser/refinement/areas", { method: "PUT", body: JSON.stringify({ ...authority, selected_area_keys: selectedAreaKeys }) });
      await api.request<Clarification[]>("/api/v1/candidate-adviser/refinement/questions/generate", { method: "POST", body: JSON.stringify(authority) });
      await refreshDependentAuthority();
    } catch (caught) {
      const message = caught instanceof ApiError && (caught.status === 502 || caught.status === 503) ? "Career Adviser could not prepare those questions yet. Your area selection is saved; retry when ready." : friendly();
      await refreshDependentAuthority();
      setError(message);
      if (caught instanceof ApiError && caught.status === 409) setNotice("Adviser state changed. The current state has been refreshed.");
    } finally { setPending(""); }
  };
  const continueWithoutClarification = async () => {
    if (locked || pending) return;
    const journey = onboarding?.adviser.journey;
    if (!journey?.refinement_journey_id || !journey.refinement_round_number || !assessment?.input_fingerprint || !assessment.assessment_authority_token) {
      setError(friendly());
      setNotice("Adviser state changed. The current state is being refreshed.");
      await refreshDependentAuthority();
      return;
    }
    setPending("select-areas"); setError(""); setNotice("");
    try {
      await api.request("/api/v1/candidate-adviser/refinement/areas", { method: "PUT", body: JSON.stringify({ expected_refinement_journey_id: journey.refinement_journey_id, expected_round_number: journey.refinement_round_number, expected_assessment_fingerprint: assessment.input_fingerprint, expected_assessment_authority_token: assessment.assessment_authority_token, selected_area_keys: [] }) });
      await refreshDependentAuthority();
    } catch (caught) { const message = friendly(); await refreshDependentAuthority(); setError(message); }
    finally { setPending(""); }
  };
  const unresolved = (item: Clarification) => item.status === "unanswered" || item.status === "review_ready";
  const current = clarifications.find((item) => item.clarification_id === active && unresolved(item)) ?? clarifications.find((item) => item.status === "review_ready") ?? clarifications.find((item) => item.status === "unanswered");
  const currentPosition = current ? clarifications.findIndex((item) => item.clarification_id === current.clarification_id) : -1;
  const followUpProgress = current && currentPosition >= 0 ? {
    position: currentPosition + 1,
    total: clarifications.length,
    remaining: clarifications.filter((item) => unresolved(item) && item.clarification_id !== current.clarification_id).length,
  } : null;
  const clarificationAreaAvailable = clarificationAuthority === "ready" && Boolean(current) && !followUpDismissed;
  useEffect(() => {
    if (!focusClarificationWhenReady || !clarificationAreaAvailable) return;
    clarificationAreaRef.current?.focus();
    setFocusClarificationWhenReady(false);
  }, [clarificationAreaAvailable, focusClarificationWhenReady]);
  const focusClarificationArea = () => {
    setFollowUpDismissed(false);
    setFocusClarificationWhenReady(true);
  };
  const currentStructuredResponse: StructuredResponse = { selected_option_ids: selectedOptionIds, custom_answer_text: customAnswer, special_selection: notSure ? "not_sure" : null };
  const currentStructuredKey = responseKey(currentStructuredResponse);
  const hasOptions = Boolean(current?.suggested_answers?.length);
  const structuredUnchanged = Boolean(current?.structured_response && currentStructuredKey === responseKey(current.structured_response) && interpretedStructuredKey === responseKey(current.structured_response));
  const answerCanBeReviewed = notSure || selectedOptionIds.length > 0 || Boolean(customAnswer.trim()) || (!hasOptions && Boolean(answer.trim()));
  const answerChangedSinceReview = current?.status === "review_ready" && (hasOptions ? !structuredUnchanged : answer !== interpretedAnswer);
  const interpret = async () => { if (!active || locked || !current || !answerCanBeReviewed) return; setPending("interpret"); setError(""); setNotice(""); const body = hasOptions ? currentStructuredResponse : { answer_text: answer }; try { const found = await api.request<Clarification>(`/api/v1/candidate-adviser/clarifications/${active}/answer`, { method: "POST", body: JSON.stringify(body) }); setClarifications((items) => items.map((item) => item.clarification_id === found.clarification_id ? found : item)); setAnswer(found.answer_text ?? ""); const response = found.structured_response; setSelectedOptionIds(response?.selected_option_ids ?? []); setCustomAnswer(response?.custom_answer_text ?? ""); setNotSure(response?.special_selection === "not_sure"); setInterpretedAnswer(found.answer_text ?? ""); setInterpretedStructuredKey(response ? responseKey(response) : ""); } catch (caught) { if (caught instanceof ApiError && (caught.status === 404 || caught.status === 409)) { resetClarifications(); setNotice("Adviser state changed. The current state has been refreshed."); await refreshDependentAuthority(); } else setError(caught instanceof ApiError && (caught.status === 502 || caught.status === 503) ? "Clarification interpretation is temporarily unavailable." : friendly()); } finally { setPending(""); } };
  const confirmClarification = async () => { if (!active || locked || (hasOptions ? !structuredUnchanged : answer !== interpretedAnswer)) return; setPending("confirm-clarification"); setError(""); setNotice(""); try { const found = await api.request<Clarification>(`/api/v1/candidate-adviser/clarifications/${active}/confirm`, { method: "POST" }); generation.current += 1; resetClarifications(); setConfirmedTransition(found); await refreshDependentAuthority(); } catch (caught) { if (caught instanceof ApiError && (caught.status === 404 || caught.status === 409)) { resetClarifications(); setNotice("Adviser state changed. The current state has been refreshed."); await refreshDependentAuthority(); } else setError(caught instanceof ApiError && (caught.status === 502 || caught.status === 503) ? "Clarification confirmation is temporarily unavailable." : friendly()); } finally { setPending(""); } };
  if (onboarding === undefined) return <main className="workspace adviser-page"><p className="muted" role="status">Loading Career Adviser…</p>{error && <><p role="alert">{error}</p><button onClick={() => void load()}>Retry</button></>}</main>;
  if (!onboarding.candidate_context_ready) return <main className="workspace adviser-page"><section className="card"><h1>Set up Career Adviser</h1><p>Your confirmed CV gives Career Adviser the career context it needs. Job Search remains separate and is checked against its own readiness requirements.</p><Link to="/profile/cv">Confirm your CV</Link></section></main>;
  const journey = onboarding.adviser.journey;
  const clarificationSessionActive = journey.clarification_session_active || clarifications.some((item) => item.session_active);
  const canConfirm = current?.status === "review_ready" && (hasOptions ? structuredUnchanged : answer === interpretedAnswer);
  const journeyAreas = journey.refinement_areas ?? [];
  const previewAreas = assessment?.content.clarification_areas ?? [];
  const selectedJourneyAreas = journeyAreas.filter((area) => area.selection_state === "selected");
  const areaSelectionAvailable = journey.refinement_state === "area_selection" && journeyAreas.length > 0;
  const currentAreaQuestions = current?.parent_area_key ? clarifications.filter((item) => item.parent_area_key === current.parent_area_key) : [];
  const currentAreaIndex = current?.parent_area_key ? selectedJourneyAreas.findIndex((area) => area.area_key === current.parent_area_key) : -1;
  const currentQuestionIndex = current ? currentAreaQuestions.findIndex((item) => item.clarification_id === current.clarification_id) : -1;
  const areaQuestionProgress = current && current.parent_area_title && currentAreaIndex >= 0 && currentQuestionIndex >= 0
    ? `Area ${currentAreaIndex + 1} of ${selectedJourneyAreas.length} · Question ${currentQuestionIndex + 1} of ${currentAreaQuestions.length} in ${current.parent_area_title}`
    : null;
  const assessmentPendingMessage = assessmentMutation === "create" ? "Preparing your career assessment…" : assessmentMutation === "update" ? "Updating your career assessment…" : assessmentMutation === "regenerate" ? "Preparing a different assessment…" : "";
  const pendingMessage = pending === "save" ? intakeRefreshPending ? "Refreshing career context…" : "Saving your career direction…" : pending === "assessment" ? assessmentPendingMessage : pending === "confirm-assessment" ? "Saving your assessment…" : pending === "interpret" ? "Reviewing your answer…" : pending === "confirm-clarification" ? "Saving your confirmation…" : pending === "generate-questions" ? "Preparing questions for your selected areas…" : pending === "select-areas" ? "Saving your choice…" : "";
  const focusIntake = () => document.getElementById("career-direction")?.focus();
  const focusEnrichment = () => document.getElementById("profile-suggestions-heading")?.focus();
  const focusAssessmentReview = () => document.getElementById("assessment-review")?.focus();
  const focusClarificationReview = () => document.getElementById("clarification-review")?.focus();
  const nextAction = dirty ? "save_intake" : assessment?.status === "review_ready" ? "review_assessment" : journey.clarification_interpretation_awaiting_confirmation || (clarificationSessionActive && current?.status === "review_ready") ? "confirm_clarification" : journey.next_action === "review_profile_enrichment" ? "review_profile_enrichment" : clarificationSessionActive && current?.status === "unanswered" ? "answer_follow_up" : clarificationSessionActive && journey.next_action === "answer_clarification" ? "answer_follow_up" : confirmedTransition ? (journey.unresolved_profile_enrichment_count > 0 ? "review_profile_enrichment" : "update_assessment") : assessment?.status === "stale" && !clarificationSessionActive ? "update_assessment" : assessment === null && saved === null ? "start_intake" : assessment === null && saved ? "create_assessment" : journey.next_action;
  const journeyMessage = error === "Adviser status is unavailable."
    ? "Career Adviser status could not be refreshed. Retry before continuing."
    : journey.refinement_complete ? "Your assessment has enough information for now. You can continue to Job Search whenever you’re ready."
      : journey.refinement_round_number === 2 ? "This is the final optional clarification round. Career Adviser will complete the refinement after one final assessment update."
        : journey.status_category === "up_to_date" ? "Career Adviser is up to date. Confirmed guidance can add context to opportunity evaluation."
      : journey.status_category === "review" ? "Your new career information is ready for your review."
        : journey.status_category === "update" ? "You added new information. Your assessment can now be improved."
          : "Career Adviser is an optional way to explore your career direction and strengthen job-search context.";
  const actionLabel: Record<string, string> = {
    save_intake: "Save changes", complete_profile: "Confirm your CV", start_intake: "Tell Career Adviser what you’re looking for", answer_follow_up: "Continue with this follow-up",
    create_assessment: "Create my career assessment", review_assessment: "Review this assessment",
    select_clarification_areas: "Choose areas to clarify", generate_round_questions: "Prepare questions for selected areas", refinement_complete: "Continue to Job Search",
    confirm_clarification: "Review what I understood", answer_clarification: "Continue with this follow-up", review_profile_enrichment: "Review for Profile",
    update_assessment: "Update my assessment", find_jobs: journey.job_search_ready ? "Find jobs" : "Check Profile readiness",
  };
  const doNextAction = () => {
    if (nextAction === "save_intake") void save();
    else if (nextAction === "start_intake") focusIntake();
    else if (nextAction === "review_assessment") focusAssessmentReview();
    else if (nextAction === "select_clarification_areas") areaSelectionRef.current?.focus();
    else if (nextAction === "generate_round_questions") void generateCommittedQuestions(false);
    else if (nextAction === "confirm_clarification") focusClarificationReview();
    else if (nextAction === "answer_follow_up") focusClarificationArea();
    else if (nextAction === "review_profile_enrichment") focusEnrichment();
    else if (nextAction === "create_assessment") void mutateAssessment("create");
    else if (nextAction === "update_assessment") void mutateAssessment("update");
    else if (nextAction === "complete_profile") navigate("/profile/cv");
    else if (nextAction === "find_jobs") navigate(journey.job_search_ready ? "/jobs" : "/profile/cv");
    else if (nextAction === "refinement_complete") navigate(journey.job_search_ready ? "/jobs" : "/profile/cv");
  };
  return <main className="workspace adviser-page">
    {pendingMessage && <p role="status">{pendingMessage}</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {saved === undefined ? <section className="card"><h1>Career Adviser</h1><p className="muted" role="status">Loading your saved career direction…</p><button onClick={() => void load()}>Retry</button></section> : <>
      <section className="card adviser-journey" aria-labelledby="adviser-journey-heading">
        <p className="eyebrow">Your career journey</p><h1 id="adviser-journey-heading">Career Adviser</h1>
        <p>{journeyMessage}</p>{error === "Adviser status is unavailable." && <button type="button" className="button-secondary" onClick={() => void load()}>Refresh Career Adviser</button>}
        {dirty && <p className="notice" role="status">You have unsaved changes. Save or discard them before continuing to assessment, clarification, or Profile review.</p>}
        {!dirty && journey.unresolved_profile_enrichment_count > 0 && <p className="muted">There {journey.unresolved_profile_enrichment_count === 1 ? "is 1 optional Profile update" : `are ${journey.unresolved_profile_enrichment_count} optional Profile updates`} to review. You can do this now or continue to your assessment.</p>}
        {(assessment !== undefined || (pending === "assessment" && (assessmentMutation === "create" || assessmentMutation === "update"))) && <button type="button" disabled={Boolean(pending) || saved === undefined || (nextAction === "review_assessment" && locked)} onClick={doNextAction}>{pending === "assessment" && assessmentMutation !== "regenerate" ? assessmentPendingMessage : actionLabel[nextAction] ?? "Continue"}</button>}
        {(journey.confirmed_guidance_active || clarificationSessionActive) && journey.current_follow_up_available && <button type="button" className="button-secondary" onClick={focusClarificationArea}>Answer an optional follow-up</button>}
        <p className="muted">Career Adviser is optional. <Link to="/jobs">Job Search</Link> remains available whenever your Profile meets its own readiness checks.</p>
      </section>
      <section className="card adviser-intake" aria-labelledby="adviser-intake-heading">
        <h2 id="adviser-intake-heading">Your career direction</h2><p className="muted">A few notes about what you want help with. You can update these at any time.</p>
        <label htmlFor="career-direction">Where would you like your career to go?<textarea id="career-direction" value={form.career_direction} disabled={Boolean(pending)} onChange={(event) => setForm({ ...form, career_direction: event.target.value })} /></label>
        <section aria-labelledby="what-matters-heading"><h3 id="what-matters-heading">What matters to you</h3><p className="muted">Share your preferences, motivations, strengths, and trade-offs in your next role.</p>{(["work_preferences", "motivations", "self_assessment", "tradeoffs"] as const).map((key) => <ListInput key={key} label={({ work_preferences: "What do you want in your next role?", motivations: "What motivates you?", self_assessment: "What are your strengths or areas to build?", tradeoffs: "What trade-offs matter most?" })[key]} values={form[key]} disabled={Boolean(pending)} onChange={(next) => setForm({ ...form, [key]: next })} />)}</section>
        <section aria-labelledby="practical-requirements-heading"><h3 id="practical-requirements-heading">Practical requirements</h3><p className="muted">Add any constraints or eligibility details that should shape your options.</p><ListInput label="What should we account for?" values={form.constraints} disabled={Boolean(pending)} onChange={(next) => setForm({ ...form, constraints: next })} />{eligibilityLists.map((key) => <ListInput key={key} label={({ work_authorisation: "Where can you work?", security_clearances: "Any required clearances?", locations: "Which locations work for you?" })[key]} values={form.eligibility[key]} disabled={Boolean(pending)} onChange={(next) => setForm({ ...form, eligibility: { ...form.eligibility, [key]: next } })} />)}</section>
        <div className="cv-actions"><button type="button" className="button-secondary" disabled={Boolean(pending)} onClick={() => void save()}>{pending === "save" ? "Saving…" : "Save career direction"}</button>{dirty && <button type="button" className="button-secondary" disabled={Boolean(pending)} onClick={() => setForm(saved ?? blank())}>Discard changes</button>}</div>
      </section>
      {assessment === undefined && <section className="card"><p className="muted" role="status">Loading your career assessment…</p><button className="button-secondary" onClick={() => void load()}>Retry</button></section>}
      {assessment && <AssessmentView assessment={assessment} />}
      {assessment?.status === "review_ready" && assessment.contract_version === "clarification_areas_v1" && Boolean(previewAreas.length) && <section className="card adviser-area-preview" aria-labelledby="area-preview-heading"><h2 id="area-preview-heading">Possible clarification areas</h2><p className="muted">These are previews only. Confirm the assessment before choosing which areas to explore.</p><ul>{previewAreas.map((area) => <li key={area.area_key}><label><input type="checkbox" checked={false} disabled aria-label={`${area.title} (preview only)`} /><strong>{area.title}</strong></label><p>{area.rationale}</p></li>)}</ul></section>}
      {(assessment?.status === "review_ready" || (pending === "assessment" && assessmentMutation === "regenerate")) && <section className="card" id="assessment-review" tabIndex={-1}><h2>Review your assessment</h2><p>This is a draft. Confirm it before Career-trans uses its role directions and search strategy to add context to opportunity evaluation.</p><div className="cv-actions"><button type="button" disabled={locked} onClick={() => void confirmAssessment()}>{pending === "confirm-assessment" ? "Saving your assessment…" : "Looks right — use this"}</button><button type="button" className="button-secondary" disabled={locked} onClick={() => void mutateAssessment("regenerate")}>{pending === "assessment" && assessmentMutation === "regenerate" ? assessmentPendingMessage : "Create a different assessment"}</button></div></section>}
      {areaSelectionAvailable && <section ref={areaSelectionRef} className="card adviser-area-selection" aria-labelledby="area-selection-heading" tabIndex={-1}><p className="eyebrow">{journey.refinement_round_number === 2 ? "Optional round 2" : "Choose what to explore"}</p><h2 id="area-selection-heading">Which areas would you like to clarify?</h2><p className="muted">Choose any number of areas, including all of them. You can also continue without clarification.</p><button type="button" className="button-secondary" disabled={Boolean(pending)} onClick={() => setSelectedAreaKeys(journeyAreas.map((area) => area.area_key))}>Select all</button><fieldset><legend>Clarification areas</legend>{journeyAreas.map((area) => <label className="adviser-area-option" key={area.area_key}><input type="checkbox" checked={selectedAreaKeys.includes(area.area_key)} disabled={Boolean(pending)} onChange={(event) => setSelectedAreaKeys((keys) => event.target.checked ? [...keys, area.area_key] : keys.filter((key) => key !== area.area_key))} /><span><strong>{area.title}</strong><span>{area.rationale}</span></span></label>)}</fieldset><div className="cv-actions"><button type="button" disabled={Boolean(pending) || selectedAreaKeys.length === 0} onClick={() => void generateCommittedQuestions(true)}>{pending === "generate-questions" ? "Preparing questions…" : "Clarify selected areas"}</button><button type="button" className="button-secondary" disabled={Boolean(pending)} onClick={() => void continueWithoutClarification()}>{pending === "select-areas" ? "Saving your choice…" : "Continue without clarification"}</button></div></section>}
      {journey.refinement_state === "questions_pending" && <section className="card" aria-labelledby="generate-questions-heading"><h2 id="generate-questions-heading">Your selected areas are saved</h2><p>Prepare the questions for this round. Your area selection is committed and will be reused if generation needs a retry.</p><ul>{selectedJourneyAreas.map((area) => <li key={area.area_key}>{area.title}</li>)}</ul><button type="button" disabled={Boolean(pending)} onClick={() => void generateCommittedQuestions(false)}>{pending === "generate-questions" ? "Preparing questions…" : "Prepare questions"}</button></section>}
      {journey.refinement_complete && <section className="card adviser-refinement-complete" aria-labelledby="refinement-complete-heading"><h2 id="refinement-complete-heading">Your assessment has enough information for now</h2><p>The bounded clarification process is complete. You can continue to Job Search or return later after updating your Profile or career direction.</p><Link className="button-link" to={journey.job_search_ready ? "/jobs" : "/profile/cv"}>{journey.job_search_ready ? "Continue to Job Search" : "Review Profile readiness"}</Link></section>}
      {journey.refinement_state === "questions_active" && (journey.round_question_count ?? 0) > 0 && <p className="muted" role="status" aria-live="polite">Round {journey.refinement_round_number ?? 1} · {journey.round_questions_resolved ?? 0} of {journey.round_question_count} questions complete</p>}
      {(journey.confirmed_guidance_active || clarificationSessionActive) && <section className="card"><h2>Optional follow-up</h2><p className="muted">Confirmed guidance is active. Answering a follow-up is optional and updates this assessment cycle.</p>{clarificationAuthority === "loading" && <p className="muted" role="status">Loading an optional follow-up…</p>}{clarificationAuthority === "unavailable" && <><p role="alert">{clarificationError}</p><button className="button-secondary" onClick={() => void loadClarifications()}>Retry follow-up</button></>}{clarificationAuthority === "ready" && (!current || followUpDismissed) && <p>{journey.refinement_state === "questions_active" && (journey.round_question_count ?? 0) > 0 && (journey.round_questions_resolved ?? 0) >= (journey.round_question_count ?? 0) ? "All selected questions are complete. Review optional Profile updates before updating your assessment." : "No optional follow-up is available right now."}</p>}{clarificationAuthority === "ready" && current && !followUpDismissed && <section ref={clarificationAreaRef} className="clarification-card" aria-label="Current optional follow-up" tabIndex={-1}><p className="muted">{current.parent_area_title ? `Round ${current.round_number ?? journey.refinement_round_number ?? 1} · ${current.parent_area_title}` : "One thing I’d like to understand better"}</p>{areaQuestionProgress ? <p className="muted" role="status" aria-live="polite">{areaQuestionProgress}</p> : followUpProgress && <p className="muted" role="status" aria-live="polite">Follow-up {followUpProgress.position} of {followUpProgress.total} · {followUpProgress.remaining === 0 ? "Final follow-up" : `${followUpProgress.remaining} question${followUpProgress.remaining === 1 ? "" : "s"} remaining`}</p>}<p>{current.question_text}</p>{hasOptions ? <><fieldset className="adviser-answer-options"><legend>Choose any that apply</legend>{current.suggested_answers?.map((option) => <label className="adviser-answer-option" key={option.option_id}><input type="checkbox" checked={selectedOptionIds.includes(option.option_id)} disabled={locked} onChange={(event) => { setSelectedOptionIds((values) => event.target.checked ? [...values, option.option_id] : values.filter((value) => value !== option.option_id)); if (event.target.checked) setNotSure(false); }} /><span>{option.text}</span></label>)}<label className="adviser-answer-option adviser-not-sure"><input type="checkbox" checked={notSure} disabled={locked} onChange={(event) => { setNotSure(event.target.checked); if (event.target.checked) { setSelectedOptionIds([]); setCustomAnswer(""); } }} /><span>I’m not sure / I don’t have enough information to answer this yet</span></label></fieldset><label htmlFor="clarification-custom-answer">Add details (optional)</label><textarea id="clarification-custom-answer" value={customAnswer} onChange={(event) => { setCustomAnswer(event.target.value); if (event.target.value.trim()) setNotSure(false); }} disabled={locked || notSure} /></> : <><label htmlFor="clarification-answer">Your answer</label><textarea id="clarification-answer" value={answer} onChange={(event) => setAnswer(event.target.value)} disabled={locked} /></>}{current.interpretation && <article className="evidence-card" id="clarification-review" tabIndex={-1}><h3>Here’s what I understood</h3><p>{current.interpretation.confirmed_context_summary}</p>{answerChangedSinceReview && <p className="notice">You changed your answer. Review it again before confirming.</p>}</article>}<div className="cv-actions">{(current.status !== "review_ready" || answerChangedSinceReview) && <button type="button" className="button-secondary" disabled={locked || !answerCanBeReviewed} onClick={() => void interpret()}>{pending === "interpret" ? "Reviewing your answer…" : "Review my answer"}</button>}{canConfirm && <button type="button" disabled={Boolean(pending) || locked} onClick={() => void confirmClarification()}>{pending === "confirm-clarification" ? "Saving your confirmation…" : "Yes, that\u2019s right"}</button>}<button type="button" className="button-secondary" disabled={Boolean(pending)} onClick={() => setFollowUpDismissed(true)}>Do this later</button></div></section>}</section>}
      {confirmedTransition && <section className="card" aria-live="polite"><h2>Your new career information is ready for your review.</h2><p>{confirmedTransition.question_text}</p>{confirmedTransition.answer_text && <p>{confirmedTransition.answer_text}</p>}{confirmedTransition.interpretation && <p>{confirmedTransition.interpretation.confirmed_context_summary}</p>}<p className="muted">Your assessment now has new information available for an update. You can review optional Profile changes before updating it.</p></section>}
      <ProfileSuggestions api={api} confirmedClarification={confirmedTransition} enrichment={journey.next_enrichment as Enrichment} activeProfileDraft={journey.active_profile_draft} onResolved={() => { void refreshDependentAuthority(); }} />
    </>}
  </main>;
}

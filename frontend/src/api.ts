// The default is deliberately same-origin so both the Compose nginx proxy and
// the Vite development proxy can route browser requests to the API. Native
// deployments can still set VITE_API_BASE_URL at build time.
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public readonly status: number, message: string, public readonly detail: string | null = null) { super(message); }
}

export type User = { id: string; email: string; created_at: string };
export type PasswordPolicy = {
  version: number; min_length: number; max_length: number;
  common_passwords_rejected: boolean; composition_requirements: string[];
};

export type SemanticOperation =
  | "cv_semantic_extraction"
  | "candidate_adviser"
  | "job_extraction"
  | "requirement_matching"
  | "career_alignment"
  | "job_relevance"
  | "job_archetype"
  | "agentic_discovery"
  | "application_drafting";
export type ReasoningEffort = "none" | "low" | "medium" | "high" | "xhigh" | "max";
export type PreferenceActivity = "inherited" | "active" | "inactive_provider_mismatch" | "inactive_invalid" | "unsupported";
export type AiModelCapability = { id: string; label: string; structured_output: boolean; reasoning_efforts: ReasoningEffort[] };
export type AiModelCatalog = { provider: string; user_overrides_supported: boolean; models: AiModelCapability[] };
export type AiOperationOverride = { model?: string | null; reasoning_effort?: ReasoningEffort | null };
export type AiPreferences = { default_model: string | null; default_reasoning_effort: ReasoningEffort | null; operation_overrides: Partial<Record<SemanticOperation, AiOperationOverride>> };
export type AiEffectiveOperation = { model: string; reasoning_effort: ReasoningEffort | null; inherited_model: boolean; inherited_reasoning_effort: boolean };
export type AiSettings = {
  revision: number; provider: string; user_overrides_supported: boolean; persisted_override_provider: string | null;
  overrides_active: boolean; preference_activity: PreferenceActivity; preferences: AiPreferences;
  effective: Record<SemanticOperation, AiEffectiveOperation>;
};
export type AiSettingsReplace = AiPreferences & { expected_revision: number };

export const SEMANTIC_OPERATION_ORDER: ReadonlyArray<{ id: SemanticOperation; label: string }> = [
  { id: "cv_semantic_extraction", label: "CV extraction" },
  { id: "candidate_adviser", label: "Career Adviser" },
  { id: "job_extraction", label: "Job extraction" },
  { id: "requirement_matching", label: "Requirement matching" },
  { id: "career_alignment", label: "Career alignment" },
  { id: "job_relevance", label: "Job relevance" },
  { id: "job_archetype", label: "Job archetype" },
  { id: "agentic_discovery", label: "Agentic discovery" },
  { id: "application_drafting", label: "Application drafting" },
];
export type Profile = {
  id: string; user_id: string; created_at: string; updated_at: string;
  display_name?: string | null; headline?: string | null; current_role?: string | null;
  location?: string | null; summary?: string | null; career_goal?: string | null;
  job_search_criteria?: string | null; preferred_email?: string | null; phone?: string | null;
  linkedin_url?: string | null; github_url?: string | null; portfolio_url?: string | null;
};
export type EditableProfile = {
  display_name: string | null; headline: string | null; current_role: string | null;
  location: string | null; summary: string | null; career_goal: string | null;
  job_search_criteria: string | null; preferred_email: string | null; phone: string | null;
  linkedin_url: string | null; github_url: string | null; portfolio_url: string | null;
};
export type CandidateEligibility = {
  work_authorisation: string[];
  security_clearances: string[];
  locations: string[];
};
export type Employment = { employer: string; title: string; start_date: string | null; end_date: string | null; location: string | null; description: string };
export type Education = { institution: string; qualification: string; field_of_study: string | null; description: string };
export type Credential = { name: string; credential_type: string; issuer: string | null; issued_date: string | null; expiry_date: string | null; status: string | null; description: string };
export type CandidateSkill = { name: string; category: string | null };
export type CandidateProject = { name: string; description: string; skills: string[] };
export type CandidateAchievement = { text: string };
export type CVEvidenceProvenance = { document_sha256: string; segment_ids: string[]; source_kind: "cv" };
export type CVEvidenceDraft = { evidence_type: string; title: string; text: string; skills: string[]; provenance: CVEvidenceProvenance[] };
export type CandidateCVData = {
  employment: Employment[];
  education: Education[];
  credentials: Credential[];
  skills: CandidateSkill[];
  projects: CandidateProject[];
  achievements: CandidateAchievement[];
  evidence: CVEvidenceDraft[];
};
export type EditableStructuredProfile = Omit<CandidateCVData, "evidence">;
export type ProfileRevisionState = "draft" | "review_ready" | "confirmed" | "discarded";
export type RevisionAuthority = "profile" | "structured";
export type ProfileRevision = {
  id: string;
  state: ProfileRevisionState;
  revision: number;
  proposed_profile: EditableProfile | null;
  proposed_structured: EditableStructuredProfile | null;
  structured_comparisons: StructuredProfileChangeComparison[];
  changed_authorities: RevisionAuthority[];
  stale_authorities: RevisionAuthority[];
  created_at: string;
  updated_at: string;
  confirmed_at: string | null;
  discarded_at: string | null;
};
export type ProfileRevisionAction = { expected_revision: number };
export type ProfileRevisionPatch = ProfileRevisionAction & {
  proposed_profile?: EditableProfile | null;
  proposed_structured?: EditableStructuredProfile | null;
};
export type StructuredProfileSection = "employment" | "education" | "credentials" | "skills" | "projects" | "achievements";
export type StructuredItemRelationship = "new" | "reinforcement" | "refinement" | "conflict" | "ambiguous";
export type StructuredProfileItem = Employment | Education | Credential | CandidateSkill | CandidateProject | CandidateAchievement;
export type StructuredProfileItemMatch = { fingerprint: string; item: StructuredProfileItem };
export type StructuredProfileComparisonResult = {
  section: StructuredProfileSection; relationship: StructuredItemRelationship;
  incoming_item: StructuredProfileItem; incoming_fingerprint: string;
  candidate_matches: StructuredProfileItemMatch[]; target_fingerprint: string | null; current_item: StructuredProfileItem | null;
};
export type StructuredProfileChangeComparison = { item_key: string; comparison: StructuredProfileComparisonResult };
export type AdviserProfileProposalOperation = "add" | "replace_exact";
type AdviserProfileProposalUpdateBase =
  | { operation: "add"; target_fingerprint: null }
  | { operation: "replace_exact"; target_fingerprint: string };
export type AdviserProfileProposalUpdate = AdviserProfileProposalUpdateBase & (
  | { section: "employment"; item: Employment }
  | { section: "education"; item: Education }
  | { section: "credentials"; item: Credential }
  | { section: "skills"; item: CandidateSkill }
  | { section: "projects"; item: CandidateProject }
  | { section: "achievements"; item: CandidateAchievement }
);
export type AdviserProfileProposalState = "pending" | "rejected" | "transferred";
export type AdviserProfileProposal = {
  id: string;
  state: AdviserProfileProposalState;
  revision: number;
  source_clarification_id: string;
  source_assessment_fingerprint: string;
  original_update: AdviserProfileProposalUpdate;
  proposed_update: AdviserProfileProposalUpdate;
  created_at: string;
  updated_at: string;
  rejected_at: string | null;
  transferred_at: string | null;
  transferred_profile_revision_id: string | null;
  comparison: StructuredProfileComparisonResult | null;
  comparison_base_fingerprint: string | null;
  overlap_resolution: AdviserProfileProposalOverlapResolution | null;
  overlap_resolution_stale: boolean | null;
};
export type AdviserProfileProposalOverlapResolution = {
  action: "add_as_new"; base_structured_fingerprint: string; incoming_fingerprint: string;
  candidate_fingerprints: string[]; resolved_at: string;
};
export type AdviserProfileProposalOverlapResolutionRequest = {
  expected_revision: number; expected_comparison_base_fingerprint: string; action: "add_as_new";
};
export type AdviserProfileProposalGenerationRead = { proposals: AdviserProfileProposal[] };
export type AdviserProfileProposalTransferRead = {
  proposal: AdviserProfileProposal;
  profile_revision: ProfileRevision;
};
export type CareerEvidence = { evidence_id: string; title: string; text: string; skills: string[]; evidence_type: string };
export type AdviserInsight = { text: string; source_references: Array<{ source_type: string; reference: string }> };
export type CandidateAdviserAssessment = {
  professional_positioning: AdviserInsight;
  transferable_strengths: AdviserInsight[];
  development_gaps: AdviserInsight[];
  role_hypotheses: AdviserInsight[];
  transition_assessment: AdviserInsight;
  open_questions: AdviserInsight[];
  career_strategy_summary: AdviserInsight;
  job_search_strategy_summary: AdviserInsight;
};
export type AdviserIntake = {
  career_direction: string;
  work_preferences: string[];
  constraints: string[];
  self_assessment: string[];
  motivations: string[];
  tradeoffs: string[];
  eligibility: CandidateEligibility;
  updated_at: string;
};
export type CandidateReadiness = {
  structured_profile_available: boolean;
  ready_for_candidate_context: boolean;
  evidence_materialization_status: "not_applicable" | "complete" | "incomplete";
  expected_evidence_count: number;
  materialized_evidence_count: number;
  missing_evidence_count: number;
  stale_evidence_count: number;
  latest_cv_draft_state: "uploaded" | "review_ready" | "confirmed" | null;
};
export type AdviserReadStatus = "not_available" | "review_ready" | "confirmed" | "stale" | "unavailable";
export type CanonicalCandidateReadSnapshot = {
  profile: Profile | null;
  structured_profile: CandidateCVData | null;
  active_evidence: CareerEvidence[];
  adviser_intake: AdviserIntake | null;
  eligibility: CandidateEligibility;
  adviser_assessment: CandidateAdviserAssessment | null;
  adviser_assessment_status: AdviserReadStatus;
  readiness: CandidateReadiness;
};
export type OnboardingStatus = {
  profile_exists: boolean; candidate_context_ready: boolean;
  latest_cv_draft: { id: string; state: "uploaded" | "review_ready" | "confirmed"; created_at: string; updated_at: string } | null;
  adviser: { intake_exists: boolean; assessment_status: "review_ready" | "confirmed" | "stale" | null; confirmed_clarification_count: number };
};

export type PostingRecency = { legitimacy: "high_confidence" | "proceed_with_caution" | "unknown"; reasoning: string };
export type JobRequirement = { text: string; importance: "essential" | "desirable" | "unspecified"; category: string; source_text?: string | null };
export type JobProfile = {
  title?: string | null; company?: string | null; location?: string | null; work_arrangement?: string | null;
  seniority?: string | null; salary?: string | null; employment_type?: string | null; application_deadline?: string | null;
  responsibilities: string[]; requirements: JobRequirement[]; technical_skills: string[]; domain_knowledge: string[];
  security_requirements: string[]; work_authorization_requirements: string[];
};
export type RankedJobOpportunity = {
  job: { title: string; company?: string | null; location?: string | null; work_arrangement?: string | null; employment_type?: string | null; url: string; posted_at?: string | null };
  relevance: { relevant: boolean; score: number; reasoning: string };
  archetype: { archetype: string; reasoning: string };
  fit_assessment: { fit_score: number; essential_score?: number | null; desirable_score?: number | null; strengths: number[]; hard_blockers: number[]; gaps: Array<{ requirement_index: number; requirement: JobRequirement; gap_type: string; severity: string; reason: string }> };
  career_assessment: { career_alignment_score: number; confidence: string; dimensions: Array<{ dimension: string; score: number; reasoning: string }>; strategic_strengths: string[]; strategic_tradeoffs: string[]; reasoning: string };
  recommendation_assessment: { recommendation: "apply" | "consider" | "skip"; fit_score: number; career_alignment_score: number; career_alignment_confidence: string; rule_id: string; reasoning: string; key_strengths: string[]; key_tradeoffs: string[]; hard_blockers: number[] };
  legitimacy: PostingRecency;
  rank: number;
  job_profile?: JobProfile | null;
  requirement_matches: Array<{ requirement_index: number; requirement: JobRequirement; match_type: string; score: number; evidence_ids: string[]; evidence_refs?: ApplicationSourceRef[]; reasoning: string }>;
};
export type UserOpportunitySummary = {
  evaluation_id: string; discovered_job_id: string; recommendation: "apply" | "consider" | "skip"; title: string;
  company: string | null; location: string | null; work_arrangement: string | null; fit_score: number;
  career_alignment_score: number; career_alignment_confidence: string; relevance_score: number; archetype: string;
  url: string; posting_recency: PostingRecency;
};
export type BoundedResponse<T> = { items: T[]; limit: number; truncated: boolean };
export type DiscoveryRunStatus = "running" | "completed" | "partial_failed" | "failed";
export type DiscoveryRunSummary = {
  id: string; status: DiscoveryRunStatus; run_input: Record<string, unknown>; funnel: Record<string, number>;
  failure_summary: Record<string, number>; started_at: string; completed_at: string | null;
};
export type DiscoveryRunJobSummary = {
  discovered_job_id: string; evaluation_id: string | null;
  outcome: "newly_evaluated" | "reused_evaluation" | "not_actionable" | "presemantic_filtered" | "outside_semantic_budget" | "semantic_rejected" | "outside_deep_analysis_budget" | "analysis_failed";
  failure_stage: string | null; failure_kind: string | null; opportunity: UserOpportunitySummary | null;
};
export type DiscoveryRunDetail = DiscoveryRunSummary & { jobs: DiscoveryRunJobSummary[] };
export type SemanticRuntimeOperation =
  | "cv_semantic_extraction" | "candidate_adviser" | "job_extraction" | "requirement_matching"
  | "career_alignment" | "job_relevance" | "job_archetype" | "agentic_discovery" | "application_drafting";
export type SemanticRuntimeReasoningEffort = "none" | "low" | "medium" | "high" | "xhigh" | "max";
export type SemanticRuntimeOperationAttribution = { model: string; reasoning_effort: SemanticRuntimeReasoningEffort | null };
export type SemanticRuntimeAttribution =
  | { status: "available"; provider: string; operations: Partial<Record<SemanticRuntimeOperation, SemanticRuntimeOperationAttribution>> }
  | { status: "not_used" | "legacy_unavailable"; provider: null; operations: Record<string, never> };
export type CVIngestionState = "uploaded" | "review_ready" | "confirmed";
export type CVDocumentProvenance = { filename: string; media_type: string; document_sha256: string; segment_ids: string[] };
export type ExtractedCVSegment = { segment_id: string; text: string; page_number: number | null; heading: string | null };
export type ExtractedCVDocument = { provenance: CVDocumentProvenance; segments: ExtractedCVSegment[] };
export type CVIngestionDraft = {
  id: string; state: CVIngestionState; documents: ExtractedCVDocument[]; merged: CandidateCVData | null;
  created_at: string; updated_at: string; runtime_attribution: SemanticRuntimeAttribution | null;
};
export type CVIngestionHistoryItem = {
  id: string; state: CVIngestionState; created_at: string; updated_at: string; filenames: string[]; document_count: number;
};
export type CVIngestionHistoryRead = { items: CVIngestionHistoryItem[]; limit: number; truncated: boolean };
export type CVOverlapResolutionAction = "replace_current" | "keep_current" | "add_as_new" | "skip_incoming";
export type CVOverlapResolution = { item_key: string; action: CVOverlapResolutionAction; target_fingerprint: string | null };
export type CVOverlapReviewItem = {
  item_key: string; section: StructuredProfileSection; incoming_item: StructuredProfileItem; incoming_fingerprint: string;
  relationship: StructuredItemRelationship; candidate_matches: StructuredProfileItemMatch[]; target_fingerprint: string | null;
  current_item: StructuredProfileItem | null; saved_resolution: CVOverlapResolution | null; resolution_required: boolean;
};
export type CVOverlapIncomingDuplicate = {
  section: StructuredProfileSection; first_item_key: string; duplicate_item_key: string;
  first_item: StructuredProfileItem; duplicate_item: StructuredProfileItem;
};
export type CVOverlapReviewRead = {
  draft_id: string; revision: number; base_structured_fingerprint: string; draft_fingerprint: string;
  stale: boolean; items: CVOverlapReviewItem[]; incoming_duplicates: CVOverlapIncomingDuplicate[];
};
export type CVOverlapReviewPatch = {
  expected_review_revision: number; expected_base_structured_fingerprint: string;
  expected_draft_fingerprint: string; resolutions: CVOverlapResolution[];
};
export type CVStructuredProfileSource = {
  kind: "cv"; source_id: string; filenames: string[]; source_state: string | null;
  source_created_at: string | null; source_updated_at: string | null; available: boolean;
};
export type ManualProfileStructuredSource = { kind: "manual_profile"; source_id: string; confirmed_at: string | null; available: boolean };
export type CandidateAdviserStructuredSource = {
  kind: "candidate_adviser"; source_id: string; source_clarification_id: string | null;
  clarification_question: string | null; proposal_item: StructuredProfileItem | null; transferred_at: string | null; available: boolean;
};
export type StructuredProfileResolvedSource = CVStructuredProfileSource | ManualProfileStructuredSource | CandidateAdviserStructuredSource;
export type StructuredProfileLineageEventRead = {
  event_id: string; section: StructuredProfileSection; item_fingerprint: string; item: StructuredProfileItem;
  source_kind: "cv" | "manual_profile" | "candidate_adviser"; relationship: StructuredItemRelationship;
  predecessor_fingerprint: string | null; predecessor_item: StructuredProfileItem | null; created_at: string;
  source: StructuredProfileResolvedSource;
};
export type StructuredProfileHistoricalLineageEventRead = { depth: number; lineage_event: StructuredProfileLineageEventRead };
export type StructuredProfileCurrentItemProvenanceRead = {
  section: StructuredProfileSection; item_index: number; item: StructuredProfileItem; item_fingerprint: string;
  direct_events: StructuredProfileLineageEventRead[]; history: StructuredProfileHistoricalLineageEventRead[];
  source_history_available: boolean;
};
export type StructuredProfileProvenanceRead = { items: StructuredProfileCurrentItemProvenanceRead[] };
export type HistoricalRunJobDetail = { discovered_job_id: string; evaluation_id: string | null; outcome: DiscoveryRunJobSummary["outcome"]; failure_stage: string | null; failure_kind: string | null; opportunity: RankedJobOpportunity | null; runtime_attribution: SemanticRuntimeAttribution | null };
export type InboxProvenance = { runtime: string; source_ref: string | null; discovered_via: string | null; imported_at: string };
export type InboxSummary = {
  discovered_job_id: string; title: string; company: string | null; location: string | null;
  work_arrangement: string | null; employment_type: string | null; url: string; state: "new" | "updated" | "unchanged" | "inactive";
  verification_status: "verified" | "unverified"; verification_reason: string | null; actionable: boolean;
  first_seen_at: string; last_seen_at: string; provenance: InboxProvenance[]; provenance_count: number;
};
export type CreateDiscoveryRun = {
  query: { keywords: string[]; locations: string[]; remote_ok: boolean | null; companies: string[]; excluded_companies: string[]; excluded_title_terms: string[]; employment_types: string[]; max_results: number };
  discovered_job_ids: string[]; max_semantic_candidates: number; max_full_analyses: number; min_relevance_score: number;
};
export type DiscoveryScheduleSpec = { cadence: "daily" | "weekly"; timezone: string; local_time: string; weekdays: number[] };
export type StructuredAtsScheduleConfig = {
  enabled: boolean; companies: string[]; providers: string[]; all_resolved_sources: boolean; max_sources: number; max_results: number;
};
export type AgenticWebScheduleConfig = {
  enabled: boolean; country: string; max_search_queries: number; max_search_results_per_query: number; max_pages_to_open: number; max_discovered_jobs: number;
};
export type DiscoveryScheduleAcquisition = { structured_ats: StructuredAtsScheduleConfig; agentic_web: AgenticWebScheduleConfig };
export type DiscoveryScheduleEvaluation = { max_semantic_candidates: number; max_full_analyses: number; min_relevance_score: number };
export type DiscoveryScheduleQuery = CreateDiscoveryRun["query"];
export type DiscoveryScheduleRead = {
  id: string; name: string; enabled: boolean; schedule: DiscoveryScheduleSpec; query: DiscoveryScheduleQuery;
  acquisition: DiscoveryScheduleAcquisition; evaluation: DiscoveryScheduleEvaluation; next_run_at: string | null; last_execution_at: string | null;
};
export type DiscoveryScheduleCreate = Omit<DiscoveryScheduleRead, "id" | "next_run_at" | "last_execution_at">;
export type DiscoverySchedulePatch = Partial<Omit<DiscoveryScheduleCreate, "schedule" | "query" | "acquisition" | "evaluation">> & {
  schedule?: DiscoveryScheduleSpec; query?: DiscoveryScheduleQuery; acquisition?: DiscoveryScheduleAcquisition; evaluation?: DiscoveryScheduleEvaluation;
};
export type ScheduledExecutionStatus = "running" | "completed" | "partial_failed" | "failed" | "skipped";
export type ScheduledExecutionRead = {
  id: string;
  trigger_kind: "manual" | "scheduled";
  scheduled_for: string | null;
  status: ScheduledExecutionStatus;
  config_snapshot: {
    schedule: DiscoveryScheduleSpec;
    query: DiscoveryScheduleQuery;
    acquisition: DiscoveryScheduleAcquisition;
    evaluation: DiscoveryScheduleEvaluation;
  };
  discovery_run_id: string | null;
  acquisition_summary: Record<string, number>;
  failure_summary: Record<string, number>;
  started_at: string;
  completed_at: string | null;
};
export type LLMConfigurationCheck = { ready: boolean };
export type DiscoveryRunCreatedJob = Omit<DiscoveryRunJobSummary, "opportunity"> & { opportunity: RankedJobOpportunity | null };
export type DiscoveryRunCreated = DiscoveryRunSummary & { search_input_fingerprint: string; candidate_evaluation_fingerprint: string; evaluation_contract_fingerprint: string; jobs: DiscoveryRunCreatedJob[] };

export type JobWorkspace = {
  job: {
    id: string; title: string; company: string | null; location: string | null; url: string;
    description: string | null; posted_at: string | null; work_arrangement: string | null;
    employment_type: string | null; detail_authority: string; verification_status: string;
    verification_reason: string | null; state: string; actionable: boolean;
    first_seen_at: string; last_seen_at: string; last_changed_at: string;
  };
  provenance: { items: Array<{ id: string; runtime: string; source_ref: string | null; discovered_via: string | null; imported_at: string }>; limit: number; truncated: boolean };
  current_fit: { status: "current" | "none" | "unavailable"; reason: string | null; evaluation: JobWorkspaceEvaluation | null };
  evaluations: { items: JobWorkspaceEvaluation[]; limit: number; truncated: boolean };
};
export type JobWorkspaceEvaluation = {
  id: string; created_at: string; applicability: "current" | "historical" | "unknown";
  opportunity: RankedJobOpportunity; runtime_attribution: SemanticRuntimeAttribution | null;
};

export type ApplicationSourceRef = { source_type: string; source_ref: string; value?: string | null };
export type ApplicationEvidenceSnapshotStatus = "available" | "legacy_unavailable";
export type ApplicationEvidenceSource = ApplicationSourceRef & { text: string };
export type ApplicationPreparationReview = {
  preparation_id: string;
  evidence_snapshot_status: ApplicationEvidenceSnapshotStatus;
  evidence_sources: ApplicationEvidenceSource[];
};
export type ApplicationRequirementMatch = {
  requirement_index: number;
  requirement: JobRequirement;
  match_type: string;
  score: number;
  evidence_ids: string[];
  evidence_refs?: ApplicationSourceRef[];
  reasoning: string;
};
export type ApplicationPreparation = {
  id: string;
  target: {
    source_kind: "discovered_job" | "job_text" | "job_url"; canonical_discovered_job_id?: string | null;
    public_url?: string | null; title: string; company?: string | null; location?: string | null;
    work_arrangement?: string | null; employment_type?: string | null;
    job_profile: JobProfile; requirement_matches: ApplicationRequirementMatch[]; job_content_hash: string;
  };
  identity: {
    display_name: string; email: string; phone?: string | null; location?: string | null;
    linkedin_url?: string | null; github_url?: string | null; portfolio_url?: string | null;
  };
  preparation_input_fingerprint: string; preparation_contract_fingerprint: string;
  runtime_attribution: SemanticRuntimeAttribution;
  result: {
    cv: {
      professional_summary: string; summary_source_refs: ApplicationSourceRef[]; key_skills: string[];
      roles: Array<{ employer: string; title: string; start_date?: string | null; end_date?: string | null; location?: string | null; bullets: Array<{ text: string; source_refs: ApplicationSourceRef[]; priority: number }> }>;
      selected_projects: Array<{ name: string; text: string; source_refs: ApplicationSourceRef[]; priority: number }>;
      education: string[]; credentials: string[];
    };
    cover_letter: { body: string; source_refs: ApplicationSourceRef[] } | null;
    answers: Array<{ question: string; status: "drafted" | "unsupported"; answer?: string | null; source_refs: ApplicationSourceRef[] }>;
    layout_status: "fit" | "overflow"; target_pages: number; actual_pdf_pages: number;
  };
  created_at: string;
};

export type ApplicationPrepareTarget =
  | { discovered_job_id: string; job_text?: never; job_url?: never }
  | { job_text: string; discovered_job_id?: never; job_url?: never }
  | { job_url: string; discovered_job_id?: never; job_text?: never };

export type ApplicationPrepareRequest = {
  target: ApplicationPrepareTarget;
  target_pages: 1 | 2 | 3;
  include_cover_letter: boolean;
  application_questions: string[];
};

export type ApplicationTrackingStatus = "prepared" | "applied" | "interview" | "rejected" | "offer" | "withdrawn";
export type ApplicationTrackingEvent = { revision: number; from_status: ApplicationTrackingStatus | null; to_status: ApplicationTrackingStatus; recorded_at: string };
export type ApplicationTrackingTarget = {
  preparation_id: string; preparation_created_at: string; source_kind: string; title: string;
  company: string | null; location: string | null; public_url: string | null;
};
export type ApplicationTracking = {
  id: string; preparation_id: string; target: ApplicationTrackingTarget;
  current_status: ApplicationTrackingStatus; revision: number; created_at: string; updated_at: string;
  events: ApplicationTrackingEvent[];
};
export type ApplicationTrackingListItem = Omit<ApplicationTracking, "events">;

export class SessionApi {
  private epoch = 0;
  private controllers = new Set<AbortController>();
  constructor(private token: string | null, private onAuthenticated401: () => void) {}
  replaceToken(token: string | null) { this.epoch += 1; this.token = token; this.controllers.forEach((controller) => controller.abort()); this.controllers.clear(); }
  async request<T>(path: string, init: RequestInit = {}, authenticated = true): Promise<T> {
    const epoch = this.epoch; const controller = new AbortController(); this.controllers.add(controller);
    try {
      const headers = new Headers(init.headers);
      if (authenticated && this.token) headers.set("Authorization", `Bearer ${this.token}`);
      // FormData owns its multipart boundary.  Setting a JSON content type here
      // would make the browser send an unreadable CV upload.
      if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }
      const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers, signal: controller.signal });
      if (authenticated && response.status === 401 && epoch === this.epoch) this.onAuthenticated401();
      if (!response.ok) {
        let detail: string | null = null;
        try {
          const body = await response.clone().json() as { detail?: unknown };
          if (typeof body.detail === "string") detail = body.detail;
        } catch { /* Error responses may have no JSON body. */ }
        throw new ApiError(response.status, "Request could not be completed.", detail);
      }
      const value = await response.json() as T;
      if (epoch !== this.epoch) throw new DOMException("Superseded auth session", "AbortError");
      return value;
    } finally { this.controllers.delete(controller); }
  }

  async requestBlob(path: string): Promise<{ blob: Blob; filename: string | null }> {
    const epoch = this.epoch; const controller = new AbortController(); this.controllers.add(controller);
    try {
      const headers = new Headers();
      if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
      const response = await fetch(`${API_BASE_URL}${path}`, { headers, signal: controller.signal });
      if (epoch !== this.epoch) throw new DOMException("Superseded auth session", "AbortError");
      if (response.status === 401 && epoch === this.epoch) this.onAuthenticated401();
      if (!response.ok) throw new ApiError(response.status, "Request could not be completed.");
      const blob = await response.blob();
      if (epoch !== this.epoch) throw new DOMException("Superseded auth session", "AbortError");
      return { blob, filename: response.headers.get("Content-Disposition") ? dispositionFilename(response.headers.get("Content-Disposition")!) : null };
    } finally { this.controllers.delete(controller); }
  }
}

function dispositionFilename(value: string): string | null {
  const extended = value.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)?.[1]?.trim().replace(/^"|"$/g, "");
  const ordinary = value.match(/filename\s*=\s*(?:"([^"]*)"|([^;]+))/i);
  const candidate = extended ? (() => { try { return decodeURIComponent(extended); } catch { return extended; } })() : ordinary?.[1] ?? ordinary?.[2]?.trim();
  return candidate || null;
}

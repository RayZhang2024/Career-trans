import type { SemanticRuntimeAttribution, SemanticRuntimeOperation, SemanticRuntimeReasoningEffort } from "./api";

const operationOrder: SemanticRuntimeOperation[] = [
  "job_relevance", "job_archetype", "job_extraction", "requirement_matching",
  "career_alignment", "application_drafting", "cv_semantic_extraction",
  "candidate_adviser", "agentic_discovery",
];
const operationLabels: Record<SemanticRuntimeOperation, string> = {
  cv_semantic_extraction: "CV semantic extraction", candidate_adviser: "Career Adviser",
  job_extraction: "Job extraction", requirement_matching: "Requirement matching",
  career_alignment: "Career alignment", job_relevance: "Job relevance",
  job_archetype: "Job archetype", agentic_discovery: "Agentic discovery",
  application_drafting: "Application drafting",
};
const effortLabels: Record<SemanticRuntimeReasoningEffort, string> = {
  none: "No reasoning", low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Maximum",
};
type Boundary = "cv" | "evaluation" | "preparation";

export function RuntimeAttributionPanel({ attribution, boundary }: { attribution: SemanticRuntimeAttribution | null; boundary: Boundary }) {
  let message: string | null = null;
  if (attribution === null) message = boundary === "cv"
    ? "AI runtime attribution will be recorded when this CV is interpreted."
    : "No persisted successful evaluation exists for this run outcome, so runtime attribution is not available at this boundary.";
  else if (attribution.status === "not_used") message = "No semantic model was used to create this saved CV interpretation.";
  else if (attribution.status === "legacy_unavailable") message = boundary === "cv"
    ? "AI runtime attribution was not recorded for this historical CV draft."
    : boundary === "evaluation"
      ? "AI runtime attribution was not recorded for this historical evaluation."
      : "AI runtime attribution was not recorded for this historical preparation.";

  return <section className="card application-section runtime-attribution" aria-label="AI runtime used">
    <h2>AI runtime used</h2>
    {message ? <p className="muted">{message}</p> : attribution?.status === "available" && <>
      <p><strong>Provider:</strong> {attribution.provider}</p>
      <dl className="detail-grid">{operationOrder.filter((operation) => attribution.operations[operation]).map((operation) => {
        const value = attribution.operations[operation];
        if (!value) return null;
        return <div key={operation}><dt>{operationLabels[operation]}</dt><dd>{value.model} · {value.reasoning_effort === null ? "Provider default reasoning" : effortLabels[value.reasoning_effort]}</dd></div>;
      })}</dl>
    </>}
  </section>;
}

import type {
  ApplicationEvidenceSnapshotStatus,
  ApplicationPreparation,
  ApplicationPreparationReview,
  ApplicationRequirementMatch,
  ApplicationSourceRef,
} from "./api";

export type CitationUsage = Map<string, string[]>;
export type RequirementEvidenceState = "admitted_cited" | "admitted_uncited" | "not_admitted" | "admission_unknown";
export type RequirementEvidenceReview = {
  ref: ApplicationSourceRef;
  historicalText: string | null;
  historicalTextSource: "snapshot" | "match_value" | "unavailable";
  cited: boolean;
  state: RequirementEvidenceState;
};
export type RequirementReviewRow = {
  index: number;
  requirement: ApplicationPreparation["target"]["job_profile"]["requirements"][number];
  match: ApplicationRequirementMatch | null;
  inconsistent: boolean;
  evidence: RequirementEvidenceReview[];
  aggregate: string;
};

export const evidenceIdentity = (ref: Pick<ApplicationSourceRef, "source_type" | "source_ref">) =>
  JSON.stringify([ref.source_type, ref.source_ref]);

/** Citations are derived only from the immutable result returned by detail. */
export function collectFinalCitationUsage(value: ApplicationPreparation): CitationUsage {
  const usage: CitationUsage = new Map();
  const add = (refs: ApplicationSourceRef[], surface: string) => {
    for (const ref of refs) {
      const key = evidenceIdentity(ref);
      const locations = usage.get(key) ?? [];
      if (!locations.includes(surface)) locations.push(surface);
      usage.set(key, locations);
    }
  };
  add(value.result.cv.summary_source_refs, "CV professional summary");
  value.result.cv.roles.forEach((role) => role.bullets.forEach((bullet) => add(bullet.source_refs, `CV bullet · ${role.title} at ${role.employer}`)));
  value.result.cv.selected_projects.forEach((project) => add(project.source_refs, `Selected project · ${project.name}`));
  if (value.result.cover_letter) add(value.result.cover_letter.source_refs, "Cover letter");
  value.result.answers.forEach((answer) => {
    if (answer.status === "drafted") add(answer.source_refs, `Application answer · ${answer.question}`);
  });
  return usage;
}

function refsForMatch(match: ApplicationRequirementMatch): ApplicationSourceRef[] {
  if (match.evidence_refs?.length) return match.evidence_refs;
  return (match.evidence_ids ?? []).map((source_ref) => ({ source_type: "career_evidence", source_ref }));
}

export function buildRequirementReview(
  value: ApplicationPreparation,
  review: ApplicationPreparationReview,
  citations: CitationUsage = collectFinalCitationUsage(value),
): { rows: RequirementReviewRow[]; invalidIndexCount: number } {
  const requirements = value.target.job_profile.requirements;
  const byIndex = new Map<number, ApplicationRequirementMatch[]>();
  let invalidIndexCount = 0;
  for (const match of value.target.requirement_matches ?? []) {
    const index = match.requirement_index;
    if (!Number.isInteger(index) || index < 0 || index >= requirements.length) {
      invalidIndexCount += 1;
      continue;
    }
    byIndex.set(index, [...(byIndex.get(index) ?? []), match]);
  }

  const snapshot = new Map(review.evidence_sources.map((source) => [evidenceIdentity(source), source.text]));
  const rows = requirements.map((requirement, index): RequirementReviewRow => {
    const matches = byIndex.get(index) ?? [];
    const inconsistent = matches.length > 1;
    const match = !inconsistent ? matches[0] ?? null : null;
    const refs = match ? refsForMatch(match).filter((ref, refIndex, all) => all.findIndex((item) => evidenceIdentity(item) === evidenceIdentity(ref)) === refIndex) : [];
    const evidence = refs.map((ref): RequirementEvidenceReview => {
      const key = evidenceIdentity(ref);
      const cited = citations.has(key);
      const hasSnapshotText = review.evidence_snapshot_status === "available" && snapshot.has(key);
      const historicalText = hasSnapshotText ? snapshot.get(key)! : ref.value ?? null;
      const historicalTextSource = hasSnapshotText ? "snapshot" : historicalText !== null ? "match_value" : "unavailable";
      let state: RequirementEvidenceState;
      if (review.evidence_snapshot_status === "legacy_unavailable") state = "admission_unknown";
      else if (snapshot.has(key)) state = cited ? "admitted_cited" : "admitted_uncited";
      else state = "not_admitted";
      return { ref, historicalText, historicalTextSource, cited, state };
    });

    let aggregate: string;
    if (review.evidence_snapshot_status === "legacy_unavailable") {
      aggregate = evidence.length ? "Drafting-context admission was not stored for this older preparation" : "No matched evidence recorded";
    } else if (!evidence.length) {
      aggregate = "No matched evidence recorded";
    } else if (evidence.some((item) => item.state === "admitted_cited")) {
      aggregate = "At least one matched evidence source is cited in prepared materials";
    } else if (evidence.some((item) => item.state === "admitted_uncited")) {
      aggregate = "Matched evidence was available to drafting, but none of the admitted sources is cited in prepared materials";
    } else {
      aggregate = "Matched evidence was not included in the bounded preparation context";
    }
    return { index, requirement, match, inconsistent, evidence, aggregate };
  });
  return { rows, invalidIndexCount };
}

export function snapshotStatusLabel(status: ApplicationEvidenceSnapshotStatus): string {
  return status === "available" ? "Preparation-time evidence snapshot available" : "Bounded drafting evidence was not stored for this older preparation";
}

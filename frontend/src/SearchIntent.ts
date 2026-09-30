import type { DiscoveryScheduleQuery } from "./api";

export type SearchIntentRemotePolicy = "any" | "exclude_remote" | "legacy_true";

export type SearchIntent = {
  themes: string[];
  locations: string[];
  remotePolicy: SearchIntentRemotePolicy;
  excludedCompanies: string[];
  excludedTitleTerms: string[];
  employmentTypes: string[];
  compatibility: {
    companies: string[];
    maxResults: number;
  };
};

export type SearchIntentField = "themes" | "locations" | "remotePolicy" | "excludedCompanies" | "excludedTitleTerms" | "employmentTypes";

export const emptySearchIntent = (): SearchIntent => ({
  themes: [], locations: [], remotePolicy: "any", excludedCompanies: [], excludedTitleTerms: [], employmentTypes: [],
  compatibility: { companies: [], maxResults: 50 },
});

const canonicalLines = (items: readonly string[]) => items.flatMap((item) => item.split(/\r?\n/).map((line) => line.trim()).filter(Boolean));

export function canonicalizeSearchIntent(intent: SearchIntent): SearchIntent {
  return {
    ...intent,
    themes: canonicalLines(intent.themes), locations: canonicalLines(intent.locations),
    excludedCompanies: canonicalLines(intent.excludedCompanies), excludedTitleTerms: canonicalLines(intent.excludedTitleTerms),
    employmentTypes: canonicalLines(intent.employmentTypes),
    compatibility: intent.compatibility,
  };
}

export function searchIntentFromQuery(query: DiscoveryScheduleQuery): SearchIntent {
  return canonicalizeSearchIntent({
    themes: [...query.keywords], locations: [...query.locations],
    remotePolicy: query.remote_ok === true ? "legacy_true" : query.remote_ok === false ? "exclude_remote" : "any",
    excludedCompanies: [...query.excluded_companies], excludedTitleTerms: [...query.excluded_title_terms], employmentTypes: [...query.employment_types],
    compatibility: { companies: [...query.companies], maxResults: query.max_results },
  });
}

export function searchIntentToQuery(intent: SearchIntent): DiscoveryScheduleQuery {
  const canonical = canonicalizeSearchIntent(intent);
  return {
    keywords: [...canonical.themes], locations: [...canonical.locations],
    remote_ok: canonical.remotePolicy === "exclude_remote" ? false : canonical.remotePolicy === "legacy_true" ? true : null,
    companies: [...canonical.compatibility.companies], excluded_companies: [...canonical.excludedCompanies], excluded_title_terms: [...canonical.excludedTitleTerms],
    employment_types: [...canonical.employmentTypes], max_results: canonical.compatibility.maxResults,
  };
}

export function searchIntentEquals(left: SearchIntent, right: SearchIntent): boolean {
  return JSON.stringify(canonicalizeSearchIntent(left)) === JSON.stringify(canonicalizeSearchIntent(right));
}

export function searchIntentChangedFields(left: SearchIntent, right: SearchIntent): SearchIntentField[] {
  const canonicalLeft = canonicalizeSearchIntent(left);
  const canonicalRight = canonicalizeSearchIntent(right);
  return (["themes", "locations", "remotePolicy", "excludedCompanies", "excludedTitleTerms", "employmentTypes"] as const).filter((field) => JSON.stringify(canonicalLeft[field]) !== JSON.stringify(canonicalRight[field]));
}

export function mergeSearchIntentIntoQuery(query: DiscoveryScheduleQuery, intent: SearchIntent, dirty: ReadonlySet<SearchIntentField>): DiscoveryScheduleQuery {
  const edited = searchIntentToQuery(intent);
  const next = {
    ...query,
    keywords: [...query.keywords], locations: [...query.locations], companies: [...query.companies],
    excluded_companies: [...query.excluded_companies], excluded_title_terms: [...query.excluded_title_terms], employment_types: [...query.employment_types],
  };
  if (dirty.has("themes")) next.keywords = edited.keywords;
  if (dirty.has("locations")) next.locations = edited.locations;
  if (dirty.has("remotePolicy")) next.remote_ok = edited.remote_ok;
  if (dirty.has("excludedCompanies")) next.excluded_companies = edited.excluded_companies;
  if (dirty.has("excludedTitleTerms")) next.excluded_title_terms = edited.excluded_title_terms;
  if (dirty.has("employmentTypes")) next.employment_types = edited.employment_types;
  return next;
}

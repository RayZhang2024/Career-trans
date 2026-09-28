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

export function searchIntentFromQuery(query: DiscoveryScheduleQuery): SearchIntent {
  return {
    themes: [...query.keywords], locations: [...query.locations],
    remotePolicy: query.remote_ok === true ? "legacy_true" : query.remote_ok === false ? "exclude_remote" : "any",
    excludedCompanies: [...query.excluded_companies], excludedTitleTerms: [...query.excluded_title_terms], employmentTypes: [...query.employment_types],
    compatibility: { companies: [...query.companies], maxResults: query.max_results },
  };
}

export function searchIntentToQuery(intent: SearchIntent): DiscoveryScheduleQuery {
  return {
    keywords: [...intent.themes], locations: [...intent.locations],
    remote_ok: intent.remotePolicy === "exclude_remote" ? false : intent.remotePolicy === "legacy_true" ? true : null,
    companies: [...intent.compatibility.companies], excluded_companies: [...intent.excludedCompanies], excluded_title_terms: [...intent.excludedTitleTerms],
    employment_types: [...intent.employmentTypes], max_results: intent.compatibility.maxResults,
  };
}

export function searchIntentEquals(left: SearchIntent, right: SearchIntent): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function searchIntentChangedFields(left: SearchIntent, right: SearchIntent): SearchIntentField[] {
  return (["themes", "locations", "remotePolicy", "excludedCompanies", "excludedTitleTerms", "employmentTypes"] as const).filter((field) => JSON.stringify(left[field]) !== JSON.stringify(right[field]));
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

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

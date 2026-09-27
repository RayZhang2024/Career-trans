import { describe, expect, it } from "vitest";
import { searchIntentEquals, searchIntentFromQuery, searchIntentToQuery } from "./SearchIntent";

describe("SearchIntent adapter", () => {
  it("round-trips every persisted query field, including compatibility fields", () => {
    const query = {
      keywords: ["AI, ML platform", "FDE"], locations: ["London, United Kingdom"], remote_ok: true as const,
      companies: ["Hidden query company"], excluded_companies: ["Avoid Co"], excluded_title_terms: ["Intern, Junior"],
      employment_types: ["Full-time"], max_results: 73,
    };
    expect(searchIntentToQuery(searchIntentFromQuery(query))).toEqual(query);
  });

  it("preserves compatibility fields when the user edits search themes", () => {
    const intent = searchIntentFromQuery({ keywords: ["AI"], locations: [], remote_ok: false, companies: ["Example Co"], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 42 });
    const edited = { ...intent, themes: ["Applied AI"] };
    expect(searchIntentToQuery(edited)).toMatchObject({ keywords: ["Applied AI"], remote_ok: false, companies: ["Example Co"], max_results: 42 });
  });

  it("uses null for the canonical any-remote policy and compares intents deterministically", () => {
    const intent = searchIntentFromQuery({ keywords: ["AI"], locations: [], remote_ok: null, companies: [], excluded_companies: [], excluded_title_terms: [], employment_types: [], max_results: 50 });
    expect(searchIntentToQuery(intent).remote_ok).toBeNull();
    expect(searchIntentEquals(intent, searchIntentFromQuery(searchIntentToQuery(intent)))).toBe(true);
  });
});

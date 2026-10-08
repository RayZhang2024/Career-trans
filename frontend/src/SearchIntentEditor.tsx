import { useEffect, useRef, useState } from "react";
import type { SearchIntent, SearchIntentRemotePolicy } from "./SearchIntent";
import { canonicalizeSearchIntent } from "./SearchIntent";

type EditableList = "themes" | "locations" | "excludedCompanies" | "excludedTitleTerms" | "employmentTypes";
const editableLists: EditableList[] = ["themes", "locations", "excludedCompanies", "excludedTitleTerms", "employmentTypes"];
const rawFromIntent = (intent: SearchIntent): Record<EditableList, string> => Object.fromEntries(editableLists.map((field) => [field, intent[field].join("\n")])) as Record<EditableList, string>;

export function SearchIntentEditor({ intent, onChange, disabled = false, idPrefix = "search-intent", collapseAdvanced = false, userFriendly = false }: { intent: SearchIntent; onChange: (next: SearchIntent) => void; disabled?: boolean; idPrefix?: string; collapseAdvanced?: boolean; userFriendly?: boolean }) {
  const [raw, setRaw] = useState(() => rawFromIntent(intent));
  const previousCanonical = useRef(rawFromIntent(intent));
  const emittedCanonical = useRef<Partial<Record<EditableList, string>>>({});
  useEffect(() => {
    const updates: Partial<Record<EditableList, string>> = {};
    for (const field of editableLists) {
      const incoming = intent[field].join("\n");
      if (emittedCanonical.current[field] === incoming) {
        delete emittedCanonical.current[field];
      } else if (previousCanonical.current[field] !== incoming) {
        updates[field] = incoming;
      }
      previousCanonical.current[field] = incoming;
    }
    if (Object.keys(updates).length) setRaw((current) => ({ ...current, ...updates }));
  }, [intent]);
  const updateList = (field: EditableList, value: string) => {
    setRaw((current) => ({ ...current, [field]: value }));
    const next = canonicalizeSearchIntent({ ...intent, [field]: value.split(/\r?\n/) });
    emittedCanonical.current[field] = next[field].join("\n");
    onChange(next);
  };
  const fieldId = (field: string) => `${idPrefix}-${field}`;
  return <fieldset className="search-intent-editor" disabled={disabled}>
    <legend>{userFriendly ? "Search preferences" : "Search intent"}</legend>
    <p className="muted">{userFriendly ? "Your preferences help find and sort vacancies. They do not decide eligibility or prove fit." : "Search intent is prioritisation and discovery context, not candidate eligibility or evidence supporting a fit claim."}</p>
    <label htmlFor={fieldId("themes")}>{userFriendly ? "What roles are you looking for? (one per line)" : "Prioritisation themes (one per line)"}</label>
    <textarea id={fieldId("themes")} value={raw.themes} onChange={(event) => updateList("themes", event.target.value)} />
    {!userFriendly && <p className="muted">Themes guide search and prioritisation. They are not exact web-search terms or eligibility filters, and do not prove candidate capability.</p>}
    <label htmlFor={fieldId("locations")}>{userFriendly ? "Where? (one per line)" : "Locations (search criteria, not eligibility)"}</label>
    <textarea id={fieldId("locations")} value={raw.locations} onChange={(event) => updateList("locations", event.target.value)} />
    <label htmlFor={fieldId("remote-policy")}>{userFriendly ? "Remote work" : "Remote policy"}</label>
    <select id={fieldId("remote-policy")} value={intent.remotePolicy} onChange={(event) => onChange({ ...intent, remotePolicy: event.target.value as SearchIntentRemotePolicy })}>
      <option value="any">No remote restriction</option>
      <option value="exclude_remote">Exclude remote jobs</option>
      {intent.remotePolicy === "legacy_true" && <option value="legacy_true">No remote restriction — legacy stored value preserved</option>}
    </select>
    {collapseAdvanced ? <details className="search-advanced"><summary>Advanced search settings</summary>{advancedFields()}</details> : advancedFields()}
    {!userFriendly && <p className="muted">Locations and employment types are search criteria, not proof of work authorisation, security clearance, or other eligibility. Eligibility still comes from canonical candidate state.</p>}
  </fieldset>;

  function advancedFields() {
    return <>
      <label htmlFor={fieldId("excluded-companies")}>Excluded companies (one per line)</label>
      <textarea id={fieldId("excluded-companies")} value={raw.excludedCompanies} onChange={(event) => updateList("excludedCompanies", event.target.value)} />
      <label htmlFor={fieldId("excluded-title-terms")}>Excluded title terms (one per line)</label>
      <textarea id={fieldId("excluded-title-terms")} value={raw.excludedTitleTerms} onChange={(event) => updateList("excludedTitleTerms", event.target.value)} />
      <label htmlFor={fieldId("employment-types")}>Employment types (one per line)</label>
      <textarea id={fieldId("employment-types")} value={raw.employmentTypes} onChange={(event) => updateList("employmentTypes", event.target.value)} />
    </>;
  }
}

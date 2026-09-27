import type { SearchIntent, SearchIntentRemotePolicy } from "./SearchIntent";

type EditableList = "themes" | "locations" | "excludedCompanies" | "excludedTitleTerms" | "employmentTypes";
const splitLines = (value: string) => value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);

export function SearchIntentEditor({ intent, onChange, disabled = false, idPrefix = "search-intent" }: { intent: SearchIntent; onChange: (next: SearchIntent) => void; disabled?: boolean; idPrefix?: string }) {
  const updateList = (field: EditableList, value: string) => onChange({ ...intent, [field]: splitLines(value) });
  const fieldId = (field: string) => `${idPrefix}-${field}`;
  return <fieldset className="search-intent-editor" disabled={disabled}>
    <legend>Search intent</legend>
    <p className="muted">Search intent is prioritisation and discovery context, not candidate eligibility or evidence supporting a fit claim.</p>
    <label htmlFor={fieldId("themes")}>Prioritisation themes (one per line)</label>
    <textarea id={fieldId("themes")} value={intent.themes.join("\n")} onChange={(event) => updateList("themes", event.target.value)} />
    <p className="muted">Themes guide search and prioritisation. They are not exact web-search terms or eligibility filters, and do not prove candidate capability.</p>
    <label htmlFor={fieldId("locations")}>Locations (search criteria, not eligibility)</label>
    <textarea id={fieldId("locations")} value={intent.locations.join("\n")} onChange={(event) => updateList("locations", event.target.value)} />
    <label htmlFor={fieldId("remote-policy")}>Remote policy</label>
    <select id={fieldId("remote-policy")} value={intent.remotePolicy} onChange={(event) => onChange({ ...intent, remotePolicy: event.target.value as SearchIntentRemotePolicy })}>
      <option value="any">No remote restriction</option>
      <option value="exclude_remote">Exclude remote jobs</option>
      {intent.remotePolicy === "legacy_true" && <option value="legacy_true">No remote restriction — legacy stored value preserved</option>}
    </select>
    <label htmlFor={fieldId("excluded-companies")}>Excluded companies (one per line)</label>
    <textarea id={fieldId("excluded-companies")} value={intent.excludedCompanies.join("\n")} onChange={(event) => updateList("excludedCompanies", event.target.value)} />
    <label htmlFor={fieldId("excluded-title-terms")}>Excluded title terms (one per line)</label>
    <textarea id={fieldId("excluded-title-terms")} value={intent.excludedTitleTerms.join("\n")} onChange={(event) => updateList("excludedTitleTerms", event.target.value)} />
    <label htmlFor={fieldId("employment-types")}>Employment types (one per line)</label>
    <textarea id={fieldId("employment-types")} value={intent.employmentTypes.join("\n")} onChange={(event) => updateList("employmentTypes", event.target.value)} />
    <p className="muted">Locations and employment types are search criteria, not proof of work authorisation, security clearance, or other eligibility. Eligibility still comes from canonical candidate state.</p>
  </fieldset>;
}

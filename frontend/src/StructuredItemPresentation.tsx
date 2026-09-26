import type { StructuredItemRelationship, StructuredProfileItem, StructuredProfileSection } from "./api";

const relationshipLabels: Record<StructuredItemRelationship, string> = {
  new: "New information", reinforcement: "Same fact", refinement: "More detailed version",
  conflict: "Conflicting information", ambiguous: "Possible duplicate",
};
export function relationshipLabel(value: StructuredItemRelationship): string { return relationshipLabels[value]; }
export function structuredItemLabel(section: StructuredProfileSection, item: StructuredProfileItem): string {
  const value = item as unknown as Record<string, unknown>;
  switch (section) {
    case "employment": return [value.title, value.employer].filter(Boolean).join(" at ") || "employment item";
    case "education": return [value.qualification, value.institution].filter(Boolean).join(" at ") || "education item";
    case "credentials": return String(value.name ?? "credential");
    case "skills": return String(value.name ?? "skill");
    case "projects": return String(value.name ?? "project");
    case "achievements": return String(value.text ?? "achievement");
  }
}

export function StructuredItemPresentation({ section, item, label }: { section: StructuredProfileSection; item: StructuredProfileItem; label?: string }) {
  const value = item as unknown as Record<string, unknown>;
  const lines: string[] = [];
  switch (section) {
    case "employment": lines.push([value.title, value.employer].filter(Boolean).join(" at "), [value.start_date, value.end_date, value.location].filter(Boolean).join(" · "), String(value.description ?? "")); break;
    case "education": lines.push([value.qualification, value.institution].filter(Boolean).join(" — "), String(value.field_of_study ?? ""), String(value.description ?? "")); break;
    case "credentials": lines.push(String(value.name ?? ""), [String(value.credential_type ?? "").replaceAll("_", " "), value.issuer, value.status, value.issued_date, value.expiry_date].filter(Boolean).join(" · "), String(value.description ?? "")); break;
    case "skills": lines.push([value.name, value.category].filter(Boolean).join(" · ")); break;
    case "projects": lines.push(String(value.name ?? ""), String(value.description ?? ""), Array.isArray(value.skills) ? `Skills: ${(value.skills as string[]).join(", ")}` : ""); break;
    case "achievements": lines.push(String(value.text ?? "")); break;
  }
  return <div className="structured-item-presentation">{label && <h4>{label}</h4>}{lines.filter(Boolean).map((line, index) => index === 0 ? <p key={index}><strong>{line}</strong></p> : <p key={index}>{line}</p>)}</div>;
}

import { Link } from "react-router-dom";

type SettingsSection = "security" | "ai" | "discovery";

const sections: ReadonlyArray<{ id: SettingsSection; label: string; path: string }> = [
  { id: "security", label: "Account Security", path: "/settings/security" },
  { id: "ai", label: "AI Models", path: "/settings/ai" },
  { id: "discovery", label: "Job Discovery", path: "/settings/discovery" },
];

export function SettingsNavigation({ current }: { current: SettingsSection }) {
  return <nav className="settings-tabs" aria-label="Settings">
    {sections.map((section) => <Link
      key={section.id}
      aria-current={section.id === current ? "page" : undefined}
      to={section.path}
    >{section.label}</Link>)}
  </nav>;
}

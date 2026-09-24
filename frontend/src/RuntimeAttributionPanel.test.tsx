import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RuntimeAttributionPanel } from "./RuntimeAttributionPanel";
import type { SemanticRuntimeAttribution } from "./api";

describe("historical runtime attribution", () => {
  it("renders the stored provider, unknown historical model, and provider-default effort without catalog lookup", () => {
    const attribution: SemanticRuntimeAttribution = {
      status: "available",
      provider: "openai",
      operations: {
        job_relevance: { model: "retired-model-id", reasoning_effort: null },
        job_archetype: { model: "gpt-5.6-sol", reasoning_effort: "high" },
      },
    };
    render(<RuntimeAttributionPanel attribution={attribution} boundary="evaluation" />);
    expect(screen.getByRole("heading", { name: "AI runtime used" })).toBeInTheDocument();
    expect(screen.getByText("Provider:")).toBeInTheDocument();
    expect(screen.getByText("retired-model-id · Provider default reasoning")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.6-sol · High")).toBeInTheDocument();
    expect(screen.queryByText(/AI Models|current catalog/i)).not.toBeInTheDocument();
  });

  it("distinguishes structured-only CV interpretation from legacy attribution", () => {
    render(<RuntimeAttributionPanel attribution={{ status: "not_used", provider: null, operations: {} }} boundary="cv" />);
    expect(screen.getByText("No semantic model was used to create this saved CV interpretation.")).toBeInTheDocument();

    render(<RuntimeAttributionPanel attribution={{ status: "legacy_unavailable", provider: null, operations: {} }} boundary="cv" />);
    expect(screen.getByText("AI runtime attribution was not recorded for this historical CV draft.")).toBeInTheDocument();
  });

  it("renders the saved CV extraction runtime without consulting current settings", () => {
    render(<RuntimeAttributionPanel attribution={{ status: "available", provider: "openai", operations: { cv_semantic_extraction: { model: "retired-cv-model", reasoning_effort: "xhigh" } } }} boundary="cv" />);
    expect(screen.getByText("CV semantic extraction")).toBeInTheDocument();
    expect(screen.getByText("retired-cv-model · Extra high")).toBeInTheDocument();
  });

  it("shows uploaded CV attribution as not yet established", () => {
    render(<RuntimeAttributionPanel attribution={null} boundary="cv" />);
    expect(screen.getByText("AI runtime attribution will be recorded when this CV is interpreted.")).toBeInTheDocument();
  });

  it("renders only operations persisted on a preparation", () => {
    const attribution: SemanticRuntimeAttribution = {
      status: "available",
      provider: "openai",
      operations: { application_drafting: { model: "archived-model", reasoning_effort: "none" } },
    };
    const { container } = render(<RuntimeAttributionPanel attribution={attribution} boundary="preparation" />);
    expect(screen.getByText("Application drafting")).toBeInTheDocument();
    expect(screen.getByText("archived-model · No reasoning")).toBeInTheDocument();
    expect(container.textContent).not.toContain("Job extraction");
    expect(container.textContent).not.toContain("Requirement matching");
    expect(container.textContent).not.toContain("Career alignment");
  });

  it("renders explicit legacy and no-evaluation boundary messages", () => {
    const legacy: SemanticRuntimeAttribution = { status: "legacy_unavailable", provider: null, operations: {} };
    render(<RuntimeAttributionPanel attribution={legacy} boundary="preparation" />);
    expect(screen.getByText("AI runtime attribution was not recorded for this historical preparation.")).toBeInTheDocument();
    render(<RuntimeAttributionPanel attribution={legacy} boundary="evaluation" />);
    expect(screen.getByText("AI runtime attribution was not recorded for this historical evaluation.")).toBeInTheDocument();
    render(<RuntimeAttributionPanel attribution={null} boundary="evaluation" />);
    expect(screen.getByText(/No persisted successful evaluation exists/)).toBeInTheDocument();
  });
});

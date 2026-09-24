import type {
  AiModelCatalog,
  AiPreferences,
  AiSettings,
  AiSettingsReplace,
  ReasoningEffort,
  SemanticOperation,
} from "./api";
import { SEMANTIC_OPERATION_ORDER } from "./api";

export const REASONING_EFFORT_ORDER: readonly ReasoningEffort[] = ["none", "low", "medium", "high", "xhigh", "max"];
export const REASONING_EFFORT_LABELS: Record<ReasoningEffort, string> = {
  none: "No reasoning",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Maximum",
};

export const emptyAiPreferences = (): AiPreferences => ({
  default_model: null,
  default_reasoning_effort: null,
  operation_overrides: {},
});

export function canonicalizeAiPreferences(value: AiPreferences): AiPreferences {
  const operation_overrides: AiPreferences["operation_overrides"] = {};
  for (const { id } of SEMANTIC_OPERATION_ORDER) {
    const override = value.operation_overrides[id];
    if (!override) continue;
    const model = override.model ?? null;
    const reasoning_effort = override.reasoning_effort ?? null;
    if (model === null && reasoning_effort === null) continue;
    operation_overrides[id] = {
      ...(model !== null ? { model } : {}),
      ...(reasoning_effort !== null ? { reasoning_effort } : {}),
    };
  }
  return {
    default_model: value.default_model ?? null,
    default_reasoning_effort: value.default_reasoning_effort ?? null,
    operation_overrides,
  };
}

export function canonicalPreferencesKey(value: AiPreferences): string {
  return JSON.stringify(canonicalizeAiPreferences(value));
}

export function preferencesEqual(left: AiPreferences, right: AiPreferences): boolean {
  return canonicalPreferencesKey(left) === canonicalPreferencesKey(right);
}

export function replacePayload(preferences: AiPreferences, expected_revision: number): AiSettingsReplace {
  return { ...canonicalizeAiPreferences(preferences), expected_revision };
}

export function catalogSettingsCoherent(catalog: AiModelCatalog, settings: AiSettings): boolean {
  return catalog.provider === settings.provider
    && catalog.user_overrides_supported === settings.user_overrides_supported;
}

export function modelLabel(catalog: AiModelCatalog, modelId: string): string {
  return catalog.models.find((model) => model.id === modelId)?.label ?? modelId;
}

export function effortOptionsFor(
  catalog: AiModelCatalog,
  preferences: AiPreferences,
  operation?: SemanticOperation,
): ReasoningEffort[] {
  const operationModel = operation ? preferences.operation_overrides[operation]?.model ?? null : null;
  const selectedModel = operationModel ?? preferences.default_model;
  if (selectedModel !== null) {
    return [...(catalog.models.find((model) => model.id === selectedModel)?.reasoning_efforts ?? [])];
  }

  const available = new Set(catalog.models.flatMap((model) => model.reasoning_efforts));
  return REASONING_EFFORT_ORDER.filter((effort) => available.has(effort));
}

export type DraftMatrixValidation = {
  invalidOperations: SemanticOperation[];
  invalidDefaultModel: boolean;
  invalidDefaultEffort: boolean;
};

export function validateDraftMatrix(catalog: AiModelCatalog, preferences: AiPreferences): DraftMatrixValidation {
  const invalid = new Set<SemanticOperation>();
  const catalogModelIds = new Set(catalog.models.map((model) => model.id));
  const invalidDefaultModel = preferences.default_model !== null && !catalogModelIds.has(preferences.default_model);
  let invalidDefaultEffort = false;

  for (const { id } of SEMANTIC_OPERATION_ORDER) {
    const override = preferences.operation_overrides[id];
    const modelId = override?.model ?? preferences.default_model;
    const effort = override?.reasoning_effort ?? preferences.default_reasoning_effort;
    if (override?.model && !catalogModelIds.has(override.model)) invalid.add(id);
    if (preferences.default_model && !catalogModelIds.has(preferences.default_model) && !override?.model) invalid.add(id);
    if (modelId === null || effort === null) continue;

    const capability = catalog.models.find((model) => model.id === modelId);
    // Deployment-inherited model identity is intentionally unknown to the browser.
    if (!capability) continue;
    if (!capability.reasoning_efforts.includes(effort)) {
      invalid.add(id);
      if (override?.reasoning_effort == null && preferences.default_reasoning_effort !== null) {
        invalidDefaultEffort = true;
      }
    }
  }

  return {
    invalidOperations: SEMANTIC_OPERATION_ORDER.map(({ id }) => id).filter((id) => invalid.has(id)),
    invalidDefaultModel,
    invalidDefaultEffort,
  };
}

export function isEditableSettings(settings: AiSettings, catalog: AiModelCatalog): boolean {
  return catalog.user_overrides_supported
    && settings.user_overrides_supported
    && settings.preference_activity !== "inactive_provider_mismatch"
    && settings.preference_activity !== "unsupported";
}

export function hasResettablePreferences(settings: AiSettings): boolean {
  return settings.preference_activity === "inactive_invalid"
    || !preferencesEqual(settings.preferences, emptyAiPreferences());
}

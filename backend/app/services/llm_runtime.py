"""Pure runtime preference resolution and deterministic capability catalog."""

from dataclasses import dataclass
import re
from typing import Mapping

from app.core.config import Settings
from app.schemas.ai_settings import (
    EffectiveOperationRead,
    ModelCapabilityRead,
    PreferenceActivity,
    ReasoningEffort,
    SemanticOperation,
    UserAiPreferences,
)


RUNTIME_OPERATION_TO_SETTING: Mapping[str, SemanticOperation] = {
    "cv_evidence_extraction": SemanticOperation.CV_SEMANTIC_EXTRACTION,
    "candidate_adviser": SemanticOperation.CANDIDATE_ADVISER,
    "candidate_adviser_clarification": SemanticOperation.CANDIDATE_ADVISER,
    "job_extraction": SemanticOperation.JOB_EXTRACTION,
    "requirement_matching": SemanticOperation.REQUIREMENT_MATCHING,
    "career_alignment": SemanticOperation.CAREER_ALIGNMENT,
    "job_relevance": SemanticOperation.JOB_RELEVANCE,
    "job_archetype": SemanticOperation.JOB_ARCHETYPE,
    "search_strategy_generation": SemanticOperation.AGENTIC_DISCOVERY,
    "web_vacancy_extraction": SemanticOperation.AGENTIC_DISCOVERY,
    "application_cv_drafting": SemanticOperation.APPLICATION_DRAFTING,
    "application_cover_letter": SemanticOperation.APPLICATION_DRAFTING,
    "application_answer_drafting": SemanticOperation.APPLICATION_DRAFTING,
}

JOB_EVALUATION_OPERATIONS = (
    SemanticOperation.JOB_RELEVANCE,
    SemanticOperation.JOB_ARCHETYPE,
    SemanticOperation.JOB_EXTRACTION,
    SemanticOperation.REQUIREMENT_MATCHING,
    SemanticOperation.CAREER_ALIGNMENT,
)
PREPARATION_OPERATIONS = (
    SemanticOperation.JOB_EXTRACTION,
    SemanticOperation.REQUIREMENT_MATCHING,
    SemanticOperation.CAREER_ALIGNMENT,
    SemanticOperation.APPLICATION_DRAFTING,
)

_ALL_EFFORTS = tuple(ReasoningEffort)
_SELECTABLE = (
    ModelCapabilityRead(id="gpt-5.6-luna", label="GPT-5.6 Luna", structured_output=True, reasoning_efforts=_ALL_EFFORTS),
    ModelCapabilityRead(id="gpt-5.6-terra", label="GPT-5.6 Terra", structured_output=True, reasoning_efforts=_ALL_EFFORTS),
    ModelCapabilityRead(id="gpt-5.6-sol", label="GPT-5.6 Sol", structured_output=True, reasoning_efforts=_ALL_EFFORTS),
)
_LEGACY_STRUCTURED = re.compile(
    r"^(?:(?:gpt-4o(?:-mini)?|gpt-4\.1(?:-mini|-nano)?|gpt-5(?:-mini|-nano)?)(?:-\d{4}-\d{2}-\d{2})?|gpt-5\.6)$"
)


@dataclass(frozen=True, slots=True)
class ModelCapability:
    provider: str
    model_id: str
    structured_output: bool
    reasoning_efforts: frozenset[ReasoningEffort]
    user_selectable: bool


class ModelCapabilityCatalog:
    """Local support metadata; it never queries provider account entitlement."""

    def __init__(self) -> None:
        self._models = {item.id: ModelCapability("openai", item.id, item.structured_output, frozenset(item.reasoning_efforts), True) for item in _SELECTABLE}

    @property
    def selectable_openai(self) -> tuple[ModelCapabilityRead, ...]:
        return _SELECTABLE

    def get(self, provider: str, model: str) -> ModelCapability | None:
        provider = provider.casefold().strip()
        model = model.strip()
        if provider != "openai":
            return None
        known = self._models.get(model)
        if known is not None:
            return known
        # Compatibility path for the exact legacy families accepted at the frozen
        # baseline. They remain deployment-compatible but not user-selectable and
        # intentionally advertise no reasoning-effort support.
        if _LEGACY_STRUCTURED.fullmatch(model):
            return ModelCapability("openai", model, True, frozenset(), False)
        return None


MODEL_CAPABILITIES = ModelCapabilityCatalog()


@dataclass(frozen=True, slots=True)
class ResolvedOperation:
    model: str
    reasoning_effort: ReasoningEffort | None
    inherited_model: bool
    inherited_reasoning_effort: bool
    capability_id: str

    def read(self) -> EffectiveOperationRead:
        return EffectiveOperationRead(
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            inherited_model=self.inherited_model,
            inherited_reasoning_effort=self.inherited_reasoning_effort,
        )


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeSnapshot:
    """Immutable semantic identity for one workflow; revision is metadata only."""

    provider: str
    preference_revision: int
    persisted_override_provider: str | None
    overrides_active: bool
    preference_activity: PreferenceActivity
    operations: tuple[tuple[SemanticOperation, ResolvedOperation], ...]

    def operation(self, operation: SemanticOperation | str) -> ResolvedOperation:
        key = SemanticOperation(operation)
        return dict(self.operations)[key]

    def fingerprint_projection(self, operations: tuple[SemanticOperation, ...]) -> dict[str, object]:
        return {
            "provider": self.provider,
            "operations": {
                operation.value: {
                    "model": self.operation(operation).model,
                    "reasoning_effort": self.operation(operation).reasoning_effort.value if self.operation(operation).reasoning_effort else None,
                }
                for operation in operations
            },
        }

    def effective_read(self) -> dict[SemanticOperation, EffectiveOperationRead]:
        return {key: value.read() for key, value in self.operations}


class RuntimePreferenceError(ValueError):
    pass


def deployment_operation_values(settings: Settings) -> dict[SemanticOperation, tuple[str, ReasoningEffort | None]]:
    return {
        SemanticOperation.CV_SEMANTIC_EXTRACTION: (settings.cv_semantic_extraction_model, settings.cv_semantic_extraction_reasoning_effort),
        SemanticOperation.CANDIDATE_ADVISER: (settings.candidate_adviser_model, settings.candidate_adviser_reasoning_effort),
        SemanticOperation.JOB_EXTRACTION: (settings.job_extraction_model, settings.job_extraction_reasoning_effort),
        SemanticOperation.REQUIREMENT_MATCHING: (settings.requirement_matching_model, settings.requirement_matching_reasoning_effort),
        SemanticOperation.CAREER_ALIGNMENT: (settings.career_alignment_model, settings.career_alignment_reasoning_effort),
        SemanticOperation.JOB_RELEVANCE: (settings.job_relevance_model, settings.job_relevance_reasoning_effort),
        SemanticOperation.JOB_ARCHETYPE: (settings.job_archetype_model, settings.job_archetype_reasoning_effort),
        SemanticOperation.AGENTIC_DISCOVERY: (settings.agentic_discovery_model, settings.agentic_discovery_reasoning_effort),
        SemanticOperation.APPLICATION_DRAFTING: (settings.application_drafting_model, settings.application_drafting_reasoning_effort),
    }


def validate_combination(provider: str, model: str, effort: ReasoningEffort | None, *, user_selectable: bool = False) -> ModelCapability | None:
    provider = provider.casefold().strip()
    if provider == "ollama":
        if effort is not None:
            raise RuntimePreferenceError("The configured provider does not support reasoning-effort selection.")
        if user_selectable:
            raise RuntimePreferenceError("User model overrides are not supported for the configured provider.")
        return None
    capability = MODEL_CAPABILITIES.get(provider, model)
    if capability is None or not capability.structured_output:
        raise RuntimePreferenceError("The selected model is not in the supported Structured Outputs capability catalog.")
    if user_selectable and not capability.user_selectable:
        raise RuntimePreferenceError("The selected model is not available in the user-selectable catalog.")
    if effort is not None and effort not in capability.reasoning_efforts:
        raise RuntimePreferenceError("The selected reasoning effort is not supported for this model.")
    return capability


def validate_preferences(settings: Settings, preferences: UserAiPreferences, provider: str | None = None) -> None:
    provider = (provider or settings.default_llm_provider).casefold().strip()
    if provider != "openai":
        if preferences.default_model or preferences.default_reasoning_effort or preferences.operation_overrides:
            raise RuntimePreferenceError("User semantic overrides are not supported for the configured provider.")
        return
    deployment = deployment_operation_values(settings)
    for operation in SemanticOperation:
        operation_override = preferences.operation_overrides.get(operation)
        model_override = operation_override.model if operation_override else None
        effort_override = operation_override.reasoning_effort if operation_override else None
        model = model_override or preferences.default_model or deployment[operation][0]
        effort = effort_override or preferences.default_reasoning_effort or deployment[operation][1]
        validate_combination(provider, model, effort, user_selectable=bool(model_override or preferences.default_model))


def resolve_runtime_snapshot(
    settings: Settings,
    preferences: UserAiPreferences | None = None,
    *,
    preference_revision: int = 0,
    persisted_override_provider: str | None = None,
    force_invalid: bool = False,
) -> ResolvedRuntimeSnapshot:
    """Pure resolution from settings and already-loaded local preference data."""
    provider = settings.default_llm_provider.casefold().strip()
    stored = preferences or UserAiPreferences()
    activity = PreferenceActivity.INHERITED
    active = False
    effective_preferences = UserAiPreferences()
    user_supported = provider == "openai"

    if preferences is not None:
        if persisted_override_provider != provider:
            activity = PreferenceActivity.INACTIVE_PROVIDER_MISMATCH
        elif force_invalid:
            activity = PreferenceActivity.INACTIVE_INVALID
        elif not user_supported:
            activity = PreferenceActivity.UNSUPPORTED
        else:
            try:
                validate_preferences(settings, stored, provider)
            except RuntimePreferenceError:
                activity = PreferenceActivity.INACTIVE_INVALID
            else:
                activity = PreferenceActivity.ACTIVE if _has_overrides(stored) else PreferenceActivity.INHERITED
                active = _has_overrides(stored)
                effective_preferences = stored

    deployment = deployment_operation_values(settings)
    resolved: list[tuple[SemanticOperation, ResolvedOperation]] = []
    for operation in SemanticOperation:
        operation_override = effective_preferences.operation_overrides.get(operation)
        model_override = operation_override.model if operation_override else None
        effort_override = operation_override.reasoning_effort if operation_override else None
        model = model_override or effective_preferences.default_model or deployment[operation][0]
        effort = effort_override or effective_preferences.default_reasoning_effort or deployment[operation][1]
        capability = validate_combination(provider, model, effort)
        cap_id = f"{provider}:{model}:structured={bool(capability and capability.structured_output)}:effort={','.join(sorted(x.value for x in capability.reasoning_efforts)) if capability else 'deployment'}"
        resolved.append((operation, ResolvedOperation(
            model=model,
            reasoning_effort=effort,
            inherited_model=not bool(model_override or effective_preferences.default_model),
            inherited_reasoning_effort=not bool(effort_override or effective_preferences.default_reasoning_effort),
            capability_id=cap_id,
        )))

    return ResolvedRuntimeSnapshot(
        provider=provider,
        preference_revision=preference_revision,
        persisted_override_provider=persisted_override_provider,
        overrides_active=active,
        preference_activity=activity,
        operations=tuple(resolved),
    )


def _has_overrides(preferences: UserAiPreferences) -> bool:
    return bool(preferences.default_model or preferences.default_reasoning_effort or preferences.operation_overrides)

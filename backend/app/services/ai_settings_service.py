"""Credential-free, user-scoped persistence for semantic runtime preferences."""

import json

from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.user_ai_settings import UserAiSettings
from app.schemas.ai_settings import (
    AiModelCatalogRead,
    AiSettingsRead,
    PreferenceActivity,
    UserAiPreferences,
    UserAiSettingsReplace,
)
from app.services.llm_runtime import (
    MODEL_CAPABILITIES,
    RuntimePreferenceError,
    ResolvedRuntimeSnapshot,
    resolve_runtime_snapshot,
    validate_preferences,
)


class AiSettingsConflictError(RuntimeError):
    """Safe optimistic concurrency failure."""


class AiSettingsValidationError(ValueError):
    """Bounded deterministic preference/capability validation failure."""


class AiSettingsService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def snapshot_for_user(self, user_id: str) -> ResolvedRuntimeSnapshot:
        row = self._session.get(UserAiSettings, user_id)
        if row is None:
            return resolve_runtime_snapshot(self._settings)
        try:
            preferences = _parse_preferences(row.preferences_json)
        except AiSettingsValidationError:
            return resolve_runtime_snapshot(
                self._settings,
                UserAiPreferences(),
                preference_revision=row.revision,
                persisted_override_provider=row.provider,
                force_invalid=True,
            )
        return resolve_runtime_snapshot(
            self._settings,
            preferences,
            preference_revision=row.revision,
            persisted_override_provider=row.provider,
        )

    def catalog(self) -> AiModelCatalogRead:
        provider = self._settings.default_llm_provider.casefold().strip()
        supported = provider == "openai"
        return AiModelCatalogRead(
            provider=provider,
            user_overrides_supported=supported,
            models=MODEL_CAPABILITIES.selectable_openai if supported else (),
        )

    def read(self, user_id: str) -> AiSettingsRead:
        row = self._session.get(UserAiSettings, user_id)
        if row is None:
            snapshot = resolve_runtime_snapshot(self._settings)
            preferences = UserAiPreferences()
            revision = 0
            stored_provider = None
        else:
            invalid = False
            try:
                preferences = _parse_preferences(row.preferences_json)
            except AiSettingsValidationError:
                preferences = UserAiPreferences()
                invalid = True
            revision = row.revision
            stored_provider = row.provider
            snapshot = resolve_runtime_snapshot(
                self._settings,
                preferences,
                preference_revision=revision,
                persisted_override_provider=stored_provider,
                force_invalid=invalid,
            )
        return AiSettingsRead(
            revision=revision,
            provider=snapshot.provider,
            user_overrides_supported=snapshot.provider == "openai",
            persisted_override_provider=stored_provider,
            overrides_active=snapshot.overrides_active,
            preference_activity=snapshot.preference_activity,
            preferences=preferences,
            effective=snapshot.effective_read(),
        )

    def replace(self, user_id: str, payload: UserAiSettingsReplace) -> AiSettingsRead:
        preferences = UserAiPreferences(
            default_model=payload.default_model,
            default_reasoning_effort=payload.default_reasoning_effort,
            operation_overrides=payload.operation_overrides,
        )
        provider = self._settings.default_llm_provider.casefold().strip()
        try:
            validate_preferences(self._settings, preferences, provider)
        except RuntimePreferenceError as exc:
            raise AiSettingsValidationError(str(exc)) from exc

        serialized = _canonical_preferences(preferences)
        now_provider = provider
        if payload.expected_revision == 0:
            row = UserAiSettings(
                user_id=user_id,
                provider=now_provider,
                revision=1,
                preferences_json=serialized,
            )
            try:
                self._session.add(row)
                self._session.commit()
            except IntegrityError as exc:
                self._session.rollback()
                raise AiSettingsConflictError("AI settings changed. Reload the current settings and try again.") from exc
            return self.read(user_id)

        result = self._session.execute(
            update(UserAiSettings)
            .where(UserAiSettings.user_id == user_id, UserAiSettings.revision == payload.expected_revision)
            .values(
                provider=now_provider,
                preferences_json=serialized,
                revision=UserAiSettings.revision + 1,
                updated_at=func.now(),
            )
        )
        if result.rowcount != 1:
            self._session.rollback()
            raise AiSettingsConflictError("AI settings changed. Reload the current settings and try again.")
        self._session.commit()
        return self.read(user_id)


def _parse_preferences(raw: str) -> UserAiPreferences:
    try:
        return UserAiPreferences.model_validate_json(raw)
    except Exception as exc:
        # Stored legacy/corrupt documents remain untouched. Expose only a bounded
        # validation category, never the malformed document or parser details.
        raise AiSettingsValidationError("Persisted AI settings are invalid and cannot be applied.") from exc


def _canonical_preferences(value: UserAiPreferences) -> str:
    return json.dumps(
        value.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )

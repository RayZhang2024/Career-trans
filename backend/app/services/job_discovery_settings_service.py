"""User-scoped browser-search settings, credential precedence, and resolution."""

from dataclasses import dataclass

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.user_job_discovery_settings import UserJobDiscoverySettings, UserTavilyCredential
from app.providers.web_search import (
    BraveWebSearchProvider,
    OpenAIWebSearchProvider,
    TavilyProviderError,
    TavilyWebSearchProvider,
    WebSearchProvider,
)
from app.schemas.job_discovery_settings import (
    EffectiveJobDiscoveryProvider,
    JobDiscoveryCredentialSource,
    JobDiscoveryProvider,
    JobDiscoverySettingsRead,
    JobDiscoverySettingsReplace,
    TavilyConnectionTestRead,
)
from app.services.tavily_credential_encryption import (
    TavilyCredentialEncryption,
    TavilyCredentialEncryptionError,
)


class JobDiscoverySettingsConflictError(RuntimeError):
    """The user's Job Discovery settings revision changed during an update."""


class JobDiscoverySettingsError(RuntimeError):
    """Safe, bounded Job Discovery configuration failure."""


class JobDiscoveryProviderNotReady(JobDiscoverySettingsError):
    def __init__(self, message: str, metadata: dict[str, str | None]) -> None:
        super().__init__(message)
        self.provider_metadata = metadata


@dataclass(frozen=True)
class ResolvedWebSearchProvider:
    provider: WebSearchProvider
    metadata: dict[str, str | None]


class JobDiscoverySettingsService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._encryption = TavilyCredentialEncryption(
            self._settings.tavily_credential_encryption_key
        )

    def read(self, user_id: str) -> JobDiscoverySettingsRead:
        row = self._session.get(UserJobDiscoverySettings, user_id)
        override = JobDiscoveryProvider(row.provider_override) if row and row.provider_override else None
        deployment = self._provider_value(self._settings.agentic_search_provider)
        effective = self._provider_value(override.value if override else self._settings.agentic_search_provider)
        source, configured, usable = self._credential_status(user_id)
        return JobDiscoverySettingsRead(
            revision=row.revision if row else 0,
            provider_override=override,
            deployment_provider=deployment,
            effective_provider=effective,
            tavily_credential_configured=configured,
            tavily_credential_source=source,
            tavily_user_credential_storage_available=self._encryption.configured(),
            tavily_credential_usable=usable,
        )

    def replace(
        self, user_id: str, payload: JobDiscoverySettingsReplace
    ) -> JobDiscoverySettingsRead:
        override = payload.provider_override.value if payload.provider_override else None
        self._advance_revision(user_id, payload.expected_revision, override=override)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise JobDiscoverySettingsConflictError(
                "Job Discovery settings changed. Reload the current settings and try again."
            ) from exc
        return self.read(user_id)

    def save_tavily_credential(self, user_id: str, expected_revision: int, api_key: str) -> JobDiscoverySettingsRead:
        secret = api_key.strip()
        if not secret or len(secret) > 512:
            raise JobDiscoverySettingsError("Enter a valid Tavily API key.")
        nonce, ciphertext, version = self._encryption.encrypt(user_id, secret)
        row = self._advance_revision(user_id, expected_revision)
        credential = self._session.get(UserTavilyCredential, user_id)
        if credential is None:
            self._session.add(
                UserTavilyCredential(
                    user_id=user_id,
                    nonce=nonce,
                    ciphertext=ciphertext,
                    format_version=version,
                )
            )
        else:
            credential.nonce = nonce
            credential.ciphertext = ciphertext
            credential.format_version = version
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise JobDiscoverySettingsConflictError(
                "Job Discovery settings changed. Reload the current settings and try again."
            ) from exc
        return self.read(user_id)

    def remove_tavily_credential(self, user_id: str, expected_revision: int) -> JobDiscoverySettingsRead:
        self._advance_revision(user_id, expected_revision)
        credential = self._session.get(UserTavilyCredential, user_id)
        if credential is not None:
            self._session.delete(credential)
        self._session.commit()
        return self.read(user_id)

    def resolve_provider(self, user_id: str) -> ResolvedWebSearchProvider:
        settings_row = self._session.get(UserJobDiscoverySettings, user_id)
        selected = (
            settings_row.provider_override
            if settings_row and settings_row.provider_override is not None
            else self._settings.agentic_search_provider
        )
        provider = selected.casefold().strip()
        if provider == "disabled":
            raise JobDiscoveryProviderNotReady(
                "Job Discovery web search is disabled in the effective provider settings.",
                {"provider": "disabled", "credential_source": None},
            )
        if provider == "openai":
            if not self._settings.openai_api_key:
                raise JobDiscoveryProviderNotReady(
                    "OpenAI web search is not configured for this deployment.",
                    {"provider": "openai", "credential_source": "deployment"},
                )
            return ResolvedWebSearchProvider(
                OpenAIWebSearchProvider(
                    api_key=self._settings.openai_api_key,
                    model=self._settings.openai_web_search_model,
                ),
                {"provider": "openai", "credential_source": "deployment"},
            )
        if provider == "brave":
            if not self._settings.brave_search_api_key:
                raise JobDiscoveryProviderNotReady(
                    "Brave web search is not configured for this deployment.",
                    {"provider": "brave", "credential_source": "deployment"},
                )
            return ResolvedWebSearchProvider(
                BraveWebSearchProvider(api_key=self._settings.brave_search_api_key),
                {"provider": "brave", "credential_source": "deployment"},
            )
        if provider == "tavily":
            try:
                credential, source = self.resolve_tavily_credential(user_id)
            except JobDiscoverySettingsError as exc:
                raise JobDiscoveryProviderNotReady(
                    str(exc),
                    {"provider": "tavily", "credential_source": "user", "search_depth": "basic"},
                ) from exc
            if not credential or source is None:
                raise JobDiscoveryProviderNotReady(
                    "Tavily is selected, but no usable Tavily API key is configured. Add a key in Job Discovery settings or configure a deployment key.",
                    {"provider": "tavily", "credential_source": "none", "search_depth": "basic"},
                )
            return ResolvedWebSearchProvider(
                TavilyWebSearchProvider(api_key=credential),
                {"provider": "tavily", "credential_source": source.value, "search_depth": "basic"},
            )
        raise JobDiscoveryProviderNotReady(
            "The deployment web-search provider is unsupported. Select a supported provider or ask the administrator to update deployment settings.",
            {"provider": "unsupported", "credential_source": None},
        )

    def resolve_tavily_credential(self, user_id: str) -> tuple[str | None, JobDiscoveryCredentialSource | None]:
        stored = self._session.get(UserTavilyCredential, user_id)
        if stored is not None:
            try:
                value = self._encryption.decrypt(
                    user_id, stored.nonce, stored.ciphertext, stored.format_version
                )
            except TavilyCredentialEncryptionError as exc:
                raise JobDiscoverySettingsError(
                    "The saved Tavily key cannot be opened with the current encryption configuration. Replace it or contact the administrator."
                ) from exc
            return value, JobDiscoveryCredentialSource.USER
        if self._settings.tavily_api_key:
            return self._settings.tavily_api_key, JobDiscoveryCredentialSource.DEPLOYMENT
        return None, None

    def test_tavily_connection(self, user_id: str) -> TavilyConnectionTestRead:
        from app.providers.web_search import TavilyWebSearchProvider

        credential, source = self.resolve_tavily_credential(user_id)
        if credential is None or source is None:
            raise JobDiscoverySettingsError(
                "Tavily is not configured. Save a key or ask the administrator to configure a deployment key."
            )
        test_tavily_connection(TavilyWebSearchProvider(api_key=credential))
        return TavilyConnectionTestRead(
            credential_source=source,
            message="Tavily connection test succeeded. This test used one Basic Search request.",
        )

    def _advance_revision(
        self,
        user_id: str,
        expected_revision: int,
        *,
        override: str | None | object = ...,
    ) -> UserJobDiscoverySettings:
        row = self._session.get(UserJobDiscoverySettings, user_id)
        if expected_revision == 0:
            if row is not None:
                raise JobDiscoverySettingsConflictError(
                    "Job Discovery settings changed. Reload the current settings and try again."
                )
            values: dict[str, object] = {
                "user_id": user_id,
                "revision": 1,
                "provider_override": None if override is ... else override,
            }
            row = UserJobDiscoverySettings(**values)
            self._session.add(row)
            try:
                self._session.flush()
            except IntegrityError as exc:
                self._session.rollback()
                raise JobDiscoverySettingsConflictError(
                    "Job Discovery settings changed. Reload the current settings and try again."
                ) from exc
            return row
        values = {"revision": UserJobDiscoverySettings.revision + 1, "updated_at": func.now()}
        if override is not ...:
            values["provider_override"] = override
        result = self._session.execute(
            update(UserJobDiscoverySettings)
            .where(
                UserJobDiscoverySettings.user_id == user_id,
                UserJobDiscoverySettings.revision == expected_revision,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            self._session.rollback()
            raise JobDiscoverySettingsConflictError(
                "Job Discovery settings changed. Reload the current settings and try again."
            )
        row = self._session.get(UserJobDiscoverySettings, user_id)
        assert row is not None
        return row

    def _credential_status(
        self, user_id: str
    ) -> tuple[JobDiscoveryCredentialSource | None, bool, bool]:
        stored = self._session.get(UserTavilyCredential, user_id)
        if stored is not None:
            try:
                usable = bool(
                    self._encryption.decrypt(
                        user_id, stored.nonce, stored.ciphertext, stored.format_version
                    )
                )
            except TavilyCredentialEncryptionError:
                usable = False
            return JobDiscoveryCredentialSource.USER, True, usable
        if self._settings.tavily_api_key:
            return JobDiscoveryCredentialSource.DEPLOYMENT, True, True
        return None, False, False

    @staticmethod
    def _provider_value(value: str) -> EffectiveJobDiscoveryProvider:
        normalized = value.casefold().strip()
        try:
            return EffectiveJobDiscoveryProvider(normalized)
        except ValueError:
            return EffectiveJobDiscoveryProvider.UNSUPPORTED


def test_tavily_connection(provider: WebSearchProvider) -> None:
    """Use one harmless Basic query; never persists discovery state."""
    try:
        provider.search("Tavily API connection test", 1)
    except TavilyProviderError as exc:
        raise JobDiscoverySettingsError(str(exc)) from exc
    except Exception as exc:
        raise JobDiscoverySettingsError("Tavily connection test failed.") from exc

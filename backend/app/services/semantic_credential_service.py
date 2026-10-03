"""User-scoped semantic credential lifecycle and ephemeral runtime resolution."""

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.user_semantic_credential import UserSemanticCredential
from app.providers.llm import (
    EnvironmentCredentialResolver,
    LLMProviderConfig,
    LLMProviderFactory,
    SemanticCredentialRejectedError,
    SemanticModelAccessError,
    SemanticStructuredOutputModelError,
    SemanticStructuredOutputSchemaError,
    SemanticProviderConfigurationError,
    SemanticProviderRequestError,
    SemanticProviderRateLimitError,
    SemanticProviderUnavailableError,
)
from app.services.semantic_credential_encryption import SemanticCredentialEncryption, SemanticCredentialEncryptionError

OPENAI_PROVIDER = "openai"


class SemanticCredentialConflictError(RuntimeError):
    pass


class SemanticCredentialConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SemanticCredentialStatus:
    provider: str
    policy: str
    storage_available: bool
    user_credential_configured: bool
    user_credential_state: str
    credential_revision: int
    effective_source: str
    display_identity: str | None
    deployment_credential_configured: bool


@dataclass(frozen=True)
class SemanticCredentialConnectionTest:
    connected: bool
    category: str
    message: str


class UserSemanticCredentialResolver:
    """Workflow-scoped resolver; holds decrypted key only in this ephemeral object."""

    def __init__(self, session: Session, user_id: str, settings: Settings) -> None:
        self._session = session
        self._user_id = user_id
        self._settings = settings
        self._resolved = False
        self._credential: str | None = None
        self._error: SemanticCredentialConfigurationError | None = None

    def credential_for(self, provider: str) -> str | None:
        normalized = provider.casefold().strip()
        if normalized != OPENAI_PROVIDER:
            return None
        if self._error is not None:
            raise self._error
        if not self._resolved:
            try:
                self._credential = self._resolve(normalized)
                self._resolved = True
            except SemanticCredentialConfigurationError as exc:
                self._error = exc
                raise
        return self._credential

    def _resolve(self, provider: str) -> str | None:
        policy = self._settings.semantic_credential_policy
        if self._settings.default_llm_provider.casefold().strip() != provider:
            return None
        if policy == "deployment_only":
            if not self._settings.openai_api_key:
                raise SemanticCredentialConfigurationError("OpenAI deployment credentials are not configured for this policy.")
            return self._settings.openai_api_key

        row = self._session.get(UserSemanticCredential, (self._user_id, provider))
        if row is not None:
            try:
                return SemanticCredentialEncryption(self._settings.semantic_credential_encryption_key).decrypt(
                    self._user_id, provider, row.nonce, row.ciphertext, row.format_version
                )
            except SemanticCredentialEncryptionError as exc:
                # A present but unreadable BYOK value is authoritative: never fall back.
                raise SemanticCredentialConfigurationError(
                    "The saved OpenAI credential is unavailable; replace or remove it in AI Settings."
                ) from exc
        if policy == "user_required":
            raise SemanticCredentialConfigurationError("Add an OpenAI API key in AI Settings before starting semantic work.")
        if self._settings.openai_api_key:
            return self._settings.openai_api_key
        raise SemanticCredentialConfigurationError("OpenAI semantic access is not configured for this account or deployment.")


class SemanticCredentialService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        connection_tester: Callable[[str, Settings], None] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._encryption = SemanticCredentialEncryption(self._settings.semantic_credential_encryption_key)
        self._connection_tester = connection_tester or self._test_with_semantic_provider

    def status(self, user_id: str) -> SemanticCredentialStatus:
        row = self._session.get(UserSemanticCredential, (user_id, OPENAI_PROVIDER))
        provider = self._settings.default_llm_provider.casefold().strip()
        policy = self._settings.semantic_credential_policy
        active_openai = provider == OPENAI_PROVIDER
        inactive = row is not None and (not active_openai or policy == "deployment_only")

        if row is None:
            credential_state = "absent"
            display_identity = None
        elif inactive:
            credential_state = "inactive"
            display_identity = _masked_identity(row.display_suffix)
        else:
            try:
                self._encryption.decrypt(user_id, OPENAI_PROVIDER, row.nonce, row.ciphertext, row.format_version)
            except SemanticCredentialEncryptionError:
                credential_state = "unavailable"
            else:
                credential_state = "usable"
            display_identity = _masked_identity(row.display_suffix)

        if not active_openai:
            source = "none"
        elif policy == "deployment_only":
            source = "deployment" if self._settings.openai_api_key else "none"
        elif row is not None:
            source = "user" if credential_state == "usable" else "unavailable" if credential_state == "unavailable" else "none"
        elif policy == "user_required":
            source = "none"
        else:
            source = "deployment" if self._settings.openai_api_key else "none"
        return SemanticCredentialStatus(
            provider=provider,
            policy=policy,
            storage_available=(
                active_openai and policy != "deployment_only" and self._encryption.configured()
            ),
            user_credential_configured=row is not None,
            user_credential_state=credential_state,
            credential_revision=row.revision if row else 0,
            effective_source=source,
            display_identity=display_identity,
            deployment_credential_configured=bool(self._settings.openai_api_key),
        )

    def save(self, user_id: str, expected_revision: int, api_key: str) -> SemanticCredentialStatus:
        if self._settings.default_llm_provider.casefold().strip() != OPENAI_PROVIDER:
            raise SemanticCredentialConfigurationError("Per-user OpenAI credentials are available only when OpenAI is the deployment semantic provider.")
        if self._settings.semantic_credential_policy == "deployment_only":
            raise SemanticCredentialConfigurationError("The current deployment policy does not accept user OpenAI credentials.")
        normalized_key = api_key.strip()
        if not normalized_key:
            raise SemanticCredentialConfigurationError("Enter a non-empty OpenAI API key.")
        try:
            nonce, ciphertext, version = self._encryption.encrypt(user_id, OPENAI_PROVIDER, normalized_key)
        except SemanticCredentialEncryptionError as exc:
            raise SemanticCredentialConfigurationError(str(exc)) from exc
        suffix = normalized_key[-4:] if len(normalized_key) >= 12 else None
        row = self._session.get(UserSemanticCredential, (user_id, OPENAI_PROVIDER))
        if expected_revision == 0:
            if row is not None:
                raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.")
            self._session.add(UserSemanticCredential(
                user_id=user_id, provider=OPENAI_PROVIDER, nonce=nonce, ciphertext=ciphertext,
                format_version=version, revision=1, display_suffix=suffix,
            ))
            try:
                self._session.commit()
            except IntegrityError as exc:
                self._session.rollback()
                raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.") from exc
        else:
            result = self._session.execute(
                update(UserSemanticCredential)
                .where(
                    UserSemanticCredential.user_id == user_id,
                    UserSemanticCredential.provider == OPENAI_PROVIDER,
                    UserSemanticCredential.revision == expected_revision,
                )
                .values(
                    nonce=nonce, ciphertext=ciphertext, format_version=version,
                    revision=UserSemanticCredential.revision + 1, display_suffix=suffix,
                    updated_at=func.now(),
                )
            )
            if result.rowcount != 1:
                self._session.rollback()
                raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.")
            self._session.commit()
        return self.status(user_id)

    def remove(self, user_id: str, expected_revision: int) -> SemanticCredentialStatus:
        row = self._session.get(UserSemanticCredential, (user_id, OPENAI_PROVIDER))
        actual_revision = row.revision if row else 0
        if actual_revision != expected_revision:
            raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.")
        if row is not None:
            result = self._session.execute(delete(UserSemanticCredential).where(
                UserSemanticCredential.user_id == user_id,
                UserSemanticCredential.provider == OPENAI_PROVIDER,
                UserSemanticCredential.revision == expected_revision,
            ))
            if result.rowcount != 1:
                self._session.rollback()
                raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.")
            self._session.commit()
        return self.status(user_id)

    def test_connection(self, user_id: str, *, api_key: str | None = None) -> SemanticCredentialConnectionTest:
        if self._settings.default_llm_provider.casefold().strip() != OPENAI_PROVIDER:
            return SemanticCredentialConnectionTest(False, "unsupported_provider", "A user OpenAI connection test is unavailable for the current semantic provider.")
        if self._settings.semantic_credential_policy == "deployment_only":
            return SemanticCredentialConnectionTest(False, "policy_restricted", "The current deployment policy does not allow user credential testing.")
        if api_key is not None:
            credential = api_key.strip()
        else:
            row = self._session.get(UserSemanticCredential, (user_id, OPENAI_PROVIDER))
            if row is None:
                return SemanticCredentialConnectionTest(False, "credential_missing", "Add or save a user OpenAI key before testing it.")
            try:
                credential = self._encryption.decrypt(user_id, OPENAI_PROVIDER, row.nonce, row.ciphertext, row.format_version)
            except SemanticCredentialEncryptionError:
                return SemanticCredentialConnectionTest(False, "credential_unavailable", "The saved OpenAI key is unavailable; replace or remove it.")
        if not credential:
            return SemanticCredentialConnectionTest(False, "credential_missing", "Enter or save a user OpenAI key before testing it.")
        try:
            self._connection_tester(credential, self._settings)
        except (SemanticCredentialConfigurationError, SemanticProviderConfigurationError):
            return SemanticCredentialConnectionTest(False, "unsupported_configuration", "The connection test is not supported by the configured model or capability.")
        except (SemanticStructuredOutputModelError, SemanticStructuredOutputSchemaError):
            return SemanticCredentialConnectionTest(False, "unsupported_configuration", "The test model or structured-output capability is unsupported.")
        except SemanticCredentialRejectedError:
            return SemanticCredentialConnectionTest(False, "credential_rejected", "OpenAI rejected this credential.")
        except SemanticModelAccessError:
            return SemanticCredentialConnectionTest(False, "model_access_rejected", "The test model or provider access was rejected.")
        except SemanticProviderUnavailableError:
            return SemanticCredentialConnectionTest(False, "provider_unavailable", "OpenAI could not be reached. Check connectivity and try again.")
        except SemanticProviderRateLimitError:
            return SemanticCredentialConnectionTest(False, "rate_or_quota_limited", "OpenAI rate-limited the test or rejected it under usage limits.")
        except SemanticProviderRequestError:
            return SemanticCredentialConnectionTest(False, "provider_request_rejected", "OpenAI rejected the connection-test request.")
        except Exception:
            return SemanticCredentialConnectionTest(False, "provider_unavailable", "The connection test could not be completed.")
        return SemanticCredentialConnectionTest(True, "connected", "The credential test request succeeded. This does not confirm access to every model.")

    def resolver(self, user_id: str) -> UserSemanticCredentialResolver:
        return UserSemanticCredentialResolver(self._session, user_id, self._settings)

    @staticmethod
    def _test_with_semantic_provider(api_key: str, settings: Settings) -> None:
        llm = LLMProviderFactory(EnvironmentCredentialResolver(openai_api_key=api_key)).create(
            LLMProviderConfig(
                provider=OPENAI_PROVIDER,
                model=settings.cv_semantic_extraction_model,
                base_url=settings.effective_llm_base_url,
            )
        )
        llm.generate(
            model=settings.cv_semantic_extraction_model,
            system_prompt="Respond with the single word OK.",
            user_prompt="Connection test.",
            operation="openai_credential_connection_test",
        )


def _masked_identity(suffix: str | None) -> str | None:
    return f"••••••••{suffix}" if suffix else None

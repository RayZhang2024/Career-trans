"""User-scoped semantic credential lifecycle and ephemeral runtime resolution."""

from dataclasses import dataclass

from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.user_openai_credential import UserOpenAICredential
from app.services.openai_credential_encryption import OpenAICredentialEncryption, OpenAICredentialEncryptionError


class SemanticCredentialConflictError(RuntimeError):
    pass


class SemanticCredentialConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SemanticCredentialStatus:
    policy: str
    storage_available: bool
    user_credential_configured: bool
    credential_revision: int
    effective_source: str


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
        if provider.casefold().strip() != "openai":
            return None
        if self._error is not None:
            raise self._error
        if not self._resolved:
            try:
                self._credential = self._resolve()
                self._resolved = True
            except SemanticCredentialConfigurationError as exc:
                self._error = exc
                raise
        return self._credential

    def _resolve(self) -> str | None:
        policy = self._settings.semantic_credential_policy
        if policy == "deployment_only":
            if not self._settings.openai_api_key:
                raise SemanticCredentialConfigurationError("OpenAI semantic access requires deployment credentials under the current policy.")
            return self._settings.openai_api_key

        row = self._session.get(UserOpenAICredential, self._user_id)
        if row is not None:
            try:
                return OpenAICredentialEncryption(self._settings.openai_credential_encryption_key).decrypt(
                    self._user_id, row.nonce, row.ciphertext, row.format_version
                )
            except OpenAICredentialEncryptionError as exc:
                # A present but unreadable BYOK value is authoritative: never fall back.
                raise SemanticCredentialConfigurationError(str(exc)) from exc
        if policy == "user_required":
            raise SemanticCredentialConfigurationError("Add an OpenAI API key in AI Settings before starting semantic work.")
        if self._settings.openai_api_key:
            return self._settings.openai_api_key
        raise SemanticCredentialConfigurationError("OpenAI semantic access is not configured for this account or deployment.")


class SemanticCredentialService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._encryption = OpenAICredentialEncryption(self._settings.openai_credential_encryption_key)

    def status(self, user_id: str) -> SemanticCredentialStatus:
        row = self._session.get(UserOpenAICredential, user_id)
        policy = self._settings.semantic_credential_policy
        if policy == "deployment_only":
            source = "deployment" if self._settings.openai_api_key else "none"
        elif row is not None:
            source = "user"
        elif policy == "user_or_deployment" and self._settings.openai_api_key:
            source = "deployment"
        else:
            source = "none"
        return SemanticCredentialStatus(
            policy=policy,
            storage_available=self._encryption.configured() and self._settings.default_llm_provider.casefold().strip() == "openai",
            user_credential_configured=row is not None,
            credential_revision=row.revision if row else 0,
            effective_source=source,
        )

    def save(self, user_id: str, expected_revision: int, api_key: str) -> SemanticCredentialStatus:
        if self._settings.default_llm_provider.casefold().strip() != "openai":
            raise SemanticCredentialConfigurationError("Per-user OpenAI credentials are available only when OpenAI is the deployment semantic provider.")
        if self._settings.semantic_credential_policy == "deployment_only":
            raise SemanticCredentialConfigurationError("The current deployment policy does not accept user OpenAI credentials.")
        if not api_key.strip():
            raise SemanticCredentialConfigurationError("Enter a non-empty OpenAI API key.")
        try:
            nonce, ciphertext, version = self._encryption.encrypt(user_id, api_key.strip())
        except OpenAICredentialEncryptionError as exc:
            raise SemanticCredentialConfigurationError(str(exc)) from exc
        row = self._session.get(UserOpenAICredential, user_id)
        if expected_revision == 0:
            if row is not None:
                raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.")
            self._session.add(UserOpenAICredential(
                user_id=user_id, nonce=nonce, ciphertext=ciphertext, format_version=version, revision=1,
            ))
            try:
                self._session.commit()
            except IntegrityError as exc:
                self._session.rollback()
                raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.") from exc
        else:
            result = self._session.execute(
                update(UserOpenAICredential)
                .where(UserOpenAICredential.user_id == user_id, UserOpenAICredential.revision == expected_revision)
                .values(nonce=nonce, ciphertext=ciphertext, format_version=version,
                        revision=UserOpenAICredential.revision + 1, updated_at=func.now())
            )
            if result.rowcount != 1:
                self._session.rollback()
                raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.")
            self._session.commit()
        return self.status(user_id)

    def remove(self, user_id: str, expected_revision: int) -> SemanticCredentialStatus:
        row = self._session.get(UserOpenAICredential, user_id)
        actual_revision = row.revision if row else 0
        if actual_revision != expected_revision:
            raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.")
        if row is not None:
            result = self._session.execute(delete(UserOpenAICredential).where(
                UserOpenAICredential.user_id == user_id,
                UserOpenAICredential.revision == expected_revision,
            ))
            if result.rowcount != 1:
                self._session.rollback()
                raise SemanticCredentialConflictError("AI credential changed. Reload the current settings and try again.")
            self._session.commit()
        return self.status(user_id)

    def resolver(self, user_id: str) -> UserSemanticCredentialResolver:
        return UserSemanticCredentialResolver(self._session, user_id, self._settings)

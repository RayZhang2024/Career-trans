import base64
import sqlite3

import pytest

from app.core.config import Settings
from app.models.user import User
from app.models.user_ai_settings import UserAiSettings
from app.models.user_openai_credential import UserOpenAICredential
from app.services.semantic_credential_service import (
    SemanticCredentialConflictError,
    SemanticCredentialConfigurationError,
    SemanticCredentialService,
)
def _settings(**overrides) -> Settings:
    key = base64.urlsafe_b64encode(b"synthetic-encryption-key-32-byte").decode().rstrip("=")
    return Settings(openai_credential_encryption_key=key, **overrides)


def _user(session, user_id: str) -> None:
    session.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="synthetic"))
    session.commit()


def test_per_user_openai_credential_is_encrypted_and_revision_is_independent(db_session):
    _user(db_session, "credential-a")
    _user(db_session, "credential-b")
    service = SemanticCredentialService(db_session, settings=_settings())
    db_session.add(UserAiSettings(user_id="credential-a", provider="openai", revision=4, preferences_json="{}"))
    db_session.commit()
    secret = "synthetic-not-a-real-openai-key"

    status = service.save("credential-a", 0, secret)

    row = db_session.get(UserOpenAICredential, "credential-a")
    assert row is not None and secret.encode() not in row.ciphertext
    assert status.credential_revision == 1
    assert db_session.get(UserAiSettings, "credential-a").revision == 4
    assert status.user_credential_configured is True
    assert service.status("credential-b").credential_revision == 0
    assert "synthetic-not-a-real-openai-key" not in repr(status)
    assert service.resolver("credential-a").credential_for("openai") == secret
    with pytest.raises(SemanticCredentialConfigurationError):
        service.resolver("credential-b").credential_for("openai")

    updated = service.save("credential-a", 1, "synthetic-replacement")
    assert updated.credential_revision == 2
    with pytest.raises(SemanticCredentialConflictError):
        service.save("credential-a", 1, "stale-update")
    removed = service.remove("credential-a", 2)
    assert removed.credential_revision == 0
    assert service.status("credential-a").user_credential_configured is False

    service.save("credential-a", 0, "synthetic-deletion-test")
    db_session.delete(db_session.get(User, "credential-a"))
    db_session.commit()
    assert db_session.get(UserOpenAICredential, "credential-a") is None


def test_user_required_never_falls_back_and_configured_byok_corruption_never_falls_back(db_session):
    _user(db_session, "required-user")
    settings = _settings(semantic_credential_policy="user_required", openai_api_key="deployment-sentinel")
    service = SemanticCredentialService(db_session, settings=settings)
    with pytest.raises(SemanticCredentialConfigurationError, match="Add an OpenAI API key"):
        service.resolver("required-user").credential_for("openai")

    service.save("required-user", 0, "user-sentinel")
    resolver = service.resolver("required-user")
    assert resolver.credential_for("openai") == "user-sentinel"
    row = db_session.get(UserOpenAICredential, "required-user")
    assert row is not None
    row.ciphertext = b"corrupt-but-present"
    db_session.commit()
    with pytest.raises(SemanticCredentialConfigurationError, match="cannot be opened"):
        service.resolver("required-user").credential_for("openai")


def test_user_or_deployment_uses_byok_authoritatively_and_deployment_only_ignores_it(db_session):
    _user(db_session, "policy-user")
    service = SemanticCredentialService(db_session, settings=_settings(openai_api_key="deployment-sentinel"))
    assert service.resolver("policy-user").credential_for("openai") == "deployment-sentinel"
    service.save("policy-user", 0, "user-sentinel")
    assert service.resolver("policy-user").credential_for("openai") == "user-sentinel"

    deployment_only = SemanticCredentialService(
        db_session, settings=_settings(semantic_credential_policy="deployment_only", openai_api_key="deployment-only-sentinel")
    )
    assert deployment_only.resolver("policy-user").credential_for("openai") == "deployment-only-sentinel"


def test_semantic_client_receives_the_selected_byok_key_without_fallback(db_session, monkeypatch):
    from app.api import deps
    from app.providers.llm import SemanticProviderRequestError

    _user(db_session, "factory-user")
    settings = _settings(openai_api_key="deployment-sentinel")
    resolver_service = SemanticCredentialService(db_session, settings=settings)
    resolver_service.save("factory-user", 0, "user-sentinel")
    resolver = resolver_service.resolver("factory-user")
    client = deps.get_semantic_response_client(settings, model="gpt-5.6-luna", operation="job_extraction", credential_resolver=resolver)
    assert client.responses._llm._api_key == "user-sentinel"

    calls = []
    class FakeResponses:
        def create(self, **_kwargs):
            calls.append("user-key-request")
            raise SemanticProviderRequestError("OpenAI rejected the configured user credential.")
    class FakeClient:
        responses = FakeResponses()
    monkeypatch.setattr("app.providers.llm.create_traced_openai_client", lambda **_kwargs: FakeClient())
    with pytest.raises(SemanticProviderRequestError):
        client.responses.create(model="gpt-5.6-luna", input=[])
    assert calls == ["user-key-request"]


def test_credential_reads_are_provider_free_and_authenticated_api_never_echoes_key(client, monkeypatch):
    settings = _settings(openai_api_key=None)
    monkeypatch.setattr("app.services.ai_settings_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.semantic_credential_service.get_settings", lambda: settings)
    login = {"email": "semantic-credential@example.com", "password": "Strong-test-password-92!"}
    registered = client.post("/api/v1/auth/register", json=login)
    assert registered.status_code == 201, registered.text
    token = client.post("/api/v1/auth/login", json=login).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    read = client.get("/api/v1/ai/credentials", headers=headers)
    assert read.status_code == 200
    assert read.json()["effective_source"] == "none"
    secret = "synthetic-sentinel-never-return-this"
    saved = client.put("/api/v1/ai/credentials/openai", headers=headers, json={"expected_revision": 0, "api_key": secret})
    assert saved.status_code == 200
    assert secret not in saved.text
    assert "api_key" not in saved.text
    assert client.put("/api/v1/ai/credentials/openai", headers=headers, json={"expected_revision": 0, "api_key": secret}).status_code == 409
    assert client.get("/api/v1/ai/credentials").status_code == 401


def test_sqlite_migration_upgrades_existing_database_idempotently(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    migration_path = Path(__file__).parents[1] / "migrations" / "20261004_user_openai_credentials_sqlite.py"
    spec = spec_from_file_location("user_openai_migration", migration_path)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    database = tmp_path / "existing.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)")
    connection.execute("INSERT INTO users (id) VALUES ('legacy-user')")
    connection.commit()
    connection.close()
    url = f"sqlite:///{database.as_posix()}"

    migration.upgrade(url)
    migration.upgrade(url)

    connection = sqlite3.connect(database)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(user_openai_credentials)")}
    connection.execute("INSERT INTO user_openai_credentials (user_id, nonce, ciphertext, format_version, revision) VALUES (?, ?, ?, ?, ?)",
                       ("legacy-user", b"123456789012", b"ciphertext", 1, 1))
    connection.commit()
    assert {"user_id", "nonce", "ciphertext", "format_version", "revision"} <= columns
    connection.close()

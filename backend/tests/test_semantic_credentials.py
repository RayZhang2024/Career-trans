import base64
import sqlite3
import traceback

import pytest

from app.core.config import Settings
from app.models.user import User
from app.models.user_ai_settings import UserAiSettings
from app.models.user_semantic_credential import UserSemanticCredential
from app.services.semantic_credential_service import (
    SemanticCredentialConflictError,
    SemanticCredentialConfigurationError,
    SemanticCredentialService,
)
def _settings(**overrides) -> Settings:
    key = base64.urlsafe_b64encode(b"synthetic-encryption-key-32-byte").decode().rstrip("=")
    return Settings(semantic_credential_encryption_key=key, **overrides)


def _user(session, user_id: str) -> None:
    session.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="synthetic"))
    session.commit()


def test_provider_neutral_credential_is_encrypted_and_revision_is_independent(db_session):
    _user(db_session, "credential-a")
    _user(db_session, "credential-b")
    service = SemanticCredentialService(db_session, settings=_settings())
    db_session.add(UserAiSettings(user_id="credential-a", provider="openai", revision=4, preferences_json="{}"))
    db_session.commit()
    secret = "synthetic-not-a-real-openai-key"

    status = service.save("credential-a", 0, secret)

    row = db_session.get(UserSemanticCredential, ("credential-a", "openai"))
    assert row is not None and secret.encode() not in row.ciphertext
    assert status.credential_revision == 1
    assert db_session.get(UserAiSettings, "credential-a").revision == 4
    assert status.user_credential_configured is True
    assert status.user_credential_state == "usable"
    assert status.display_identity == "••••••••-key"
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
    assert db_session.get(UserSemanticCredential, ("credential-a", "openai")) is None


def test_user_required_never_falls_back_and_configured_byok_corruption_never_falls_back(db_session):
    _user(db_session, "required-user")
    settings = _settings(semantic_credential_policy="user_required", openai_api_key="deployment-sentinel")
    service = SemanticCredentialService(db_session, settings=settings)
    with pytest.raises(SemanticCredentialConfigurationError, match="Add an OpenAI API key"):
        service.resolver("required-user").credential_for("openai")

    service.save("required-user", 0, "user-sentinel")
    resolver = service.resolver("required-user")
    assert resolver.credential_for("openai") == "user-sentinel"
    row = db_session.get(UserSemanticCredential, ("required-user", "openai"))
    assert row is not None
    row.ciphertext = b"corrupt-but-present"
    db_session.commit()
    with pytest.raises(SemanticCredentialConfigurationError, match="unavailable"):
        service.resolver("required-user").credential_for("openai")
    assert service.status("required-user").user_credential_state == "unavailable"
    assert service.status("required-user").effective_source == "unavailable"


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
    assert deployment_only.status("policy-user").user_credential_state == "inactive"
    assert deployment_only.remove("policy-user", 1).user_credential_configured is False


def test_saved_credential_with_lost_or_wrong_encryption_key_is_unavailable_and_fails_closed(db_session):
    _user(db_session, "key-rotation-user")
    storage = SemanticCredentialService(db_session, settings=_settings())
    storage.save("key-rotation-user", 0, "synthetic-key-must-not-escape")

    for wrong_key in (None, base64.urlsafe_b64encode(b"a-different-synthetic-32-byte-key").decode().rstrip("=")):
        service = SemanticCredentialService(
            db_session,
            settings=Settings(
                semantic_credential_encryption_key=wrong_key,
                openai_api_key="deployment-fallback-must-not-be-used",
                semantic_credential_policy="user_or_deployment",
            ),
        )
        status = service.status("key-rotation-user")
        assert status.user_credential_state == "unavailable"
        assert status.effective_source == "unavailable"
        with pytest.raises(SemanticCredentialConfigurationError, match="unavailable"):
            service.resolver("key-rotation-user").credential_for("openai")
        assert "synthetic-key-must-not-escape" not in repr(status)


def test_short_key_has_no_reversible_display_suffix(db_session):
    _user(db_session, "short-key-user")
    status = SemanticCredentialService(db_session, settings=_settings()).save("short-key-user", 0, "tiny")
    assert status.display_identity is None


def test_encryption_associated_data_binds_user_and_provider():
    from app.services.semantic_credential_encryption import SemanticCredentialEncryption, SemanticCredentialEncryptionError

    encryption = SemanticCredentialEncryption(_settings().semantic_credential_encryption_key)
    nonce, ciphertext, version = encryption.encrypt("owner-a", "openai", "synthetic-provider-bound-key")
    assert encryption.decrypt("owner-a", "openai", nonce, ciphertext, version) == "synthetic-provider-bound-key"
    with pytest.raises(SemanticCredentialEncryptionError):
        encryption.decrypt("owner-b", "openai", nonce, ciphertext, version)
    with pytest.raises(SemanticCredentialEncryptionError):
        encryption.decrypt("owner-a", "anthropic", nonce, ciphertext, version)


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


def test_secret_identity_and_credential_revision_do_not_enter_runtime_or_content_fingerprints(db_session):
    from app.schemas.ai_settings import SemanticOperation, UserAiPreferences
    from app.services.llm_runtime import resolve_runtime_snapshot

    _user(db_session, "fingerprint-owner")
    settings = _settings(semantic_credential_policy="user_or_deployment", openai_api_key="deployment-key")
    snapshot_before = resolve_runtime_snapshot(settings, UserAiPreferences(), preference_revision=1)
    before_projection = snapshot_before.fingerprint_projection((SemanticOperation.JOB_RELEVANCE,))
    secret = "synthetic-secret-must-never-escape-269"

    service = SemanticCredentialService(db_session, settings=settings)
    saved = service.save("fingerprint-owner", 0, secret)
    snapshot_after = resolve_runtime_snapshot(settings, UserAiPreferences(), preference_revision=99)
    after_projection = snapshot_after.fingerprint_projection((SemanticOperation.JOB_RELEVANCE,))

    assert before_projection == after_projection
    assert secret not in repr(snapshot_after)
    assert secret not in repr(after_projection)
    assert saved.display_identity == "••••••••-269"


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
    assert saved.json()["display_identity"] == "••••••••this"
    assert secret not in read.text and secret not in saved.text
    invalid = client.put(
        "/api/v1/ai/credentials/openai",
        headers=headers,
        json={"expected_revision": 1, "api_key": secret, "unexpected": "invalid"},
    )
    assert invalid.status_code == 422 and secret not in invalid.text
    assert client.put("/api/v1/ai/credentials/openai", headers=headers, json={"expected_revision": 0, "api_key": secret}).status_code == 409
    assert client.get("/api/v1/ai/credentials").status_code == 401


def test_invalid_secret_bearing_save_validation_does_not_retain_secret_in_response_logs_or_traceback(
    client, monkeypatch, caplog
):
    from fastapi import HTTPException

    from app.api.routes.ai_settings import _parse_credential_write

    settings = _settings(openai_api_key=None)
    monkeypatch.setattr("app.services.ai_settings_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.semantic_credential_service.get_settings", lambda: settings)
    login = {"email": "invalid-secret-validation@example.com", "password": "Strong-test-password-92!"}
    assert client.post("/api/v1/auth/register", json=login).status_code == 201
    token = client.post("/api/v1/auth/login", json=login).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    sentinel = "synthetic-invalid-secret-must-never-escape-269"
    oversized_key = sentinel + ("x" * 4096)

    response = client.put(
        "/api/v1/ai/credentials/openai",
        headers=headers,
        json={"expected_revision": 0, "api_key": oversized_key},
    )
    assert response.status_code == 422
    assert sentinel not in response.text
    assert sentinel not in caplog.text

    with pytest.raises(HTTPException) as caught:
        _parse_credential_write({"expected_revision": 0, "api_key": oversized_key})
    raised = caught.value
    assert raised.__cause__ is None
    assert raised.__context__ is None
    assert sentinel not in "".join(traceback.format_exception(raised))
    assert sentinel not in caplog.text


def test_connection_test_uses_only_submitted_user_key_and_returns_bounded_categories(client, db_session, monkeypatch, caplog):
    from app.api.routes import ai_settings
    from app.providers.llm import SemanticCredentialRejectedError

    secret = "synthetic-secret-must-never-escape-269"
    settings = _settings(openai_api_key="deployment-key-must-not-be-tested")
    monkeypatch.setattr("app.services.ai_settings_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.semantic_credential_service.get_settings", lambda: settings)
    seen: list[str] = []

    def fake_tester(key: str, _settings: Settings) -> None:
        seen.append(key)

    from app.main import app
    app.dependency_overrides[ai_settings._credential_service] = lambda: SemanticCredentialService(
        db_session, settings=settings, connection_tester=fake_tester
    )
    login = {"email": "connection-test@example.com", "password": "Strong-test-password-92!"}
    assert client.post("/api/v1/auth/register", json=login).status_code == 201
    token = client.post("/api/v1/auth/login", json=login).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/api/v1/ai/credentials/openai/test",
        headers=headers,
        json={"api_key": secret},
    )
    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert "does not confirm access to every model" in response.json()["message"]
    assert seen == [secret]
    assert secret not in response.text
    assert "deployment-key-must-not-be-tested" not in response.text

    owner_id = db_session.query(User).filter_by(email=login["email"]).one().id
    SemanticCredentialService(db_session, settings=settings).save(owner_id, 0, secret)
    saved_test = client.post("/api/v1/ai/credentials/openai/test", headers=headers, json={})
    assert saved_test.status_code == 200 and saved_test.json()["connected"] is True
    assert seen == [secret, secret]
    assert secret not in saved_test.text

    app.dependency_overrides[ai_settings._credential_service] = lambda: SemanticCredentialService(
            db_session,
            settings=settings,
            connection_tester=lambda *_args: (_ for _ in ()).throw(
                SemanticCredentialRejectedError(f"raw provider body with {secret}")
            ),
        )
    rejected = client.post(
        "/api/v1/ai/credentials/openai/test",
        headers=headers,
        json={"api_key": secret},
    )
    assert rejected.status_code == 200
    assert rejected.json()["category"] == "credential_rejected"
    assert secret not in rejected.text
    assert secret not in caplog.text

    invalid = client.post(
        "/api/v1/ai/credentials/openai/test",
        headers=headers,
        json={"api_key": secret, "unexpected": "value"},
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text
    assert secret not in caplog.text


def test_connection_test_reports_saved_credential_unavailable_without_fallback(client, db_session, monkeypatch):
    from app.api.routes import ai_settings

    settings = _settings(openai_api_key="deployment-key-must-not-be-used")
    monkeypatch.setattr("app.services.ai_settings_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.semantic_credential_service.get_settings", lambda: settings)
    login = {"email": "connection-unavailable@example.com", "password": "Strong-test-password-92!"}
    assert client.post("/api/v1/auth/register", json=login).status_code == 201
    token = client.post("/api/v1/auth/login", json=login).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    key_service = SemanticCredentialService(db_session, settings=settings)
    owner_id = db_session.query(User).filter_by(email=login["email"]).one().id
    key_service.save(owner_id, 0, "synthetic-user-key")
    row = db_session.get(UserSemanticCredential, (owner_id, "openai"))
    assert row is not None
    row.ciphertext = b"corrupt"
    db_session.commit()
    from app.main import app
    app.dependency_overrides[ai_settings._credential_service] = lambda: SemanticCredentialService(
            db_session, settings=settings,
            connection_tester=lambda *_args: pytest.fail("unreadable saved key must not be tested"),
        )

    status = client.get("/api/v1/ai/credentials", headers=headers)
    assert status.status_code == 200
    assert status.json()["user_credential_state"] == "unavailable"
    assert "deployment-key-must-not-be-used" not in status.text
    response = client.post("/api/v1/ai/credentials/openai/test", headers=headers, json={})
    assert response.status_code == 200
    assert response.json()["category"] == "credential_unavailable"


def test_sqlite_migration_upgrades_existing_database_idempotently(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    migration_path = Path(__file__).parents[1] / "migrations" / "20261004_user_semantic_credentials_sqlite.py"
    spec = spec_from_file_location("user_semantic_migration", migration_path)
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
    columns = {row[1] for row in connection.execute("PRAGMA table_info(user_semantic_credentials)")}
    connection.execute("INSERT INTO user_semantic_credentials (user_id, provider, nonce, ciphertext, format_version, revision, display_suffix) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       ("legacy-user", "openai", b"123456789012", b"ciphertext", 1, 1, "1234"))
    connection.commit()
    assert {"user_id", "provider", "nonce", "ciphertext", "format_version", "revision", "display_suffix", "created_at", "updated_at"} <= columns
    primary_key = [row[1] for row in sorted(
        (row for row in connection.execute("PRAGMA table_info(user_semantic_credentials)") if row[5]),
        key=lambda row: row[5],
    )]
    assert primary_key == ["user_id", "provider"]
    connection.close()


def test_postgresql_migration_declares_provider_identity_and_encrypted_columns():
    from pathlib import Path

    migration = (Path(__file__).parents[1] / "migrations" / "20261004_user_semantic_credentials_postgresql.sql").read_text()
    assert "PRIMARY KEY (user_id, provider)" in migration
    assert "REFERENCES users(id) ON DELETE CASCADE" in migration
    for column in ("nonce BYTEA", "ciphertext BYTEA", "format_version", "revision", "display_suffix", "created_at", "updated_at"):
        assert column in migration

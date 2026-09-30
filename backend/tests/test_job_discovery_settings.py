import base64
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy import create_engine, text

from app.api import deps
from app.api.routes import job_discovery_settings as settings_route
from app.core.config import Settings
from app.models.user import User
from app.models.user_job_discovery_settings import UserJobDiscoverySettings, UserTavilyCredential
from app.providers.web_search import BraveWebSearchProvider, OpenAIWebSearchProvider, TavilyWebSearchProvider
from app.schemas.job_discovery_settings import JobDiscoveryProvider, JobDiscoverySettingsReplace
from app.services.job_discovery_settings_service import (
    JobDiscoveryProviderNotReady,
    JobDiscoverySettingsConflictError,
    JobDiscoverySettingsError,
    JobDiscoverySettingsService,
)
from app.services.tavily_credential_encryption import TavilyCredentialEncryption, TavilyCredentialEncryptionError
from app.main import app
from app import scheduled_discovery_runner


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "agentic_search_provider": "openai",
        "openai_api_key": "deployment-openai-key",
        "brave_search_api_key": "deployment-brave-key",
        "tavily_api_key": None,
        "tavily_credential_encryption_key": base64.urlsafe_b64encode(b"e" * 32).decode(),
    }
    values.update(overrides)
    return Settings(**values)


def _user(db_session, user_id: str) -> User:
    user = User(id=user_id, email=f"{user_id}@example.com", password_hash="test-only")
    db_session.add(user)
    db_session.commit()
    return user


def test_job_discovery_settings_inherit_default_and_support_revision_conflicts(db_session) -> None:
    _user(db_session, "inherit")
    service = JobDiscoverySettingsService(db_session, settings=_settings(agentic_search_provider="brave"))

    initial = service.read("inherit")
    assert initial.revision == 0
    assert initial.provider_override is None
    assert initial.deployment_provider == "brave"
    assert initial.effective_provider == "brave"

    saved = service.replace(
        "inherit",
        JobDiscoverySettingsReplace(expected_revision=0, provider_override=JobDiscoveryProvider.DISABLED),
    )
    assert saved.revision == 1
    assert saved.effective_provider == "disabled"

    inherited = service.replace(
        "inherit", JobDiscoverySettingsReplace(expected_revision=1, provider_override=None)
    )
    assert inherited.revision == 2
    assert inherited.effective_provider == "brave"
    with pytest.raises(JobDiscoverySettingsConflictError):
        service.replace(
            "inherit", JobDiscoverySettingsReplace(expected_revision=1, provider_override=JobDiscoveryProvider.TAVILY)
        )


@pytest.mark.parametrize(
    ("deployment", "api_key", "expected_type", "expected_name"),
    [
        ("openai", "deployment-openai-key", OpenAIWebSearchProvider, "openai"),
        ("brave", "deployment-brave-key", BraveWebSearchProvider, "brave"),
        ("tavily", "deployment-tavily-key", TavilyWebSearchProvider, "tavily"),
    ],
)
def test_deployment_defaults_resolve_supported_providers(db_session, deployment, api_key, expected_type, expected_name) -> None:
    _user(db_session, "default")
    configuration = {"agentic_search_provider": deployment}
    configuration["tavily_api_key" if deployment == "tavily" else "openai_api_key" if deployment == "openai" else "brave_search_api_key"] = api_key
    resolved = JobDiscoverySettingsService(db_session, settings=_settings(**configuration)).resolve_provider("default")
    assert isinstance(resolved.provider, expected_type)
    assert resolved.metadata["provider"] == expected_name
    assert resolved.metadata["credential_source"] == "deployment"


def test_tavily_deployment_default_without_key_fails_before_semantic_construction(db_session, monkeypatch) -> None:
    _user(db_session, "no-tavily-key")
    db_session.add(UserJobDiscoverySettings(user_id="no-tavily-key", provider_override="tavily", revision=1))
    db_session.commit()
    settings = _settings(tavily_api_key=None)
    semantic_calls = []
    monkeypatch.setattr(deps, "get_semantic_response_client", lambda *args, **kwargs: semantic_calls.append(args))

    with pytest.raises(JobDiscoveryProviderNotReady, match="no usable Tavily API key") as error:
        deps.get_user_agentic_job_discovery_service_for_user(
            db_session, "no-tavily-key", settings=settings
        )
    assert semantic_calls == []
    assert error.value.provider_metadata == {
        "provider": "tavily", "credential_source": "none", "search_depth": "basic"
    }


def test_user_tavily_override_beats_deployment_and_is_encrypted_and_isolated(db_session) -> None:
    _user(db_session, "user-a")
    _user(db_session, "user-b")
    settings = _settings(agentic_search_provider="openai", tavily_api_key="deployment-tavily-key")
    service = JobDiscoverySettingsService(db_session, settings=settings)
    service.save_tavily_credential("user-a", 0, "user-a-tavily-secret")
    service.replace("user-a", JobDiscoverySettingsReplace(expected_revision=1, provider_override=JobDiscoveryProvider.TAVILY))

    read = service.read("user-a")
    assert read.tavily_credential_configured is True
    assert read.tavily_credential_source == "user"
    assert read.tavily_credential_usable is True
    assert "user-a-tavily-secret" not in read.model_dump_json()
    stored = db_session.get(UserTavilyCredential, "user-a")
    assert b"user-a-tavily-secret" not in stored.ciphertext
    assert stored.ciphertext != b"user-a-tavily-secret"

    resolved_a = service.resolve_provider("user-a")
    resolved_b = service.resolve_provider("user-b")
    assert isinstance(resolved_a.provider, TavilyWebSearchProvider)
    assert resolved_a.metadata["credential_source"] == "user"
    assert resolved_a.provider._api_key == "user-a-tavily-secret"
    assert isinstance(resolved_b.provider, OpenAIWebSearchProvider)
    assert resolved_b.provider is not resolved_a.provider


def test_removing_user_key_falls_back_to_deployment_key(db_session) -> None:
    _user(db_session, "fallback")
    settings = _settings(agentic_search_provider="tavily", tavily_api_key="deployment-tavily-key")
    service = JobDiscoverySettingsService(db_session, settings=settings)
    service.save_tavily_credential("fallback", 0, "user-tavily-key")
    assert service.read("fallback").tavily_credential_source == "user"
    removed = service.remove_tavily_credential("fallback", expected_revision=1)
    assert removed.revision == 2
    assert removed.tavily_credential_source == "deployment"
    resolved = service.resolve_provider("fallback")
    assert resolved.metadata["credential_source"] == "deployment"
    assert resolved.provider._api_key == "deployment-tavily-key"


def test_no_deployment_or_user_key_is_unavailable_and_unsupported_deployment_is_safe(db_session) -> None:
    _user(db_session, "missing")
    no_key = JobDiscoverySettingsService(
        db_session,
        settings=_settings(agentic_search_provider="tavily", tavily_api_key=None, tavily_credential_encryption_key=None),
    )
    assert no_key.read("missing").tavily_user_credential_storage_available is False
    with pytest.raises(JobDiscoveryProviderNotReady, match="no usable Tavily API key"):
        no_key.resolve_provider("missing")

    unsupported = JobDiscoverySettingsService(db_session, settings=_settings(agentic_search_provider="serper"))
    assert unsupported.read("missing").effective_provider == "unsupported"
    with pytest.raises(JobDiscoveryProviderNotReady, match="unsupported"):
        unsupported.resolve_provider("missing")


def test_explicit_openai_and_disabled_override_deployment_provider(db_session) -> None:
    _user(db_session, "openai-override")
    _user(db_session, "disabled-override")
    settings = _settings(agentic_search_provider="brave")
    service = JobDiscoverySettingsService(db_session, settings=settings)
    service.replace("openai-override", JobDiscoverySettingsReplace(expected_revision=0, provider_override="openai"))
    service.replace("disabled-override", JobDiscoverySettingsReplace(expected_revision=0, provider_override="disabled"))
    assert isinstance(service.resolve_provider("openai-override").provider, OpenAIWebSearchProvider)
    with pytest.raises(JobDiscoveryProviderNotReady, match="disabled"):
        service.resolve_provider("disabled-override")


def test_corrupt_or_wrong_owner_ciphertext_fails_closed_without_secret_in_error(db_session) -> None:
    _user(db_session, "crypt-a")
    _user(db_session, "crypt-b")
    cipher = TavilyCredentialEncryption(_settings().tavily_credential_encryption_key)
    nonce, ciphertext, version = cipher.encrypt("crypt-a", "private-key-value")
    with pytest.raises(TavilyCredentialEncryptionError):
        cipher.decrypt("crypt-b", nonce, ciphertext, version)
    db_session.add(UserTavilyCredential(user_id="crypt-a", nonce=nonce, ciphertext=ciphertext, format_version=version))
    db_session.add(UserJobDiscoverySettings(user_id="crypt-a", provider_override="tavily", revision=1))
    db_session.commit()
    service = JobDiscoverySettingsService(db_session, settings=_settings(tavily_credential_encryption_key=None))
    assert service.read("crypt-a").tavily_credential_usable is False
    with pytest.raises(JobDiscoveryProviderNotReady) as error:
        service.resolve_provider("crypt-a")
    assert "private-key-value" not in str(error.value)


def test_user_deletion_cascades_new_settings_and_encrypted_credential(db_session) -> None:
    user = _user(db_session, "delete-me")
    service = JobDiscoverySettingsService(db_session, settings=_settings())
    service.save_tavily_credential(user.id, 0, "user-key")
    db_session.delete(user)
    db_session.commit()
    assert db_session.get(UserJobDiscoverySettings, user.id) is None
    assert db_session.get(UserTavilyCredential, user.id) is None


def test_settings_metadata_never_contains_plaintext_and_revision_mutations_are_isolated(db_session) -> None:
    _user(db_session, "api-user")
    service = JobDiscoverySettingsService(db_session, settings=_settings())
    saved = service.save_tavily_credential("api-user", 0, "another-secret")
    assert "another-secret" not in saved.model_dump_json()
    assert db_session.scalar(select(UserTavilyCredential).where(UserTavilyCredential.user_id == "api-user"))
    assert db_session.get(UserJobDiscoverySettings, "api-user").revision == 1


def test_issue_256_migration_adds_user_tables_and_preserves_existing_execution() -> None:
    migration = Path(__file__).parents[1] / "migrations" / "20260930_job_discovery_settings.sql"
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE scheduled_discovery_executions (id VARCHAR(36) PRIMARY KEY)"))
            connection.execute(text("INSERT INTO users (id) VALUES ('migration-user')"))
            connection.execute(text("INSERT INTO scheduled_discovery_executions (id) VALUES ('execution-1')"))
            for statement in migration.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    connection.execute(text(statement))
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(scheduled_discovery_executions)"))}
            assert "web_search_metadata_json" in columns
            assert connection.execute(text("SELECT web_search_metadata_json FROM scheduled_discovery_executions WHERE id='execution-1'")).scalar_one() == "{}"
            assert connection.execute(text("SELECT COUNT(*) FROM user_job_discovery_settings")).scalar_one() == 0
            connection.execute(text("INSERT INTO user_job_discovery_settings (user_id, revision) VALUES ('migration-user', 1)"))
    finally:
        engine.dispose()


def test_authenticated_settings_api_never_returns_key_and_test_connection_is_one_basic_search(
    client, db_session, monkeypatch
) -> None:
    credentials = {"email": "settings-api@example.com", "password": "Strong-password-123"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    api_settings = _settings(agentic_search_provider="tavily", tavily_api_key="deployment-secret")
    app.dependency_overrides[settings_route._service] = lambda: JobDiscoverySettingsService(db_session, settings=api_settings)
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(TavilyWebSearchProvider, "search", lambda self, query, limit: seen.append((query, limit)) or [])
    try:
        response = client.get("/api/v1/job-discovery/settings", headers=headers)
        assert response.status_code == 200
        assert response.json()["effective_provider"] == "tavily"
        assert "deployment-secret" not in response.text

        saved = client.put(
            "/api/v1/job-discovery/tavily-credential",
            headers=headers,
            json={"expected_revision": 0, "api_key": "user-secret-value"},
        )
        assert saved.status_code == 200
        assert "user-secret-value" not in saved.text
        assert db_session.get(UserTavilyCredential, db_session.scalar(select(User.id).where(User.email == credentials["email"])))
        after_save = client.get("/api/v1/job-discovery/settings", headers=headers)
        assert after_save.status_code == 200
        assert after_save.json()["tavily_credential_source"] == "user"
        assert "user-secret-value" not in after_save.text

        tested = client.post("/api/v1/job-discovery/tavily-connection-test", headers=headers, json={})
        assert tested.status_code == 200
        assert tested.json()["credential_source"] == "user"
        assert seen == [("Tavily API connection test", 1)]
        assert db_session.query(UserTavilyCredential).count() == 1
    finally:
        app.dependency_overrides.pop(settings_route._service, None)


@pytest.mark.parametrize("encryption_key", [None, "not-valid-base64-and-not-a-32-byte-key"])
def test_authenticated_credential_save_fails_safely_without_advancing_revision(
    client, db_session, encryption_key
) -> None:
    credentials = {
        "email": f"missing-encryption-{uuid4()}@example.com",
        "password": "Strong-password-123",
    }
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = db_session.scalar(select(User.id).where(User.email == credentials["email"]))
    submitted_key = "synthetic-tavily-secret-must-not-appear-in-errors"
    api_settings = _settings(tavily_credential_encryption_key=encryption_key)
    app.dependency_overrides[settings_route._service] = lambda: JobDiscoverySettingsService(
        db_session, settings=api_settings
    )
    try:
        response = client.put(
            "/api/v1/job-discovery/tavily-credential",
            headers=headers,
            json={"expected_revision": 0, "api_key": submitted_key},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == (
            "Per-user Tavily credential storage is not configured. Ask the administrator to configure the credential-encryption key."
        )
        assert submitted_key not in response.text
        assert db_session.get(UserJobDiscoverySettings, user_id) is None
        assert db_session.get(UserTavilyCredential, user_id) is None
    finally:
        app.dependency_overrides.pop(settings_route._service, None)


def test_standalone_due_runner_uses_shared_user_scoped_provider_resolver(db_session, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(scheduled_discovery_runner.deps, "get_career_analysis_graph", lambda *args: object())
    monkeypatch.setattr(scheduled_discovery_runner.deps, "get_job_analysis_service", lambda: object())
    monkeypatch.setattr(scheduled_discovery_runner.deps, "get_requirement_matching_service", lambda: object())
    monkeypatch.setattr(scheduled_discovery_runner.deps, "get_career_assessment_service", lambda: object())
    monkeypatch.setattr(scheduled_discovery_runner.deps, "get_job_relevance_agent", lambda: object())
    monkeypatch.setattr(scheduled_discovery_runner.deps, "get_job_archetype_agent", lambda: object())
    monkeypatch.setattr(scheduled_discovery_runner.deps, "get_job_ranking_service", lambda *args: object())
    monkeypatch.setattr(scheduled_discovery_runner.deps, "get_structured_ats_discovery_service", lambda session: object())
    monkeypatch.setattr(scheduled_discovery_runner.deps, "get_user_job_discovery_service", lambda *args: object())
    monkeypatch.setattr(
        scheduled_discovery_runner.deps,
        "get_user_agentic_job_discovery_service_for_user",
        lambda session, user_id, snapshot, **kwargs: calls.append((session, user_id, snapshot, kwargs)) or object(),
    )
    service = scheduled_discovery_runner.build_service(db_session)
    snapshot = object()
    service._agentic_service("scheduled-owner", snapshot)
    assert calls == [(db_session, "scheduled-owner", snapshot, {"scheduled_due_runner": True})]

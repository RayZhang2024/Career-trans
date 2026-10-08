import asyncio
import logging
import os

from app.core.config import (
    Settings,
    configure_langsmith_environment,
    langsmith_configuration_status,
    log_langsmith_configuration,
)


def test_configured_langsmith_values_are_exported_without_logging_secrets(monkeypatch) -> None:
    configured = {
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_API_KEY": "test-key",
        "LANGSMITH_PROJECT": "career-trans-dev",
        "LANGSMITH_ENDPOINT": "https://eu.api.smith.langchain.com",
        "LANGSMITH_WORKSPACE_ID": "test-workspace-id",
    }
    for name in configured:
        monkeypatch.delenv(name, raising=False)
    for name, value in configured.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)
    assert settings.langsmith_tracing is True
    assert settings.langsmith_api_key == "test-key"
    assert settings.langsmith_project == "career-trans-dev"
    assert settings.langsmith_endpoint == "https://eu.api.smith.langchain.com"
    assert settings.langsmith_workspace_id == "test-workspace-id"

    for name in configured:
        monkeypatch.delenv(name, raising=False)
    configure_langsmith_environment(settings)

    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "career-trans-dev"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://eu.api.smith.langchain.com"
    assert os.environ["LANGSMITH_WORKSPACE_ID"] == "test-workspace-id"
    assert "LANGSMITH_API_KEY" in os.environ


def test_existing_process_langsmith_configuration_is_preserved(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_PROJECT", "externally-configured")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://eu.api.smith.langchain.com")
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "external-workspace-id")

    configure_langsmith_environment(
        Settings(
            langsmith_tracing=True,
            langsmith_project="career-trans-dev",
            langsmith_endpoint="https://eu.api.smith.langchain.com",
        )
    )

    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGSMITH_PROJECT"] == "externally-configured"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://eu.api.smith.langchain.com"
    assert os.environ["LANGSMITH_WORKSPACE_ID"] == "external-workspace-id"


def test_langsmith_is_disabled_by_default_without_a_key(monkeypatch, caplog) -> None:
    for name in (
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_WORKSPACE_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)
    configure_langsmith_environment(settings)
    status = langsmith_configuration_status(settings)
    log_langsmith_configuration(settings)

    assert status == {
        "tracing_enabled": False,
        "api_key_configured": False,
        "project": "default",
        "endpoint": "https://api.smith.langchain.com",
        "workspace_id_configured": False,
    }
    assert "LANGSMITH_TRACING" not in os.environ
    assert "LANGSMITH_API_KEY" not in os.environ
    assert "LangSmith tracing" not in caplog.text


def test_enabled_tracing_logs_safe_status_and_warns_when_key_is_missing(monkeypatch, caplog) -> None:
    for name in ("LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT", "LANGSMITH_WORKSPACE_ID"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        _env_file=None,
        langsmith_tracing=True,
        langsmith_project="career-trans-dev\noperator-controlled",
        langsmith_endpoint="https://user:password@eu.api.smith.langchain.com/path?token=endpoint-secret",
        langsmith_workspace_id="workspace-secret",
    )

    with caplog.at_level(logging.WARNING):
        log_langsmith_configuration(settings)

    assert "enabled=true api_key_configured=false" in caplog.text
    assert "career-trans-dev operator-controlled" in caplog.text
    assert "endpoint=https://eu.api.smith.langchain.com" in caplog.text
    assert "workspace_id_configured=True" in caplog.text
    for secret in ("password", "endpoint-secret", "workspace-secret"):
        assert secret not in caplog.text


def test_configured_tracing_status_does_not_claim_delivery_or_log_credentials(caplog) -> None:
    settings = Settings(
        _env_file=None,
        langsmith_tracing=True,
        langsmith_api_key="langsmith-secret",
        langsmith_project="career-trans-dev",
        langsmith_endpoint="https://eu.api.smith.langchain.com",
        langsmith_workspace_id="workspace-secret",
    )

    with caplog.at_level(logging.INFO):
        log_langsmith_configuration(settings)

    assert "enabled=true api_key_configured=true" in caplog.text
    assert "project=career-trans-dev" in caplog.text
    assert "endpoint=https://eu.api.smith.langchain.com" in caplog.text
    assert "workspace_id_configured=True" in caplog.text
    assert "trace delivery is not verified" in caplog.text
    assert "langsmith-secret" not in caplog.text
    assert "workspace-secret" not in caplog.text


def test_startup_with_tracing_disabled_and_no_key_emits_no_langsmith_diagnostic(monkeypatch, caplog) -> None:
    from app import main

    for name in (
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_WORKSPACE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "settings", Settings(_env_file=None))
    monkeypatch.setattr(main, "run_candidate_compatibility_startup", lambda _engine: None)

    async def run_lifespan():
        async with main.lifespan(main.app):
            pass

    with caplog.at_level(logging.INFO):
        asyncio.run(run_lifespan())

    assert "LangSmith tracing" not in caplog.text

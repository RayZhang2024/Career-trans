import os

from app.core.config import Settings, configure_langsmith_environment


def test_configured_langsmith_values_are_exported_without_logging_secrets(
    monkeypatch,
    tmp_path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "LANGSMITH_TRACING=true",
                "LANGSMITH_API_KEY=test-key",
                "LANGSMITH_PROJECT=career-trans-dev",
                "LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com",
            ]
        ),
        encoding="utf-8",
    )
    for name in (
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=env_file)
    configure_langsmith_environment(settings)

    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "career-trans-dev"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://eu.api.smith.langchain.com"
    assert "LANGSMITH_API_KEY" in os.environ


def test_existing_process_langsmith_configuration_is_preserved(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_PROJECT", "externally-configured")

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

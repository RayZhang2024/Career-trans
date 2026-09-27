import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _clear_codex_model_environment(monkeypatch) -> None:
    monkeypatch.delenv("CODEX_EXTERNAL_DISCOVERY_MODEL", raising=False)
    monkeypatch.delenv("codex_external_discovery_model", raising=False)


def test_codex_external_discovery_model_defaults_independently_of_semantic_settings(monkeypatch) -> None:
    _clear_codex_model_environment(monkeypatch)
    monkeypatch.setenv("AGENTIC_DISCOVERY_MODEL", "semantic-agentic-model")
    monkeypatch.setenv("JOB_RELEVANCE_MODEL", "semantic-relevance-model")
    monkeypatch.setenv("JOB_EXTRACTION_MODEL", "semantic-extraction-model")
    monkeypatch.setenv("APPLICATION_DRAFTING_MODEL", "semantic-drafting-model")

    settings = Settings(
        _env_file=None,
        default_llm_provider="ollama",
        openai_api_key=None,
    )

    assert settings.codex_external_discovery_model == "gpt-5.6-luna"
    assert settings.agentic_discovery_model == "semantic-agentic-model"
    assert settings.job_relevance_model == "semantic-relevance-model"


def test_codex_external_discovery_model_reads_local_env_and_environment_override(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CODEX_EXTERNAL_DISCOVERY_MODEL=local-env-model\n", encoding="utf-8")
    _clear_codex_model_environment(monkeypatch)
    assert Settings(_env_file=env_file).codex_external_discovery_model == "local-env-model"

    monkeypatch.setenv("CODEX_EXTERNAL_DISCOVERY_MODEL", "process-env-model")
    assert Settings(_env_file=env_file).codex_external_discovery_model == "process-env-model"


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_codex_external_discovery_model_uses_builtin_default(blank, monkeypatch) -> None:
    _clear_codex_model_environment(monkeypatch)
    settings = Settings(_env_file=None, codex_external_discovery_model=blank)
    assert settings.codex_external_discovery_model == "gpt-5.6-luna"


def test_invalid_codex_external_discovery_model_fails_configuration_clearly(monkeypatch) -> None:
    _clear_codex_model_environment(monkeypatch)
    with pytest.raises(ValidationError, match="CODEX_EXTERNAL_DISCOVERY_MODEL"):
        Settings(_env_file=None, codex_external_discovery_model="--search exec")

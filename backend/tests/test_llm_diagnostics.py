import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.deps import get_cv_ingestion_service
from app.api.routes import config as config_routes
from app.core.config import Settings
from app.core.database import get_db
from app.main import app
from app.providers.llm import (
    SemanticOutputError,
    SemanticProviderRequestError,
    SemanticProviderUnavailableError,
)
from app.services.cv_ingestion_service import CVIngestionService
from app.services.cv_interpretation_service import SemanticCVInterpreter


class FailingInterpreter:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def interpret(self, _documents):
        raise self._error


def _auth(client: TestClient, email: str = "diagnostics@example.com") -> dict[str, str]:
    credentials = {"email": email, "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _uploaded_markdown_draft(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/cv-ingestion/upload",
        headers=headers,
        files=[("files", ("cv.md", "Candidate private text", "text/markdown"))],
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (SemanticProviderUnavailableError("Ollama semantic provider is unavailable."), 503, "Ollama semantic provider is unavailable."),
        (SemanticProviderRequestError("OpenAI rejected semantic model 'missing-model'. Check the configured model and provider access."), 502, "OpenAI rejected semantic model"),
        (SemanticOutputError("CV interpretation returned invalid structured data."), 502, "invalid structured data"),
    ],
)
def test_cv_interpret_maps_expected_semantic_failures_to_safe_http_errors(client, db_session, error, expected_status, expected_detail) -> None:
    app.dependency_overrides[get_cv_ingestion_service] = lambda: CVIngestionService(db_session, interpreter=FailingInterpreter(error))
    try:
        headers = _auth(client)
        draft_id = _uploaded_markdown_draft(client, headers)
        response = client.post(f"/api/v1/cv-ingestion/{draft_id}/interpret", headers=headers)
        assert response.status_code == expected_status
        assert expected_detail in response.json()["detail"]
        assert "Candidate private text" not in response.json()["detail"]
        assert "api_key" not in response.json()["detail"].casefold()
    finally:
        app.dependency_overrides.pop(get_cv_ingestion_service, None)


def test_cv_interpret_missing_openai_key_returns_503_at_api_boundary(client, db_session) -> None:
    def missing_key_service() -> CVIngestionService:
        return CVIngestionService(
            db_session,
            interpreter_factory=lambda: SemanticCVInterpreter(
                deps.get_semantic_response_client(
                    Settings(openai_api_key=None),
                    model="cv-test-model",
                    operation="cv_evidence_extraction",
                ),
                "cv-test-model",
            ),
        )

    app.dependency_overrides[get_cv_ingestion_service] = missing_key_service
    try:
        headers = _auth(client, "missing-key@example.com")
        draft_id = _uploaded_markdown_draft(client, headers)
        response = client.post(f"/api/v1/cv-ingestion/{draft_id}/interpret", headers=headers)
        assert response.status_code == 503
        assert "OPENAI_API_KEY" in response.json()["detail"]
        assert "Candidate private text" not in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_cv_ingestion_service, None)


def test_unexpected_cv_interpret_programming_error_remains_500(db_session) -> None:
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_cv_ingestion_service] = lambda: CVIngestionService(db_session, interpreter=FailingInterpreter(RuntimeError("programming defect")))
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            headers = _auth(client, "unexpected-error@example.com")
            draft_id = _uploaded_markdown_draft(client, headers)
            response = client.post(f"/api/v1/cv-ingestion/{draft_id}/interpret", headers=headers)
        assert response.status_code == 500
    finally:
        app.dependency_overrides.pop(get_cv_ingestion_service, None)
        app.dependency_overrides.pop(get_db, None)


def test_provider_neutral_model_setting_precedence_and_legacy_compatibility(tmp_path) -> None:
    env_file = tmp_path / "settings.env"
    env_file.write_text("JOB_EXTRACTION_MODEL=neutral-model\nOPENAI_JOB_EXTRACTION_MODEL=legacy-model\n")
    assert Settings(_env_file=env_file).job_extraction_model == "neutral-model"
    legacy_file = tmp_path / "legacy.env"
    legacy_file.write_text("OPENAI_REQUIREMENT_MATCHING_MODEL=legacy-model\n")
    settings = Settings(_env_file=legacy_file)
    assert settings.requirement_matching_model == "legacy-model"
    assert settings.openai_requirement_matching_model == "legacy-model"


def test_safe_protected_configuration_endpoint_and_check(client, monkeypatch) -> None:
    secret = "never-return-this-secret"
    settings = Settings(
        default_llm_provider="ollama",
        openai_api_key=secret,
        ollama_base_url="http://localhost:11434",
        cv_semantic_extraction_model="local-model",
    )
    monkeypatch.setattr(config_routes, "get_settings", lambda: settings)
    headers = _auth(client, "config@example.com")
    response = client.get("/api/v1/config/llm", headers=headers)
    assert response.status_code == 200
    assert response.json()["openai_api_key_configured"] is True
    assert response.json()["cv_semantic_extraction_model"] == "local-model"
    assert response.json()["candidate_adviser_model"] == "gpt-5.6-luna"
    assert response.json()["agentic_search_provider"] == "disabled"
    assert response.json()["requirement_matching_structured_output_capability"] == "not_applicable"
    assert secret not in response.text
    assert client.get("/api/v1/config/llm/check", headers=headers).json()["ready"] is True
    assert client.get("/api/v1/config/llm").status_code == 401


def test_configuration_check_reports_missing_key_without_secret(client, monkeypatch) -> None:
    monkeypatch.setattr(config_routes, "get_settings", lambda: Settings(default_llm_provider="openai", openai_api_key=None))
    response = client.get("/api/v1/config/llm/check", headers=_auth(client, "config-missing@example.com"))
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_configuration_check_rejects_empty_semantic_model(client, monkeypatch) -> None:
    monkeypatch.setattr(config_routes, "get_settings", lambda: Settings(default_llm_provider="ollama", job_relevance_model="   "))
    response = client.get("/api/v1/config/llm/check", headers=_auth(client, "config-empty-model@example.com"))
    assert response.status_code == 503
    assert "JOB_RELEVANCE_MODEL" in response.json()["detail"]


def test_configuration_check_rejects_unknown_openai_structured_output_model(client, monkeypatch) -> None:
    settings = Settings(
        default_llm_provider="openai",
        openai_api_key="server-secret",
        requirement_matching_model="unknown-model",
    )
    monkeypatch.setattr(config_routes, "get_settings", lambda: settings)

    response = client.get("/api/v1/config/llm/check", headers=_auth(client, "config-structured-output@example.com"))

    assert response.status_code == 503
    assert "Structured Outputs" in response.json()["detail"]
    assert "server-secret" not in response.text


def test_configuration_reports_supported_openai_requirement_matching_model(client, monkeypatch) -> None:
    settings = Settings(
        default_llm_provider="openai",
        openai_api_key="server-secret",
        requirement_matching_model="gpt-5.6",
    )
    monkeypatch.setattr(config_routes, "get_settings", lambda: settings)

    headers = _auth(client, "config-supported-structured-output@example.com")
    assert client.get("/api/v1/config/llm", headers=headers).json()[
        "requirement_matching_structured_output_capability"
    ] == "supported"
    assert client.get("/api/v1/config/llm/check", headers=headers).json()["ready"] is True


def test_ollama_transport_and_output_failures_are_normalized() -> None:
    from urllib.error import URLError

    from app.providers.llm import OllamaSemanticLLM

    unavailable = OllamaSemanticLLM(transport=lambda *_args: (_ for _ in ()).throw(URLError("offline")))
    with pytest.raises(SemanticProviderUnavailableError):
        unavailable.generate(model="local", system_prompt="", user_prompt="", operation="test")
    malformed = OllamaSemanticLLM(transport=lambda *_args: {"message": {}})
    with pytest.raises(SemanticOutputError):
        malformed.generate(model="local", system_prompt="", user_prompt="", operation="test")


def test_openai_transport_and_model_rejections_are_normalized(monkeypatch) -> None:
    from openai import APIConnectionError, APIStatusError

    from app.providers import llm as llm_provider
    from app.providers.llm import OpenAISemanticLLM

    request = httpx.Request("POST", "https://api.example.test/responses")

    class ConnectionClient:
        class responses:
            @staticmethod
            def create(**_kwargs):
                raise APIConnectionError(request=request)

    monkeypatch.setattr(llm_provider, "create_traced_openai_client", lambda **_kwargs: ConnectionClient())
    with pytest.raises(SemanticProviderUnavailableError):
        OpenAISemanticLLM(api_key="not-exposed").generate(model="model", system_prompt="", user_prompt="", operation="test")

    class RejectedClient:
        class responses:
            @staticmethod
            def create(**_kwargs):
                raise APIStatusError("not found", response=httpx.Response(404, request=request), body=None)

    monkeypatch.setattr(llm_provider, "create_traced_openai_client", lambda **_kwargs: RejectedClient())
    with pytest.raises(SemanticProviderRequestError, match="model 'model'") as error:
        OpenAISemanticLLM(api_key="not-exposed").generate(model="model", system_prompt="private", user_prompt="candidate data", operation="test")
    assert "candidate data" not in str(error.value)
    assert "not-exposed" not in str(error.value)

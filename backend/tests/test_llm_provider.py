from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError
from fastapi import HTTPException

from app.api import deps
from app.core.config import Settings
from app.providers.llm import (
    EnvironmentCredentialResolver,
    LLMCapability,
    LLMProviderConfig,
    LLMProviderConfigurationError,
    LLMProviderFactory,
    OllamaSemanticLLM,
    OpenAISemanticLLM,
    SemanticProviderConfigurationError,
    SemanticProviderRequestError,
    SemanticResponseClient,
    SemanticStructuredOutputModelError,
    SemanticStructuredOutputSchemaError,
    openai_structured_output_supported,
)
from app.providers.web_search import BraveWebSearchProvider


class FakeSemanticLLM:
    capabilities = frozenset({LLMCapability.STRUCTURED_OUTPUT})

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return '{"ok": true}'


def test_openai_is_the_compatible_default_and_requires_safe_credential() -> None:
    settings = Settings(openai_api_key="server-secret")
    client = deps.get_semantic_response_client(settings, model="gpt-5.6-luna", operation="job_extraction")

    assert isinstance(client.responses._llm, OpenAISemanticLLM)
    with pytest.raises(HTTPException, match="OPENAI_API_KEY") as exc_info:
        deps.get_semantic_response_client(Settings(openai_api_key=None), model="gpt-5.6-luna", operation="job_extraction")
    assert "server-secret" not in str(exc_info.value.detail)


def test_unknown_openai_model_is_not_assumed_structured_output_compatible() -> None:
    with pytest.raises(HTTPException, match="capability catalog") as exc_info:
        deps.get_semantic_response_client(Settings(openai_api_key="test-key"), model="arbitrary-model-alias", operation="job_extraction")
    assert exc_info.value.status_code == 503


def test_ollama_requires_no_cloud_key_and_respects_configured_base_url() -> None:
    transport_calls: list[tuple[str, dict[str, object]]] = []
    ollama = OllamaSemanticLLM(
        base_url="http://localhost:12345/",
        transport=lambda url, payload: transport_calls.append((url, payload)) or {"message": {"content": "{}"}},
    )

    assert ollama.generate(
        model="local-model",
        system_prompt="system",
        user_prompt="user",
        operation="job_extraction",
        required_capabilities=frozenset({LLMCapability.STRUCTURED_OUTPUT}),
    ) == "{}"
    assert transport_calls[0][0] == "http://localhost:12345/api/chat"
    resolved = LLMProviderFactory(EnvironmentCredentialResolver()).create(
        LLMProviderConfig(provider="ollama", model="local-model", base_url="http://localhost:12345")
    )
    assert isinstance(resolved, OllamaSemanticLLM)


def test_unsupported_provider_and_capabilities_fail_without_secrets() -> None:
    factory = LLMProviderFactory(EnvironmentCredentialResolver(openai_api_key="do-not-expose"))

    with pytest.raises(LLMProviderConfigurationError, match="Unsupported LLM provider") as provider_error:
        factory.create(LLMProviderConfig(provider="unknown", model="model"))
    assert "do-not-expose" not in str(provider_error.value)
    with pytest.raises(LLMProviderConfigurationError, match="hosted_web_search"):
        factory.create(
            LLMProviderConfig(
                provider="ollama",
                model="local-model",
                required_capabilities=frozenset({LLMCapability.HOSTED_WEB_SEARCH}),
            )
        )


def test_semantic_response_facade_preserves_existing_agent_message_shape() -> None:
    llm = FakeSemanticLLM()
    client = SemanticResponseClient(llm, operation="requirement_matching")

    response = client.responses.create(
        model="test-model",
        input=[
            {"role": "system", "content": "unchanged system prompt"},
            {"role": "user", "content": "unchanged user payload"},
        ],
    )

    assert response.output_text == '{"ok": true}'
    assert llm.calls == [
        {
            "model": "test-model",
            "system_prompt": "unchanged system prompt",
            "user_prompt": "unchanged user payload",
            "operation": "requirement_matching",
            "required_capabilities": frozenset({LLMCapability.STRUCTURED_OUTPUT}),
            "output_schema": None,
            "reasoning_effort": None,
        }
    ]


def test_semantic_response_facade_passes_json_schema_to_provider() -> None:
    llm = FakeSemanticLLM()
    client = SemanticResponseClient(llm, operation="requirement_matching")
    schema = {"type": "object", "properties": {"matches": {"type": "array"}}}

    client.responses.create(
        model="gpt-5.6",
        input=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        text={"format": {"type": "json_schema", "schema": schema}},
    )

    assert llm.calls[0]["output_schema"] == schema


def test_openai_semantic_llm_uses_native_responses_json_schema(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponses:
        def create(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(output_text='{"matches": []}')

    monkeypatch.setattr(
        "app.providers.llm.create_traced_openai_client",
        lambda **_: SimpleNamespace(responses=FakeResponses()),
    )
    schema = {"type": "object", "properties": {"matches": {"type": "array"}}}

    output = OpenAISemanticLLM(api_key="server-secret").generate(
        model="gpt-5.6",
        system_prompt="system",
        user_prompt="user",
        operation="requirement_matching",
        output_schema=schema,
    )

    assert output == '{"matches": []}'
    assert calls[0]["text"] == {
        "format": {
            "type": "json_schema",
            "name": "requirement_matching",
            "strict": True,
            "schema": schema,
        }
    }
    assert "reasoning" not in calls[0]


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_openai_semantic_llm_forwards_explicit_reasoning_effort(monkeypatch, effort: str) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponses:
        def create(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(output_text="ok")

    monkeypatch.setattr("app.providers.llm.create_traced_openai_client", lambda **_: SimpleNamespace(responses=FakeResponses()))
    assert OpenAISemanticLLM(api_key="server-secret").generate(
        model="gpt-5.6-luna",
        system_prompt="system",
        user_prompt="user",
        operation="job_extraction",
        output_schema={"type": "object"},
        reasoning_effort=effort,
    ) == "ok"
    assert calls[0]["reasoning"] == {"effort": effort}


def test_openai_rejects_unsupported_reasoning_before_request(monkeypatch) -> None:
    invoked = False

    def should_not_construct(**_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("provider client must not be constructed")

    monkeypatch.setattr("app.providers.llm.create_traced_openai_client", should_not_construct)
    with pytest.raises(SemanticProviderConfigurationError, match="reasoning effort"):
        OpenAISemanticLLM(api_key="server-secret").generate(
            model="gpt-4o",
            system_prompt="private",
            user_prompt="private",
            operation="job_extraction",
            output_schema={"type": "object"},
            reasoning_effort="high",
        )
    assert not invoked


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6"])
def test_known_openai_structured_output_models_are_supported(model: str) -> None:
    assert openai_structured_output_supported(model) is True


@pytest.mark.parametrize("operation", ["candidate_adviser", "requirement_matching"])
def test_openai_rejects_unknown_structured_output_model_before_request_without_operation_or_private_data_leakage(operation: str) -> None:
    assert openai_structured_output_supported("unknown-model") is False
    with pytest.raises(SemanticProviderConfigurationError, match="known Structured Outputs") as exc_info:
        OpenAISemanticLLM(api_key="server-secret").generate(
            model="unknown-model",
            system_prompt="private-system-prompt",
            user_prompt="candidate-private-data",
            operation=operation,
            output_schema={"type": "object"},
        )

    assert type(exc_info.value) is SemanticProviderConfigurationError
    assert str(exc_info.value) == (
        "Configured OpenAI semantic model does not have a known "
        "Structured Outputs capability. Configure a supported OpenAI model."
    )
    assert "requirement matching" not in str(exc_info.value).casefold()
    assert "candidate-private-data" not in str(exc_info.value)
    assert "private-system-prompt" not in str(exc_info.value)
    assert "server-secret" not in str(exc_info.value)


def test_openai_structured_output_bad_requests_are_safely_classified(monkeypatch) -> None:
    request = httpx.Request("POST", "https://api.example.test/responses")

    def rejected(message: str):
        class RejectedClient:
            class responses:
                @staticmethod
                def create(**_kwargs):
                    raise APIStatusError(message, response=httpx.Response(400, request=request), body=None)

        return RejectedClient()

    schema = {"type": "object"}
    monkeypatch.setattr(
        "app.providers.llm.create_traced_openai_client",
        lambda **_kwargs: rejected("Invalid schema for response_format"),
    )
    with pytest.raises(SemanticStructuredOutputSchemaError) as schema_error:
        OpenAISemanticLLM(api_key="server-secret").generate(
            model="gpt-5.6",
            system_prompt="system",
            user_prompt="candidate-private-data",
            operation="requirement_matching",
            output_schema=schema,
        )
    assert "candidate-private-data" not in str(schema_error.value)

    monkeypatch.setattr(
        "app.providers.llm.create_traced_openai_client",
        lambda **_kwargs: rejected("Model does not support structured outputs"),
    )
    with pytest.raises(SemanticStructuredOutputModelError):
        OpenAISemanticLLM(api_key="server-secret").generate(
            model="gpt-5.6",
            system_prompt="system",
            user_prompt="user",
            operation="requirement_matching",
            output_schema=schema,
        )

    monkeypatch.setattr(
        "app.providers.llm.create_traced_openai_client",
        lambda **_kwargs: rejected("Invalid parameter: temperature"),
    )
    with pytest.raises(SemanticProviderRequestError) as generic_error:
        OpenAISemanticLLM(api_key="server-secret").generate(
            model="gpt-5.6",
            system_prompt="system",
            user_prompt="user",
            operation="requirement_matching",
            output_schema=schema,
        )
    assert type(generic_error.value) is SemanticProviderRequestError


@pytest.mark.parametrize(
    ("operation", "expected_message"),
    [
        ("candidate_adviser", "OpenAI rejected the Structured Outputs schema for candidate adviser."),
        ("requirement_matching", "OpenAI rejected the Structured Outputs schema for requirement matching."),
    ],
)
def test_structured_schema_rejection_diagnostics_are_operation_correct_and_private(monkeypatch, operation: str, expected_message: str) -> None:
    request = httpx.Request("POST", "https://api.example.test/responses")

    class RejectedClient:
        class responses:
            @staticmethod
            def create(**_kwargs):
                raise APIStatusError(
                    "Invalid schema with provider-private-detail",
                    response=httpx.Response(400, request=request),
                    body=None,
                )

    monkeypatch.setattr(
        "app.providers.llm.create_traced_openai_client",
        lambda **_kwargs: RejectedClient(),
    )
    with pytest.raises(SemanticStructuredOutputSchemaError) as exc_info:
        OpenAISemanticLLM(api_key="server-secret").generate(
            model="gpt-5.6",
            system_prompt="private prompt",
            user_prompt="candidate-private-data",
            operation=operation,
            output_schema={"type": "object"},
        )

    assert str(exc_info.value) == expected_message
    assert "provider-private-detail" not in str(exc_info.value)
    assert "candidate-private-data" not in str(exc_info.value)
    assert "server-secret" not in str(exc_info.value)


def test_migrated_dependency_construction_accepts_ollama_without_openai(monkeypatch) -> None:
    settings = Settings(default_llm_provider="ollama", ollama_base_url="http://localhost:11434", openai_api_key=None)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    service = deps.get_job_analysis_service()
    assert isinstance(service._extractor._client, SemanticResponseClient)
    assert isinstance(service._extractor._client.responses._llm, OllamaSemanticLLM)


def test_all_migrated_semantic_components_receive_provider_neutral_clients(monkeypatch) -> None:
    settings = Settings(
        default_llm_provider="ollama",
        openai_api_key=None,
        agentic_search_provider="brave",
        brave_search_api_key="test-brave-key",
    )
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    clients = [
        deps.get_job_analysis_service()._extractor._client,
        deps.get_requirement_matching_service()._matcher._client,
        deps.get_job_relevance_agent()._client,
        deps.get_job_archetype_agent()._client,
        deps.get_career_assessment_service()._agent._client,
        deps.get_agentic_job_discovery_service(object())._strategy_generator._client,
        deps.get_agentic_job_discovery_service(object())._vacancy_extractor._client,
    ]
    assert all(isinstance(client, SemanticResponseClient) for client in clients)
    assert all(isinstance(client.responses._llm, OllamaSemanticLLM) for client in clients)


def test_search_provider_selection_remains_independent_from_llm_provider() -> None:
    settings = Settings(
        default_llm_provider="ollama",
        openai_api_key=None,
        agentic_search_provider="brave",
        brave_search_api_key="brave-server-secret",
    )

    assert isinstance(deps.get_agentic_web_search_provider(settings), BraveWebSearchProvider)


def test_factory_rejects_missing_model() -> None:
    with pytest.raises(Exception, match="String should have at least 1 character"):
        LLMProviderConfig(provider="ollama", model="")

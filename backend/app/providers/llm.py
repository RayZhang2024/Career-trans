"""Small provider-neutral boundary for Career-trans semantic LLM operations."""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openai import APIConnectionError, APIStatusError, APITimeoutError

from pydantic import BaseModel, ConfigDict, Field

from app.agents.openai_client import create_traced_openai_client


class LLMCapability(StrEnum):
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    HOSTED_WEB_SEARCH = "hosted_web_search"
    LOCAL_INFERENCE = "local_inference"
    STREAMING = "streaming"


class SemanticProviderConfigurationError(ValueError):
    """A safe operator-facing configuration error that never includes credentials."""


class LLMProviderConfigurationError(SemanticProviderConfigurationError):
    """Backward-compatible name for semantic provider configuration failures."""


class SemanticProviderUnavailableError(RuntimeError):
    """The configured semantic provider could not be reached safely."""


class SemanticProviderRequestError(RuntimeError):
    """The semantic provider rejected a model or request without exposing payloads."""


class SemanticStructuredOutputModelError(SemanticProviderRequestError):
    """The provider rejected Structured Outputs for the selected model."""


class SemanticStructuredOutputSchemaError(SemanticProviderRequestError):
    """The provider rejected the requested Structured Outputs schema."""


class SemanticOutputError(RuntimeError):
    """The provider returned output that cannot satisfy the semantic contract."""


class LLMProviderConfig(BaseModel):
    """Provider-neutral configuration for one semantic model invocation path."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str | None = None
    required_capabilities: frozenset[LLMCapability] = frozenset()


class CredentialResolver(Protocol):
    """Boundary for server credentials today and injectable BYOK resolution later."""

    def credential_for(self, provider: str) -> str | None: ...


@dataclass(frozen=True)
class EnvironmentCredentialResolver:
    """Receives environment-derived server credentials without exposing them."""

    openai_api_key: str | None = None

    def credential_for(self, provider: str) -> str | None:
        return self.openai_api_key if provider.casefold().strip() == "openai" else None


class SemanticLLM(Protocol):
    """Narrow semantic interface; discovery/search remains outside this boundary."""

    @property
    def capabilities(self) -> frozenset[LLMCapability]: ...

    def generate(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        operation: str,
        required_capabilities: frozenset[LLMCapability] = frozenset(),
        output_schema: dict[str, object] | None = None,
    ) -> str: ...


class OpenAISemanticLLM:
    _capabilities = frozenset(
        {
            LLMCapability.STRUCTURED_OUTPUT,
            LLMCapability.TOOL_CALLING,
            LLMCapability.HOSTED_WEB_SEARCH,
            LLMCapability.STREAMING,
        }
    )

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url

    @property
    def capabilities(self) -> frozenset[LLMCapability]:
        return self._capabilities

    def generate(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        operation: str,
        required_capabilities: frozenset[LLMCapability] = frozenset(),
        output_schema: dict[str, object] | None = None,
    ) -> str:
        _require_capabilities("openai", self.capabilities, required_capabilities)
        if output_schema is not None:
            validate_openai_structured_output_model(model)
        client = create_traced_openai_client(
            api_key=self._api_key,
            trace_name=operation,
            base_url=self._base_url,
        )
        request: dict[str, object] = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if output_schema is not None:
            request["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": operation,
                    "strict": True,
                    "schema": output_schema,
                }
            }
        try:
            response = client.responses.create(**request)
        except (APIConnectionError, APITimeoutError) as exc:
            raise SemanticProviderUnavailableError("OpenAI semantic provider is unavailable. Check connectivity and retry.") from exc
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise SemanticProviderUnavailableError("OpenAI semantic provider is temporarily unavailable. Retry later.") from exc
            if output_schema is not None and exc.status_code == 400:
                error_kind = _structured_output_bad_request_kind(exc)
                if error_kind == "model_unsupported":
                    raise SemanticStructuredOutputModelError(
                        "OpenAI rejected Structured Outputs for the configured semantic model."
                    ) from exc
                if error_kind == "schema_rejected":
                    raise SemanticStructuredOutputSchemaError(
                        "OpenAI rejected the Structured Outputs schema for requirement matching."
                    ) from exc
            raise SemanticProviderRequestError(
                f"OpenAI rejected semantic model '{model}'. Check the configured model and provider access."
            ) from exc
        return response.output_text


OllamaTransport = Callable[[str, dict[str, object]], dict[str, object]]


class OllamaSemanticLLM:
    _capabilities = frozenset({LLMCapability.STRUCTURED_OUTPUT, LLMCapability.LOCAL_INFERENCE})

    def __init__(self, *, base_url: str = "http://localhost:11434", transport: OllamaTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport or self._post

    @property
    def capabilities(self) -> frozenset[LLMCapability]:
        return self._capabilities

    def generate(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        operation: str,
        required_capabilities: frozenset[LLMCapability] = frozenset(),
        output_schema: dict[str, object] | None = None,
    ) -> str:
        del operation, output_schema  # Ollama has no hosted tracing capability in this bounded adapter.
        _require_capabilities("ollama", self.capabilities, required_capabilities)
        try:
            payload = self._transport(
                f"{self._base_url}/api/chat",
                {
                    "model": model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "format": "json",
                },
            )
        except HTTPError as exc:
            if exc.code >= 500:
                raise SemanticProviderUnavailableError("Ollama semantic provider is temporarily unavailable. Retry later.") from exc
            raise SemanticProviderRequestError(
                f"Ollama rejected semantic model '{model}'. Check the configured model."
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SemanticProviderUnavailableError("Ollama semantic provider is unavailable. Check OLLAMA_BASE_URL and retry.") from exc
        message = payload.get("message") if isinstance(payload, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise SemanticOutputError("Ollama returned no usable semantic output.")
        return content

    @staticmethod
    def _post(url: str, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - configured local Ollama endpoint
            result = json.load(response)
        return result if isinstance(result, dict) else {}


class LLMProviderFactory:
    """Registry/factory intentionally limited to semantic providers, not search tools."""

    def __init__(self, credential_resolver: CredentialResolver) -> None:
        self._credential_resolver = credential_resolver

    def create(self, config: LLMProviderConfig) -> SemanticLLM:
        provider = config.provider.casefold().strip()
        if not config.model.strip():
            raise LLMProviderConfigurationError("An LLM model must be configured.")
        if provider == "openai":
            credential = self._credential_resolver.credential_for(provider)
            if not credential:
                raise LLMProviderConfigurationError("OpenAI semantic LLM requires OPENAI_API_KEY.")
            llm: SemanticLLM = OpenAISemanticLLM(api_key=credential, base_url=config.base_url)
        elif provider == "ollama":
            llm = OllamaSemanticLLM(base_url=config.base_url or "http://localhost:11434")
        else:
            raise LLMProviderConfigurationError(
                f"Unsupported LLM provider '{config.provider}'. Supported providers: openai, ollama."
            )
        _require_capabilities(provider, llm.capabilities, config.required_capabilities)
        return llm


class SemanticResponseClient:
    """Compatibility façade so existing prompt-owning agents keep their unchanged inputs."""

    def __init__(self, llm: SemanticLLM, *, operation: str) -> None:
        self.responses = _SemanticResponses(llm, operation=operation)


class _SemanticResponses:
    def __init__(self, llm: SemanticLLM, *, operation: str) -> None:
        self._llm = llm
        self._operation = operation

    def create(self, *, model: str, input: object, **kwargs: object) -> SimpleNamespace:
        required = {LLMCapability.STRUCTURED_OUTPUT}
        if kwargs.get("tools"):
            required.add(LLMCapability.TOOL_CALLING)
        system_prompt, user_prompt = _messages(input)
        return SimpleNamespace(
            output_text=self._llm.generate(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                operation=self._operation,
                required_capabilities=frozenset(required),
                output_schema=_json_schema_from_response_text(kwargs.get("text")),
            )
        )


def _json_schema_from_response_text(value: object) -> dict[str, object] | None:
    """Translate the narrow Responses compatibility shape into a semantic schema."""
    if not isinstance(value, dict):
        return None
    response_format = value.get("format")
    if not isinstance(response_format, dict):
        return None
    schema = response_format.get("schema")
    return schema if response_format.get("type") == "json_schema" and isinstance(schema, dict) else None


def _messages(value: object) -> tuple[str, str]:
    if not isinstance(value, list):
        raise ValueError("Semantic generation requires role-tagged message input.")
    system = ""
    user = ""
    for message in value:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if message.get("role") == "system":
            system = content
        elif message.get("role") == "user":
            user = content
    return system, user


def _require_capabilities(
    provider: str,
    available: frozenset[LLMCapability],
    required: frozenset[LLMCapability],
) -> None:
    unsupported = sorted(capability.value for capability in required - available)
    if unsupported:
        raise LLMProviderConfigurationError(
            f"LLM provider '{provider}' does not support required capabilities: {', '.join(unsupported)}."
        )


_OPENAI_STRUCTURED_OUTPUT_MODEL = re.compile(
    r"^(?:(?:gpt-4o(?:-mini)?|gpt-4\.1(?:-mini|-nano)?|gpt-5(?:-mini|-nano)?)(?:-\d{4}-\d{2}-\d{2})?|gpt-5\.6(?:-(?:luna|terra|sol))?)$"
)


def openai_structured_output_supported(model: str) -> bool:
    """Return only statically-known Structured Outputs compatibility.

    OpenAI's Structured Outputs capability is model-specific. Unknown aliases are
    intentionally not assumed compatible because a request-time 400 is avoidable.
    """
    return bool(_OPENAI_STRUCTURED_OUTPUT_MODEL.fullmatch(model.strip()))


def validate_openai_structured_output_model(model: str) -> None:
    if not openai_structured_output_supported(model):
        raise SemanticProviderConfigurationError(
            "Configured OpenAI requirement-matching model does not have a known "
            "Structured Outputs capability. Configure a supported OpenAI model."
        )


def _structured_output_bad_request_kind(error: APIStatusError) -> str | None:
    """Classify known setup failures without surfacing provider response bodies."""
    detail = str(error).casefold()
    if any(
        phrase in detail
        for phrase in (
            "does not support structured outputs",
            "does not support json_schema",
            "model does not support response_format",
        )
    ):
        return "model_unsupported"
    if any(
        phrase in detail
        for phrase in (
            "invalid schema",
            "schema for response_format",
            "json schema is invalid",
        )
    ):
        return "schema_rejected"
    return None

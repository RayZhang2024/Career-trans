"""Small provider-neutral boundary for Career-trans semantic LLM operations."""

import json
from dataclasses import dataclass
from enum import StrEnum
from types import SimpleNamespace
from typing import Callable, Protocol
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from app.agents.openai_client import create_traced_openai_client


class LLMCapability(StrEnum):
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    HOSTED_WEB_SEARCH = "hosted_web_search"
    LOCAL_INFERENCE = "local_inference"
    STREAMING = "streaming"


class LLMProviderConfigurationError(ValueError):
    """A safe operator-facing configuration error that never includes credentials."""


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
    ) -> str:
        _require_capabilities("openai", self.capabilities, required_capabilities)
        client = create_traced_openai_client(
            api_key=self._api_key,
            trace_name=operation,
            base_url=self._base_url,
        )
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
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
    ) -> str:
        del operation  # Ollama has no hosted tracing capability in this bounded adapter.
        _require_capabilities("ollama", self.capabilities, required_capabilities)
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
        message = payload.get("message") if isinstance(payload, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise RuntimeError("Ollama returned no text content.")
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
            )
        )


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

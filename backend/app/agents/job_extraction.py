import json
from pathlib import Path
from typing import Protocol

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import ValidationError

from app.agents.openai_client import create_traced_openai_client
from app.providers.llm import (
    SemanticOutputError,
    SemanticProviderConfigurationError,
    SemanticProviderRequestError,
    SemanticProviderUnavailableError,
    SemanticStructuredOutputModelError,
    SemanticStructuredOutputSchemaError,
)
from app.providers.openai_structured_output import (
    StrictStructuredOutputSchemaError,
    strict_schema_from_pydantic_model,
)
from app.schemas.job import JobProfile
from app.services.job_profile_normalization import normalize_job_profile


class JobExtractionError(RuntimeError):
    """Raised when a job description cannot be converted into a valid JobProfile."""

    _SAFE_KINDS = frozenset(
        {
            "invalid_output",
            "provider_failure",
            "structured_output_model_unsupported",
            "structured_output_schema_rejected",
            "structured_output_sdk_unsupported",
            "prompt_load_failure",
        }
    )

    def __init__(self, message: str, *, kind: str = "invalid_output") -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def safe_kind(self) -> str:
        return self.kind if self.kind in self._SAFE_KINDS else "unknown"


class JobExtractor(Protocol):
    def extract(self, job_text: str) -> JobProfile:
        """Extract a structured JobProfile from raw job text."""


def _default_prompt_path() -> Path:
    # .../career-trans/backend/app/agents/job_extraction.py -> repo root
    return Path(__file__).resolve().parents[3] / "prompts" / "job_extraction.md"


class OpenAIJobExtractor:
    """OpenAI-backed implementation of JobExtractor.

    The extractor intentionally returns a domain schema rather than provider-specific
    response objects so the provider can be replaced later.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt_path: Path | None = None,
        client: OpenAI | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("An OpenAI API key is required.")

        self._client = client if client is not None else create_traced_openai_client(
            api_key=api_key,
            trace_name="job_extraction",
        )
        self._model = model
        self._prompt_path = prompt_path or _default_prompt_path()

    def extract(self, job_text: str) -> JobProfile:
        prompt = self._load_prompt()
        try:
            schema = strict_schema_from_pydantic_model(JobProfile)
        except StrictStructuredOutputSchemaError as exc:
            raise JobExtractionError(
                "The installed OpenAI SDK cannot build a job-extraction Structured Outputs schema.",
                kind="structured_output_sdk_unsupported",
            ) from exc

        try:
            response = self._client.responses.create(
                model=self._model,
                input=[
                    {
                        "role": "system",
                        "content": prompt,
                    },
                    {
                        "role": "user",
                        "content": (
                            "Extract the following job advert into the supplied schema.\n\n"
                            f"JOB ADVERT:\n{job_text}"
                        ),
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "job_profile",
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
        except SemanticStructuredOutputModelError as exc:
            raise JobExtractionError(
                "The configured semantic model does not support job-extraction Structured Outputs.",
                kind="structured_output_model_unsupported",
            ) from exc
        except SemanticStructuredOutputSchemaError as exc:
            raise JobExtractionError(
                "The job-extraction Structured Outputs schema was rejected by the provider.",
                kind="structured_output_schema_rejected",
            ) from exc
        except SemanticProviderConfigurationError as exc:
            raise JobExtractionError(
                "The configured semantic model does not support job-extraction Structured Outputs.",
                kind="structured_output_model_unsupported",
            ) from exc
        except (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            SemanticOutputError,
            SemanticProviderRequestError,
            SemanticProviderUnavailableError,
        ) as exc:
            raise JobExtractionError(
                "The job-extraction provider could not complete the request.",
                kind="provider_failure",
            ) from exc

        raw_output = response.output_text.strip()

        # Be tolerant of fenced JSON even though the prompt asks for JSON only.
        if raw_output.startswith("```"):
            raw_output = self._strip_json_fence(raw_output)

        try:
            payload = json.loads(raw_output)
            return normalize_job_profile(JobProfile.model_validate(payload))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise JobExtractionError(
                "The model returned output that did not validate as JobProfile.",
                kind="invalid_output",
            ) from exc

    def _load_prompt(self) -> str:
        try:
            return self._prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise JobExtractionError(
                "Unable to load the job-extraction prompt.",
                kind="prompt_load_failure",
            ) from exc

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

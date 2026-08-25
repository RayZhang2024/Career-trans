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
)
from app.schemas.candidate import CandidateMatchingProfile
from app.schemas.job import JobProfile
from app.schemas.matching import RequirementMatchSet


class RequirementMatchingError(RuntimeError):
    """Raised when candidate/job matching cannot produce valid structured output."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


class RequirementMatcher(Protocol):
    def match(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateMatchingProfile,
    ) -> RequirementMatchSet:
        """Match every job requirement against candidate evidence."""


def _default_prompt_path() -> Path:
    return Path(__file__).resolve().parents[3] / "prompts" / "requirement_matching.md"


class OpenAIRequirementMatcher:
    _MAX_STRUCTURAL_ATTEMPTS = 2

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
            trace_name="requirement_matching",
        )
        self._model = model
        self._prompt_path = prompt_path or _default_prompt_path()

    def match(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateMatchingProfile,
    ) -> RequirementMatchSet:
        prompt = self._load_prompt()
        schema = RequirementMatchSet.model_json_schema()

        payload = {
            "job_profile": job_profile.model_dump(mode="json"),
            "candidate_context": candidate_context.model_dump(mode="json"),
        }

        for attempt in range(self._MAX_STRUCTURAL_ATTEMPTS):
            try:
                result = self._match_once(
                    prompt=prompt,
                    schema=schema,
                    payload=payload,
                )
                self._validate_result(result, job_profile, candidate_context)
                return result
            except RequirementMatchingError as exc:
                if exc.kind == "provider_failure" or attempt + 1 == self._MAX_STRUCTURAL_ATTEMPTS:
                    raise

        raise AssertionError("Requirement matching attempts were exhausted unexpectedly.")

    def _match_once(
        self,
        *,
        prompt: str,
        schema: dict[str, object],
        payload: dict[str, object],
    ) -> RequirementMatchSet:
        try:
            response = self._client.responses.create(
                model=self._model,
                input=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": (
                            "Match every job requirement against the candidate context.\n\n"
                            f"JSON schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
                            f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}"
                        ),
                    },
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "requirement_match_set",
                        "strict": True,
                        "schema": self._strict_json_schema(schema),
                    }
                },
            )
        except (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            SemanticOutputError,
            SemanticProviderConfigurationError,
            SemanticProviderRequestError,
            SemanticProviderUnavailableError,
        ) as exc:
            raise RequirementMatchingError(
                "The requirement-matching provider could not complete the request.",
                kind="provider_failure",
            ) from exc

        raw_output = response.output_text.strip()
        if raw_output.startswith("```"):
            raw_output = self._strip_json_fence(raw_output)

        try:
            return RequirementMatchSet.model_validate(json.loads(raw_output))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise RequirementMatchingError(
                "The model returned output that did not validate as RequirementMatchSet.",
                kind="invalid_output",
            ) from exc

    def _validate_result(
        self,
        result: RequirementMatchSet,
        job_profile: JobProfile,
        candidate_context: CandidateMatchingProfile,
    ) -> None:
        requirements = job_profile.requirements
        if len(result.matches) != len(requirements):
            raise RequirementMatchingError(
                "The matcher did not return exactly one match per job requirement.",
                kind="wrong_match_count",
            )

        expected_indexes = list(range(len(requirements)))
        returned_indexes = sorted(match.requirement_index for match in result.matches)
        if returned_indexes != expected_indexes:
            raise RequirementMatchingError(
                "Requirement indexes are missing, duplicated, or out of range.",
                kind="invalid_indexes",
            )

        valid_evidence_ids = {item.evidence_id for item in candidate_context.evidence}

        for match in result.matches:
            canonical_requirement = requirements[match.requirement_index]
            if match.requirement != canonical_requirement:
                raise RequirementMatchingError(
                    "The matcher altered a job requirement instead of preserving it.",
                    kind="altered_requirement",
                )

            unknown_ids = set(match.evidence_ids) - valid_evidence_ids
            if unknown_ids:
                raise RequirementMatchingError(
                    "The matcher referenced evidence IDs that do not exist in the "
                    "candidate context.",
                    kind="unknown_evidence_ids",
                )

    def _load_prompt(self) -> str:
        try:
            return self._prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RequirementMatchingError(
                f"Unable to load requirement matching prompt: {self._prompt_path}",
                kind="prompt_load_failure",
            ) from exc

    @staticmethod
    def _strict_json_schema(schema: dict[str, object]) -> dict[str, object]:
        """Make the Pydantic schema compatible with Responses strict JSON Schema mode."""
        strict_schema = json.loads(json.dumps(schema))

        def visit(value: object) -> None:
            if isinstance(value, dict):
                properties = value.get("properties")
                if isinstance(properties, dict):
                    value["additionalProperties"] = False
                    value["required"] = list(properties)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(strict_schema)
        return strict_schema

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

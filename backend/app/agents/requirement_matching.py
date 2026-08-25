import json
from pathlib import Path
from typing import Any, Protocol

from langsmith import run_helpers
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
from app.schemas.candidate import CandidateMatchingProfile
from app.schemas.job import JobProfile
from app.schemas.matching import (
    MatchType,
    RequirementMatchingJobProfile,
    RequirementMatchingRequirement,
    RequirementMatch,
    RequirementMatchSet,
    SemanticRequirementMatchSet,
)


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
        schema = self._openai_strict_schema()

        payload = {
            "job_profile": RequirementMatchingJobProfile(
                requirements=[
                    RequirementMatchingRequirement(
                        text=requirement.text,
                        importance=requirement.importance,
                        category=requirement.category,
                    )
                    for requirement in job_profile.requirements
                ]
            ).model_dump(mode="json"),
            "candidate_context": candidate_context.model_dump(mode="json"),
        }

        previous_failure_kind: str | None = None
        for attempt in range(self._MAX_STRUCTURAL_ATTEMPTS):
            attempt_number = attempt + 1
            metadata = {
                "application_attempt": attempt_number,
                "max_application_attempts": self._MAX_STRUCTURAL_ATTEMPTS,
                "requirement_count": len(job_profile.requirements),
                "candidate_evidence_count": len(candidate_context.evidence),
                "attempt_scope": "application_structural",
                "previous_failure_kind": previous_failure_kind,
            }
            try:
                # The wrapped OpenAI ``requirement_matching`` run is the
                # application boundary we need to annotate.  Scoping metadata
                # here keeps it on that provider run without creating a
                # duplicate custom span or exposing prompt/evidence content.
                with run_helpers.tracing_context(metadata=metadata):
                    result = self._match_once(
                        prompt=prompt,
                        schema=schema,
                        payload=payload,
                    )
                self._validate_result(result, job_profile, candidate_context)
                return self._attach_canonical_requirements(result, job_profile)
            except RequirementMatchingError as exc:
                if (
                    exc.kind not in self._RETRYABLE_FAILURE_KINDS
                    or attempt_number == self._MAX_STRUCTURAL_ATTEMPTS
                ):
                    raise
                previous_failure_kind = exc.kind

        raise AssertionError("Requirement matching attempts were exhausted unexpectedly.")

    def _match_once(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        payload: dict[str, object],
    ) -> SemanticRequirementMatchSet:
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
                        "schema": schema,
                    }
                },
            )
        except SemanticStructuredOutputModelError as exc:
            raise RequirementMatchingError(
                "The configured semantic model does not support requirement-matching Structured Outputs.",
                kind="structured_output_model_unsupported",
            ) from exc
        except SemanticStructuredOutputSchemaError as exc:
            raise RequirementMatchingError(
                "The requirement-matching Structured Outputs schema was rejected by the provider.",
                kind="structured_output_schema_rejected",
            ) from exc
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
            return SemanticRequirementMatchSet.model_validate(json.loads(raw_output))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise RequirementMatchingError(
                "The model returned output that did not validate as semantic requirement matches.",
                kind="invalid_output",
            ) from exc

    def _validate_result(
        self,
        result: SemanticRequirementMatchSet,
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
            unknown_ids = set(match.evidence_ids) - valid_evidence_ids
            if unknown_ids:
                raise RequirementMatchingError(
                    "The matcher referenced evidence IDs that do not exist in the "
                    "candidate context.",
                    kind="unknown_evidence_ids",
                )

    @staticmethod
    def _attach_canonical_requirements(
        result: SemanticRequirementMatchSet,
        job_profile: JobProfile,
    ) -> RequirementMatchSet:
        return RequirementMatchSet(
            matches=[
                RequirementMatch(
                    requirement_index=match.requirement_index,
                    requirement=job_profile.requirements[match.requirement_index],
                    match_type=match.match_type,
                    score=match.score,
                    evidence_ids=match.evidence_ids,
                    reasoning=OpenAIRequirementMatcher._deterministic_reasoning(
                        match.match_type,
                        match.evidence_ids,
                    ),
                )
                for match in result.matches
            ]
        )

    @staticmethod
    def _deterministic_reasoning(match_type: MatchType, evidence_ids: list[str]) -> str:
        """Keep API explanations stable without model-authored boilerplate."""
        has_evidence = bool(evidence_ids)
        if match_type is MatchType.DEMONSTRATED:
            return (
                "Supplied candidate evidence directly supports this requirement."
                if has_evidence
                else "The requirement was classified as directly demonstrated."
            )
        if match_type is MatchType.TRANSFERABLE:
            return (
                "Supplied candidate evidence supports an adjacent transferable capability."
                if has_evidence
                else "The requirement was classified as a transferable capability."
            )
        if match_type is MatchType.INFERRED:
            return "Candidate evidence is plausible but insufficient to confirm this requirement."
        if match_type is MatchType.INCOMPATIBLE:
            return "Supplied candidate evidence conflicts with this requirement."
        if match_type is MatchType.UNKNOWN:
            return "Candidate evidence is insufficient to confirm this requirement."
        return "No meaningful supporting candidate evidence was supplied."

    def _load_prompt(self) -> str:
        try:
            return self._prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RequirementMatchingError(
                f"Unable to load requirement matching prompt: {self._prompt_path}",
                kind="prompt_load_failure",
            ) from exc

    _RETRYABLE_FAILURE_KINDS = frozenset(
        {
            "invalid_output",
            "wrong_match_count",
            "invalid_indexes",
            "unknown_evidence_ids",
        }
    )

    @staticmethod
    def _openai_strict_schema() -> dict[str, Any]:
        """Use the SDK Pydantic builder, then remove unsupported default keywords."""
        try:
            # This is the same SDK helper used by ``responses.parse`` to derive
            # a strict provider schema from a Pydantic model.
            from openai.lib._parsing import type_to_response_format_param
        except ImportError as exc:  # pragma: no cover - guarded by the installed SDK
            raise RequirementMatchingError(
                "The installed OpenAI SDK cannot build a native Structured Outputs schema.",
                kind="structured_output_sdk_unsupported",
            ) from exc

        response_format = type_to_response_format_param(SemanticRequirementMatchSet)
        json_schema = response_format.get("json_schema")
        schema = json_schema.get("schema") if isinstance(json_schema, dict) else None
        if not isinstance(schema, dict):
            raise RequirementMatchingError(
                "The OpenAI SDK could not build a native Structured Outputs schema.",
                kind="structured_output_sdk_unsupported",
            )

        normalized = json.loads(json.dumps(schema))

        def remove_defaults(value: object) -> None:
            if isinstance(value, dict):
                value.pop("default", None)
                for child in value.values():
                    remove_defaults(child)
            elif isinstance(value, list):
                for child in value:
                    remove_defaults(child)

        remove_defaults(normalized)
        return normalized

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

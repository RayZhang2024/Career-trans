import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Protocol

from langchain_core.runnables import RunnableConfig
from langsmith import traceable
from langsmith.run_trees import RunTree
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


_runnable_config: ContextVar[RunnableConfig | None] = ContextVar(
    "requirement_matching_runnable_config",
    default=None,
)


@contextmanager
def requirement_matching_tracing_config(
    config: RunnableConfig | None,
) -> Iterator[None]:
    """Make the active LangGraph config available to the traced attempt boundary."""
    token = _runnable_config.set(config)
    try:
        yield
    finally:
        _runnable_config.reset(token)


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
            "job_profile": job_profile.model_dump(mode="json"),
            "candidate_context": candidate_context.model_dump(mode="json"),
        }

        for attempt in range(self._MAX_STRUCTURAL_ATTEMPTS):
            attempt_number = attempt + 1
            metadata = {
                "attempt": attempt_number,
                "max_attempts": self._MAX_STRUCTURAL_ATTEMPTS,
                "requirement_count": len(job_profile.requirements),
                "candidate_evidence_count": len(candidate_context.evidence),
                "attempt_scope": "application_structural",
                "validation_status": "pending",
                "failure_kind": None,
                "retrying": False,
            }
            config = _runnable_config.get()
            try:
                return self._run_attempt(
                    prompt=prompt,
                    schema=schema,
                    payload=payload,
                    job_profile=job_profile,
                    candidate_context=candidate_context,
                    attempt_number=attempt_number,
                    metadata=metadata,
                    config=config,
                    langsmith_extra={
                        "metadata": metadata,
                        "parent": RunTree.from_runnable_config(config),
                    },
                )
            except RequirementMatchingError as exc:
                if (
                    exc.kind not in self._RETRYABLE_FAILURE_KINDS
                    or attempt_number == self._MAX_STRUCTURAL_ATTEMPTS
                ):
                    raise

        raise AssertionError("Requirement matching attempts were exhausted unexpectedly.")

    @traceable(
        name="requirement_matching_attempt",
        run_type="chain",
        process_inputs=lambda _inputs: {},
        process_outputs=lambda _outputs: {},
    )
    def _run_attempt(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        payload: dict[str, object],
        job_profile: JobProfile,
        candidate_context: CandidateMatchingProfile,
        attempt_number: int,
        metadata: dict[str, object],
        config: RunnableConfig | None = None,
        run_tree: RunTree | None = None,
    ) -> RequirementMatchSet:
        """Perform one application structural attempt under an explicit graph parent."""
        del config  # Consumed by LangSmith's traceable wrapper for parent propagation.
        result: SemanticRequirementMatchSet | None = None
        try:
            result = self._match_once(
                prompt=prompt,
                schema=schema,
                payload=payload,
            )
            self._validate_result(result, job_profile, candidate_context)
        except RequirementMatchingError as exc:
            retryable = (
                exc.kind in self._RETRYABLE_FAILURE_KINDS
                and attempt_number < self._MAX_STRUCTURAL_ATTEMPTS
            )
            self._record_attempt_metadata(
                run_tree,
                {
                    **metadata,
                    "validation_status": "failed",
                    "failure_kind": exc.kind,
                    "retrying": retryable,
                    "result_match_count": len(result.matches) if result is not None else None,
                },
            )
            raise

        self._record_attempt_metadata(
            run_tree,
            {
                **metadata,
                "validation_status": "passed",
                "failure_kind": None,
                "retrying": False,
                "result_match_count": len(result.matches),
            },
        )
        return self._attach_canonical_requirements(result, job_profile)

    @staticmethod
    def _record_attempt_metadata(
        run_tree: RunTree | None,
        metadata: dict[str, object],
    ) -> None:
        if run_tree is not None:
            run_tree.metadata.update(metadata)

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
                    reasoning=match.reasoning,
                )
                for match in result.matches
            ]
        )

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

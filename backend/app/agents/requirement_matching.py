import json
from copy import deepcopy
from dataclasses import dataclass
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
from app.providers.openai_structured_output import (
    StrictStructuredOutputSchemaError,
    strict_schema_from_pydantic_model,
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

    _SAFE_KINDS = frozenset(
        {
            "invalid_output",
            "wrong_match_count",
            "invalid_indexes",
            "unknown_evidence_ids",
            "provider_failure",
            "structured_output_model_unsupported",
            "structured_output_schema_rejected",
            "structured_output_sdk_unsupported",
            "prompt_load_failure",
        }
    )

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def safe_kind(self) -> str:
        """Expose only the closed set of diagnostic categories at API boundaries."""
        return self.kind if self.kind in self._SAFE_KINDS else "unknown"


class RequirementMatcher(Protocol):
    def match(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateMatchingProfile,
    ) -> RequirementMatchSet:
        """Match every job requirement against candidate evidence."""


def _default_prompt_path() -> Path:
    return Path(__file__).resolve().parents[3] / "prompts" / "requirement_matching.md"


@dataclass(frozen=True)
class _RequirementMatchingContract:
    """Per-request constraints for the model-owned semantic fields only.

    Canonical requirements and the candidate evidence objects remain application
    owned. This compact contract makes the permitted requirement indexes,
    evidence references, and cardinality explicit to both Structured Outputs
    and the model instruction.
    """

    expected_match_count: int
    allowed_requirement_indexes: tuple[int, ...]
    allowed_evidence_ids: tuple[str, ...]

    @classmethod
    def from_inputs(
        cls,
        job_profile: JobProfile,
        candidate_context: CandidateMatchingProfile,
    ) -> "_RequirementMatchingContract":
        return cls(
            expected_match_count=len(job_profile.requirements),
            allowed_requirement_indexes=tuple(range(len(job_profile.requirements))),
            # Preserve the compact profile's canonical evidence order while
            # making duplicate IDs harmless in the provider schema.
            allowed_evidence_ids=tuple(dict.fromkeys(
                evidence.evidence_id for evidence in candidate_context.evidence
            )),
        )

    def model_input(self) -> dict[str, object]:
        return {
            "expected_match_count": self.expected_match_count,
            "allowed_requirement_indexes": list(self.allowed_requirement_indexes),
            "allowed_evidence_ids": list(self.allowed_evidence_ids),
        }

    @staticmethod
    def corrective_guidance(failure_kind: str | None) -> str | None:
        """Return only a safe, category-level correction for attempt two."""
        guidance = {
            "invalid_output": "Return valid JSON that conforms exactly to the supplied schema.",
            "wrong_match_count": (
                "Return exactly one match for every allowed requirement index."
            ),
            "invalid_indexes": (
                "Use every allowed requirement index exactly once; do not repeat, omit, "
                "or invent indexes."
            ),
            "unknown_evidence_ids": (
                "Use only exact IDs from allowed_evidence_ids, or an empty evidence_ids list "
                "when no supplied evidence supports the requirement."
            ),
        }
        return guidance.get(failure_kind)


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
        contract = _RequirementMatchingContract.from_inputs(job_profile, candidate_context)
        schema = self._openai_strict_schema_for_contract(contract)

        payload = {
            "matching_contract": contract.model_input(),
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
                        corrective_guidance=contract.corrective_guidance(previous_failure_kind),
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
        corrective_guidance: str | None,
    ) -> SemanticRequirementMatchSet:
        correction = (
            f"\n\nCORRECTIVE RETRY:\n{corrective_guidance}"
            if corrective_guidance is not None
            else ""
        )
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
                            f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}{correction}"
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
            return strict_schema_from_pydantic_model(SemanticRequirementMatchSet)
        except StrictStructuredOutputSchemaError as exc:
            raise RequirementMatchingError(
                "The installed OpenAI SDK cannot build a native Structured Outputs schema.",
                kind="structured_output_sdk_unsupported",
            ) from exc

    @classmethod
    def _openai_strict_schema_for_contract(
        cls,
        contract: _RequirementMatchingContract,
    ) -> dict[str, Any]:
        """Constrain the native SDK schema to this request's canonical contract.

        The provider receives the same strict SDK-derived base schema as before,
        with supported array bounds and enums narrowing only model-owned fields.
        Application validation remains the final fail-closed authority.
        """
        schema = deepcopy(cls._openai_strict_schema())
        matches = schema["properties"]["matches"]
        semantic_match = schema["$defs"]["SemanticRequirementMatch"]
        properties = semantic_match["properties"]

        matches["minItems"] = contract.expected_match_count
        matches["maxItems"] = contract.expected_match_count
        properties["requirement_index"] = {
            "type": "integer",
            "enum": list(contract.allowed_requirement_indexes),
        }

        evidence_ids = properties["evidence_ids"]
        if contract.allowed_evidence_ids:
            evidence_ids["items"] = {
                "type": "string",
                "enum": list(contract.allowed_evidence_ids),
            }
        else:
            # An empty enum is not a useful provider contract. A zero upper
            # bound permits the valid empty list while rejecting invented IDs.
            evidence_ids["maxItems"] = 0

        return schema

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

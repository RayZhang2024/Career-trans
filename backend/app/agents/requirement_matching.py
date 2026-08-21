import json
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from app.schemas.candidate import CandidateContext
from app.schemas.job import JobProfile
from app.schemas.matching import RequirementMatchSet


class RequirementMatchingError(RuntimeError):
    """Raised when candidate/job matching cannot produce valid structured output."""


class RequirementMatcher(Protocol):
    def match(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateContext,
    ) -> RequirementMatchSet:
        """Match every job requirement against candidate evidence."""


def _default_prompt_path() -> Path:
    return Path(__file__).resolve().parents[3] / "prompts" / "requirement_matching.md"


class OpenAIRequirementMatcher:
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

        self._client = client or OpenAI(api_key=api_key)
        self._model = model
        self._prompt_path = prompt_path or _default_prompt_path()

    def match(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateContext,
    ) -> RequirementMatchSet:
        prompt = self._load_prompt()
        schema = RequirementMatchSet.model_json_schema()

        payload = {
            "job_profile": job_profile.model_dump(mode="json"),
            "candidate_context": candidate_context.model_dump(mode="json"),
        }

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
        )

        raw_output = response.output_text.strip()
        if raw_output.startswith("```"):
            raw_output = self._strip_json_fence(raw_output)

        try:
            result = RequirementMatchSet.model_validate(json.loads(raw_output))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise RequirementMatchingError(
                "The model returned output that did not validate as RequirementMatchSet."
            ) from exc

        self._validate_result(result, job_profile, candidate_context)
        return result

    def _validate_result(
        self,
        result: RequirementMatchSet,
        job_profile: JobProfile,
        candidate_context: CandidateContext,
    ) -> None:
        requirements = job_profile.requirements
        if len(result.matches) != len(requirements):
            raise RequirementMatchingError(
                "The matcher did not return exactly one match per job requirement."
            )

        expected_indexes = list(range(len(requirements)))
        returned_indexes = sorted(match.requirement_index for match in result.matches)
        if returned_indexes != expected_indexes:
            raise RequirementMatchingError(
                "Requirement indexes are missing, duplicated, or out of range."
            )

        valid_evidence_ids = {item.evidence_id for item in candidate_context.evidence}

        for match in result.matches:
            canonical_requirement = requirements[match.requirement_index]
            if match.requirement != canonical_requirement:
                raise RequirementMatchingError(
                    "The matcher altered a job requirement instead of preserving it."
                )

            unknown_ids = set(match.evidence_ids) - valid_evidence_ids
            if unknown_ids:
                raise RequirementMatchingError(
                    "The matcher referenced evidence IDs that do not exist in the "
                    f"candidate context: {sorted(unknown_ids)}"
                )

    def _load_prompt(self) -> str:
        try:
            return self._prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RequirementMatchingError(
                f"Unable to load requirement matching prompt: {self._prompt_path}"
            ) from exc

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

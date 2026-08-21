import json
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import CareerAlignmentJudgement
from app.schemas.job import JobProfile


class CareerAlignmentError(RuntimeError):
    """Raised when career alignment cannot produce valid structured output."""


class CareerAlignmentAgent(Protocol):
    def assess(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateContext,
        fit_assessment: FitAssessment,
    ) -> CareerAlignmentJudgement:
        """Judge the role against the candidate's stated career direction."""


def _default_prompt_path() -> Path:
    return Path(__file__).resolve().parents[3] / "prompts" / "career_alignment.md"


class OpenAICareerAlignmentAgent:
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

    def assess(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateContext,
        fit_assessment: FitAssessment,
    ) -> CareerAlignmentJudgement:
        prompt = self._load_prompt()
        schema = CareerAlignmentJudgement.model_json_schema()
        payload = {
            "job_profile": job_profile.model_dump(mode="json"),
            "candidate_context": {
                "profile_text": candidate_context.profile_text,
                "career_strategy_text": candidate_context.career_strategy_text,
                "job_search_criteria_text": (
                    candidate_context.job_search_criteria_text
                ),
                "eligibility": candidate_context.eligibility.model_dump(mode="json"),
            },
            "fit_assessment": fit_assessment.model_dump(mode="json"),
        }

        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        "Assess this role against the candidate's stated career "
                        "strategy and preferences.\n\n"
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
            return CareerAlignmentJudgement.model_validate(json.loads(raw_output))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise CareerAlignmentError(
                "The model returned output that did not validate as "
                "CareerAlignmentJudgement."
            ) from exc

    def _load_prompt(self) -> str:
        try:
            return self._prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CareerAlignmentError(
                f"Unable to load career alignment prompt: {self._prompt_path}"
            ) from exc

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

import json
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from app.schemas.candidate import CandidateContext
from app.schemas.discovery import JobListing
from app.schemas.job_ranking import JobRelevanceAssessment


class JobRelevanceError(RuntimeError):
    pass


class JobRelevanceAgent(Protocol):
    def assess(self, job: JobListing, candidate: CandidateContext) -> JobRelevanceAssessment: ...


class OpenAIJobRelevanceAgent:
    def __init__(self, *, api_key: str, model: str, client: OpenAI | None = None) -> None:
        if not api_key and client is None:
            raise ValueError("An OpenAI API key is required.")
        self._client = client or OpenAI(api_key=api_key)
        self._model = model

    def assess(self, job: JobListing, candidate: CandidateContext) -> JobRelevanceAssessment:
        payload = {
            "job": {"title": job.title, "description": job.description},
            "candidate": {
                "profile_text": candidate.profile_text,
                "skills_text": candidate.skills_text,
                "career_strategy_text": candidate.career_strategy_text,
                "job_search_criteria_text": candidate.job_search_criteria_text,
            },
        }
        return self._request("job_relevance.md", payload, JobRelevanceAssessment)

    def _request(self, prompt_name: str, payload: dict[str, object], schema: type[JobRelevanceAssessment]) -> JobRelevanceAssessment:
        prompt = (Path(__file__).resolve().parents[3] / "prompts" / prompt_name).read_text(encoding="utf-8")
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"JSON schema:\n{json.dumps(schema.model_json_schema())}\n\nINPUT:\n{json.dumps(payload)}"},
            ],
        )
        raw = response.output_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return schema.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise JobRelevanceError("The model returned invalid relevance output.") from exc

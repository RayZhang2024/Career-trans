import json
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from app.agents.openai_client import create_traced_openai_client
from app.schemas.discovery import JobListing
from app.schemas.job_ranking import JobArchetypeAssessment


class JobArchetypeError(RuntimeError):
    pass


class JobArchetypeAgent(Protocol):
    def classify(self, job: JobListing) -> JobArchetypeAssessment: ...


class OpenAIJobArchetypeAgent:
    def __init__(self, *, api_key: str, model: str, client: OpenAI | None = None) -> None:
        if not api_key and client is None:
            raise ValueError("An OpenAI API key is required.")
        self._client = client if client is not None else create_traced_openai_client(
            api_key=api_key,
            trace_name="job_archetype",
        )
        self._model = model

    def classify(self, job: JobListing) -> JobArchetypeAssessment:
        prompt = (Path(__file__).resolve().parents[3] / "prompts" / "job_archetype.md").read_text(encoding="utf-8")
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"JSON schema:\n{json.dumps(JobArchetypeAssessment.model_json_schema())}\n\nJOB:\n{json.dumps({'title': job.title, 'description': job.description})}"},
            ],
        )
        raw = response.output_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return JobArchetypeAssessment.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise JobArchetypeError("The model returned invalid archetype output.") from exc

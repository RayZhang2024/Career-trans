import json
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from app.agents.openai_client import create_traced_openai_client
from app.schemas.candidate import CandidateContext
from app.services.candidate_profile_compaction import candidate_search_profile
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
        self._client = client if client is not None else create_traced_openai_client(
            api_key=api_key,
            trace_name="job_relevance",
        )
        self._model = model

    def assess(self, job: JobListing, candidate: CandidateContext) -> JobRelevanceAssessment:
        profile = candidate_search_profile(candidate)
        candidate_payload = profile.model_dump(mode="json")
        self._drop_empty_adviser(candidate_payload)
        payload = {
            "job": {"title": job.title, "description": job.description},
            "candidate": candidate_payload,
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

    @staticmethod
    def _drop_empty_adviser(payload: dict[str, object]) -> None:
        adviser = payload.get("adviser")
        if isinstance(adviser, dict) and not any(adviser.values()):
            payload.pop("adviser", None)

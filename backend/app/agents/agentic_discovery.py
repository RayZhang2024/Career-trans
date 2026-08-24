import json
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from app.agents.openai_client import create_traced_openai_client
from app.schemas.agentic_discovery import ExtractedVacancy, PageContent, SearchStrategy
from app.schemas.candidate import CandidateCareerProfile, CandidateSearchProfile


class SearchStrategyGenerator(Protocol):
    def generate(
        self,
        search_profile: CandidateSearchProfile,
        career_profile: CandidateCareerProfile,
        limit: int,
    ) -> list[SearchStrategy]: ...


class PageVacancyExtractor(Protocol):
    def extract(self, page: PageContent) -> ExtractedVacancy | None: ...


class OpenAISearchStrategyGenerator:
    def __init__(self, *, api_key: str, model: str, client: OpenAI | None = None) -> None:
        self._client = client or create_traced_openai_client(api_key=api_key, trace_name="search_strategy_generation")
        self._model = model

    def generate(self, search_profile: CandidateSearchProfile, career_profile: CandidateCareerProfile, limit: int) -> list[SearchStrategy]:
        payload = {
            "search_profile": search_profile.model_dump(mode="json"),
            "career_profile": career_profile.model_dump(mode="json"),
            "max_strategies": limit,
        }
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": _prompt("search_strategy_generation.md")},
                {"role": "user", "content": f"JSON schema:\n{json.dumps({'type': 'array', 'items': SearchStrategy.model_json_schema()})}\n\nINPUT:\n{json.dumps(payload)}"},
            ],
        )
        try:
            return [SearchStrategy.model_validate(item) for item in json.loads(_clean(response.output_text))][:limit]
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise RuntimeError("The model returned invalid search strategies.") from exc


class OpenAIPageVacancyExtractor:
    def __init__(self, *, api_key: str, model: str, client: OpenAI | None = None) -> None:
        self._client = client or create_traced_openai_client(api_key=api_key, trace_name="web_vacancy_extraction")
        self._model = model

    def extract(self, page: PageContent) -> ExtractedVacancy | None:
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": _prompt("web_vacancy_extraction.md")},
                {"role": "user", "content": f"JSON schema:\n{json.dumps(ExtractedVacancy.model_json_schema())}\n\nPUBLIC PAGE:\n{page.html[:100_000]}"},
            ],
        )
        try:
            return ExtractedVacancy.model_validate(json.loads(_clean(response.output_text)))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise RuntimeError("The model returned invalid vacancy extraction output.") from exc


def _prompt(name: str) -> str:
    return (Path(__file__).resolve().parents[3] / "prompts" / name).read_text(encoding="utf-8")


def _clean(value: str) -> str:
    return value.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

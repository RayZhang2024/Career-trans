"""One bounded provider call for a committed clarification-area selection."""

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.providers.llm import SemanticOutputError
from app.providers.openai_structured_output import strict_schema_from_pydantic_model
from app.schemas.candidate_adviser import (
    CandidateAdviserQuestionGenerationInput,
    CandidateAdviserSemanticInput,
    ProviderRoundQuestionSet,
)
from app.services.candidate_adviser_references import candidate_adviser_reference_catalog


class CandidateAdviserQuestionGenerator(Protocol):
    def generate(self, *, generation_input: CandidateAdviserQuestionGenerationInput) -> ProviderRoundQuestionSet: ...


class SemanticCandidateAdviserQuestionGenerator:
    def __init__(self, client: object, model: str) -> None:
        self._client = client
        self._model = model

    def generate(self, *, generation_input: CandidateAdviserQuestionGenerationInput) -> ProviderRoundQuestionSet:
        prompt = (Path(__file__).resolve().parents[3] / "prompts" / "candidate_adviser_questions.md").read_text(encoding="utf-8")
        payload = generation_input.model_dump(mode="json")
        semantic_input = CandidateAdviserSemanticInput(
            intake=generation_input.intake,
            structured_cv=generation_input.structured_cv,
            career_evidence=generation_input.career_evidence,
            clarifications=generation_input.clarifications,
        )
        catalog = candidate_adviser_reference_catalog(semantic_input)
        schema = strict_schema_from_pydantic_model(ProviderRoundQuestionSet)
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"JSON schema:\n{json.dumps(schema)}\n\nALLOWED_SOURCE_REFERENCES:\n{json.dumps(catalog)}\n\nINPUT:\n{json.dumps(payload, ensure_ascii=False)}"},
            ],
            text={"format": {"type": "json_schema", "name": "candidate_adviser_round_questions", "strict": True, "schema": schema}},
        )
        try:
            return ProviderRoundQuestionSet.model_validate(
                json.loads(response.output_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError("Candidate Adviser returned invalid grouped clarification questions.") from exc

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.providers.llm import SemanticOutputError
from app.providers.openai_structured_output import (
    StrictStructuredOutputSchemaError,
    strict_schema_from_pydantic_model,
)
from app.schemas.cv_ingestion import CandidateCVData, ExtractedCVDocument


class CVSemanticInterpreter(Protocol):
    def interpret(self, documents: list[ExtractedCVDocument]) -> CandidateCVData: ...


class SemanticCVInterpreter:
    """Structured interpretation through the provider-neutral response façade."""

    def __init__(self, client: object, model: str) -> None:
        self._client = client
        self._model = model

    def interpret(self, documents: list[ExtractedCVDocument]) -> CandidateCVData:
        prompt = (Path(__file__).resolve().parents[3] / "prompts" / "cv_evidence_extraction.md").read_text(encoding="utf-8")
        payload = {"documents": [document.model_dump(mode="json") for document in documents]}
        try:
            schema = strict_schema_from_pydantic_model(CandidateCVData)
        except StrictStructuredOutputSchemaError as exc:
            raise SemanticOutputError(
                "The installed semantic SDK cannot build a CV interpretation Structured Outputs schema."
            ) from exc
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"INPUT:\n{json.dumps(payload)}"},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "candidate_cv_data",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        try:
            return CandidateCVData.model_validate(json.loads(response.output_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError("CV interpretation returned invalid structured data.") from exc

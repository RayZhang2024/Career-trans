import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.providers.llm import SemanticOutputError
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
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"JSON schema:\n{json.dumps(CandidateCVData.model_json_schema())}\n\nINPUT:\n{json.dumps(payload)}"},
            ],
        )
        try:
            return CandidateCVData.model_validate(json.loads(response.output_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError("CV interpretation returned invalid structured data.") from exc

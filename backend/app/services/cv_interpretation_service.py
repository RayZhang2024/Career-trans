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
        allowed_documents = sorted({document.provenance.document_sha256 for document in documents})
        allowed_segments = sorted({segment_id for document in documents for segment_id in document.provenance.segment_ids})
        if not allowed_documents or not allowed_segments:
            raise SemanticOutputError("CV interpretation requires source documents with source segments.")
        for document in documents:
            provenance_ids = set(document.provenance.segment_ids)
            segment_ids = {segment.segment_id for segment in document.segments}
            if not provenance_ids or provenance_ids != segment_ids:
                raise SemanticOutputError("CV source segment identifiers are inconsistent.")
        provenance_schema = schema["$defs"]["EvidenceProvenance"]["properties"]
        provenance_schema["document_sha256"]["enum"] = allowed_documents
        provenance_schema["segment_ids"]["items"]["enum"] = allowed_segments
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

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.providers.llm import SemanticOutputError
from app.schemas.candidate_adviser import CandidateAdviserAssessmentContent, CandidateAdviserSemanticInput


class CandidateAdviserAgent(Protocol):
    def assess(self, *, semantic_input: CandidateAdviserSemanticInput) -> CandidateAdviserAssessmentContent: ...


class SemanticCandidateAdviser:
    """Provider-neutral structured adviser synthesis; persistence stays in the service."""

    def __init__(self, client: object, model: str) -> None:
        self._client = client
        self._model = model

    def assess(self, *, semantic_input: CandidateAdviserSemanticInput) -> CandidateAdviserAssessmentContent:
        prompt = (Path(__file__).resolve().parents[3] / "prompts" / "candidate_adviser.md").read_text(encoding="utf-8")
        payload = semantic_input.model_dump(mode="json")
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        f"JSON schema:\n{json.dumps(CandidateAdviserAssessmentContent.model_json_schema())}\n\n"
                        f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}"
                    ),
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "candidate_adviser_assessment",
                    "strict": True,
                    "schema": CandidateAdviserAssessmentContent.model_json_schema(),
                }
            },
        )
        try:
            return CandidateAdviserAssessmentContent.model_validate(
                json.loads(response.output_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError("Candidate adviser returned invalid structured output.") from exc

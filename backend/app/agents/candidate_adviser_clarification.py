"""Strict, answer-isolated clarification interpretation."""

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.providers.llm import SemanticOutputError
from app.schemas.candidate_adviser import ClarificationInterpretation


class CandidateAdviserClarificationInterpreter(Protocol):
    def interpret(self, *, question_text: str, answer_text: str) -> ClarificationInterpretation: ...


class SemanticCandidateAdviserClarificationInterpreter:
    """One strict provider call over only the question and candidate answer."""

    def __init__(self, client: object, model: str) -> None:
        self._client = client
        self._model = model

    def interpret(self, *, question_text: str, answer_text: str) -> ClarificationInterpretation:
        prompt = (Path(__file__).resolve().parents[3] / "prompts" / "candidate_adviser_clarification.md").read_text(encoding="utf-8")
        payload = {"question": question_text, "candidate_answer": answer_text}
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"JSON schema:\n{json.dumps(ClarificationInterpretation.model_json_schema())}\n\nINPUT:\n{json.dumps(payload, ensure_ascii=False)}"},
            ],
            text={"format": {"type": "json_schema", "name": "candidate_adviser_clarification", "strict": True, "schema": ClarificationInterpretation.model_json_schema()}},
        )
        try:
            return ClarificationInterpretation.model_validate(
                json.loads(response.output_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError("Candidate adviser clarification returned invalid structured output.") from exc

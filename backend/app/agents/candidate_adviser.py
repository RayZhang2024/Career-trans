import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.providers.llm import SemanticOutputError
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessment,
    CandidateAdviserSourceContext,
    CandidateIntakeProfileData,
)


class CandidateAdviserAgent(Protocol):
    def assess(
        self,
        candidate_context: CandidateAdviserSourceContext,
        intake: CandidateIntakeProfileData,
        *,
        allowed_evidence_ids: list[str],
        allowed_intake_refs: list[str],
    ) -> CandidateAdviserAssessment: ...


class SemanticCandidateAdviser:
    """Provider-neutral semantic adviser over bounded confirmed candidate source data."""

    def __init__(self, client: object, model: str) -> None:
        self._client = client
        self._model = model

    def assess(
        self,
        candidate_context: CandidateAdviserSourceContext,
        intake: CandidateIntakeProfileData,
        *,
        allowed_evidence_ids: list[str],
        allowed_intake_refs: list[str],
    ) -> CandidateAdviserAssessment:
        prompt = (
            Path(__file__).resolve().parents[3] / "prompts" / "candidate_adviser.md"
        ).read_text(encoding="utf-8")
        payload = {
            "candidate_context": candidate_context.model_dump(mode="json"),
            "candidate_intake": intake.model_dump(mode="json"),
            "allowed_evidence_ids": allowed_evidence_ids,
            "allowed_intake_refs": allowed_intake_refs,
        }
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        f"JSON schema:\n{json.dumps(CandidateAdviserAssessment.model_json_schema())}"
                        f"\n\nINPUT:\n{json.dumps(payload, ensure_ascii=False)}"
                    ),
                },
            ],
        )
        raw = (
            response.output_text.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        try:
            return CandidateAdviserAssessment.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError(
                "Candidate adviser returned invalid structured data."
            ) from exc

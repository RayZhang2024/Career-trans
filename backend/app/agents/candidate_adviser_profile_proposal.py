"""Source-grounded generation of noncanonical structured Profile suggestions."""

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.providers.llm import SemanticOutputError
from app.providers.openai_structured_output import strict_schema_from_pydantic_model
from app.schemas.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalGeneration,
    CandidateAdviserProfileProposalGenerationInput,
)


class CandidateAdviserProfileProposalGenerator(Protocol):
    def generate(
        self, *, generation_input: CandidateAdviserProfileProposalGenerationInput
    ) -> CandidateAdviserProfileProposalGeneration: ...


class SemanticCandidateAdviserProfileProposalGenerator:
    """Generate bounded proposals; persistence and target validation stay outside."""

    def __init__(self, client: object, model: str) -> None:
        self._client = client
        self._model = model

    def generate(
        self, *, generation_input: CandidateAdviserProfileProposalGenerationInput
    ) -> CandidateAdviserProfileProposalGeneration:
        prompt = (
            Path(__file__).resolve().parents[3]
            / "prompts"
            / "candidate_adviser_profile_proposal.md"
        ).read_text(encoding="utf-8")
        schema = strict_schema_from_pydantic_model(CandidateAdviserProfileProposalGeneration)
        payload = generation_input.model_dump(mode="json")
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        f"JSON schema:\n{json.dumps(schema)}\n\n"
                        f"INPUT:\n{json.dumps(payload, ensure_ascii=False)}"
                    ),
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "candidate_adviser_profile_proposal",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        try:
            raw = response.output_text.strip()
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return CandidateAdviserProfileProposalGeneration.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError(
                "Candidate Adviser Profile proposal generation returned invalid structured output."
            ) from exc

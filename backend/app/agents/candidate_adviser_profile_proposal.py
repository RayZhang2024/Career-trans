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
    CandidateAdviserProfileProposalProviderGeneration,
    _UPDATE_ADAPTER,
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
        schema = strict_schema_from_pydantic_model(CandidateAdviserProfileProposalProviderGeneration)
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
            output_text = response.output_text
            if not isinstance(output_text, str):
                raise TypeError
            raw = output_text.strip()
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            decoded = json.loads(raw)
        except (json.JSONDecodeError, AttributeError, TypeError):
            raise SemanticOutputError(
                "Candidate Adviser Profile proposal generation returned malformed JSON output."
            ) from None
        try:
            wire_result = CandidateAdviserProfileProposalProviderGeneration.model_validate(decoded)
        except ValidationError:
            raise SemanticOutputError(
                "Candidate Adviser Profile proposal generation returned invalid provider output."
            ) from None
        try:
            canonical_proposals = [
                _UPDATE_ADAPTER.validate_python(proposal.model_dump(mode="python"))
                for proposal in wire_result.proposals
            ]
            return CandidateAdviserProfileProposalGeneration(proposals=canonical_proposals)
        except ValidationError:
            raise SemanticOutputError(
                "Candidate Adviser Profile proposal generation returned invalid canonical proposals."
            ) from None

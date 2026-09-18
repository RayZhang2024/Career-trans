"""Small, separate semantic drafting components for Application Preparation."""

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.providers.llm import SemanticOutputError
from app.providers.openai_structured_output import strict_schema_from_pydantic_model
from app.schemas.application_preparation import ApplicationQuestionAnswerSet, CVWritingDraft, CoverLetterContent


class ApplicationDraftingAgent(Protocol):
    def draft_cv(self, context: dict[str, object]) -> CVWritingDraft: ...
    def draft_cover_letter(self, context: dict[str, object]) -> CoverLetterContent: ...
    def draft_answers(self, context: dict[str, object]) -> ApplicationQuestionAnswerSet: ...


class OpenAIApplicationDraftingAgent:
    """Structured drafting only; deterministic services own factual validation."""

    def __init__(self, *, cv_client: object, cover_letter_client: object, answer_client: object, model: str) -> None:
        self._cv_client = cv_client
        self._cover_letter_client = cover_letter_client
        self._answer_client = answer_client
        self._model = model

    def draft_cv(self, context: dict[str, object]) -> CVWritingDraft:
        return self._generate("application_cv_drafting", CVWritingDraft, context)

    def draft_cover_letter(self, context: dict[str, object]) -> CoverLetterContent:
        return self._generate("application_cover_letter", CoverLetterContent, context)

    def draft_answers(self, context: dict[str, object]) -> ApplicationQuestionAnswerSet:
        return self._generate("application_answer_drafting", ApplicationQuestionAnswerSet, context)

    def _generate(self, operation: str, output_type: type[CVWritingDraft] | type[CoverLetterContent] | type[ApplicationQuestionAnswerSet], context: dict[str, object]) -> CVWritingDraft | CoverLetterContent | ApplicationQuestionAnswerSet:
        try:
            prompt = (Path(__file__).resolve().parents[3] / "prompts" / f"{operation}.md").read_text(encoding="utf-8")
            client = {"application_cv_drafting": self._cv_client, "application_cover_letter": self._cover_letter_client, "application_answer_drafting": self._answer_client}[operation]
            response = client.responses.create(
                model=self._model,
                input=[{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                text={"format": {"type": "json_schema", "name": operation, "strict": True, "schema": strict_schema_from_pydantic_model(output_type)}},
            )
            return output_type.model_validate(json.loads(response.output_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError("Application drafting returned invalid structured output.") from exc

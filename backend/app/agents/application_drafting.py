"""Small, separate semantic drafting components for Application Preparation."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol

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
            schema = self._build_request_schema(output_type, context, operation)
            prompt = (Path(__file__).resolve().parents[3] / "prompts" / f"{operation}.md").read_text(encoding="utf-8")
            client = {"application_cv_drafting": self._cv_client, "application_cover_letter": self._cover_letter_client, "application_answer_drafting": self._answer_client}[operation]
            response = client.responses.create(
                model=self._model,
                input=[{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                text={"format": {"type": "json_schema", "name": operation, "strict": True, "schema": schema}},
            )
            return output_type.model_validate(json.loads(response.output_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError("Application drafting returned invalid structured output.") from exc

    @staticmethod
    def _build_request_schema(
        output_type: type[CVWritingDraft] | type[CoverLetterContent] | type[ApplicationQuestionAnswerSet],
        context: dict[str, object],
        operation: str,
    ) -> dict[str, Any]:
        """Narrow the shared strict schema to this request's factual authority."""
        source_pairs = _source_pairs(context)
        if not source_pairs:
            raise SemanticOutputError("Application drafting requires at least one factual source.")

        schema = deepcopy(strict_schema_from_pydantic_model(output_type))
        definitions = schema["$defs"]
        definitions["ApplicationSourceRef"] = {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source_type", "source_ref"],
                    "properties": {
                        "source_type": {"type": "string", "enum": [source_type]},
                        "source_ref": {"type": "string", "enum": [source_ref]},
                    },
                }
                for source_type, source_ref in source_pairs
            ]
        }

        if output_type is CVWritingDraft:
            properties = schema["properties"]
            skills = _string_values(context.get("allowed_skills"))
            if skills:
                properties["key_skills"]["items"] = {"type": "string", "enum": skills}
            else:
                properties["key_skills"]["maxItems"] = 0

            employment_indexes = _indexes(context.get("employment"), "employment_index")
            if employment_indexes:
                definitions["TailoredRoleDraft"]["properties"]["employment_index"] = {
                    "type": "integer", "enum": employment_indexes,
                }
            else:
                properties["role_drafts"]["maxItems"] = 0

            project_indexes = _indexes(context.get("projects"), "project_index")
            if project_indexes:
                definitions["TailoredProjectDraft"]["properties"]["project_index"] = {
                    "type": "integer", "enum": project_indexes,
                }
            else:
                properties["project_drafts"]["maxItems"] = 0

        if output_type is ApplicationQuestionAnswerSet:
            questions = _string_values(context.get("questions"))
            answers = schema["properties"]["answers"]
            answers["minItems"] = len(questions)
            answers["maxItems"] = len(questions)
            definitions["ApplicationQuestionAnswer"]["properties"]["question"] = {
                "type": "string", "enum": questions,
            }

        return schema


def _source_pairs(context: dict[str, object]) -> list[tuple[str, str]]:
    values = context.get("sources")
    pairs: list[tuple[str, str]] = []
    if not isinstance(values, list):
        return pairs
    for item in values:
        if not isinstance(item, dict):
            continue
        source_type = item.get("source_type")
        source_ref = item.get("source_ref")
        if isinstance(source_type, str) and source_type and isinstance(source_ref, str) and source_ref:
            pair = (source_type, source_ref)
            if pair not in pairs:
                pairs.append(pair)
    return pairs


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in result:
            result.append(item)
    return result


def _indexes(value: object, key: str) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get(key), int) and item[key] not in result:
            result.append(item[key])
    return result

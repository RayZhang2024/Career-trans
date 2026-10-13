"""Purpose-built strategy and CV drafting for explicit V2B operations."""
import json
from copy import deepcopy
from pathlib import Path
from typing import Protocol
from pydantic import ValidationError
from app.agents.application_drafting import OpenAIApplicationDraftingAgent
from app.providers.llm import SemanticOutputError
from app.providers.openai_structured_output import strict_schema_from_pydantic_model
from app.schemas.application_cv_tailoring import TailoringStrategy
from app.schemas.application_preparation import CVWritingDraft


class ApplicationCVTailoringAgent(Protocol):
    def recommend_strategy(self, context: dict[str, object]) -> TailoringStrategy: ...
    def draft_tailored_cv(self, context: dict[str, object]) -> CVWritingDraft: ...


class OpenAIApplicationCVTailoringAgent:
    """Exactly one strict-schema model request per explicit strategy or draft action."""
    def __init__(self, *, strategy_client: object, draft_client: object, model: str) -> None:
        self._strategy_client = strategy_client
        self._draft_client = draft_client
        self._model = model

    def recommend_strategy(self, context: dict[str, object]) -> TailoringStrategy:
        schema = self._strategy_schema(context)
        _remove_unsupported_array_bounds(schema)
        return self._generate("application_cv_tailoring_strategy", TailoringStrategy, schema, context, self._strategy_client)

    def draft_tailored_cv(self, context: dict[str, object]) -> CVWritingDraft:
        schema = OpenAIApplicationDraftingAgent._build_request_schema(CVWritingDraft, context, "application_cv_drafting")
        _remove_unsupported_array_bounds(schema)
        return self._generate("application_cv_tailoring_draft", CVWritingDraft, schema, context, self._draft_client)

    @staticmethod
    def _strategy_schema(context: dict[str, object]) -> dict[str, object]:
        sources = context.get("sources")
        pairs = sorted({
            (str(item["source_type"]), str(item["source_ref"]))
            for item in sources if isinstance(item, dict) and item.get("source_type") and item.get("source_ref")
        }) if isinstance(sources, list) else []
        requirements = context.get("requirements")
        indexes = sorted({
            int(item["requirement_index"])
            for item in requirements if isinstance(item, dict) and isinstance(item.get("requirement_index"), int)
        }) if isinstance(requirements, list) else []
        if not pairs or not indexes:
            raise SemanticOutputError("CV tailoring needs current requirements and confirmed evidence.")
        schema = deepcopy(strict_schema_from_pydantic_model(TailoringStrategy))
        schema["$defs"]["ApplicationSourceRef"] = {
            "anyOf": [
                {"type": "object", "additionalProperties": False, "required": ["source_type", "source_ref"],
                 "properties": {"source_type": {"type": "string", "enum": [kind]}, "source_ref": {"type": "string", "enum": [ref]}}}
                for kind, ref in pairs
            ]
        }
        rec = schema["$defs"]["TailoringRecommendation"]["properties"]
        rec["requirement_index"] = {"type": "integer", "enum": indexes}
        rec["recommendation_id"] = {"type": "string", "enum": [f"requirement-{i}" for i in indexes]}
        schema["$defs"]["TailoringGap"]["properties"]["requirement_index"] = {"type": "integer", "enum": indexes}
        return schema

    def _generate(self, operation: str, output_type: type, schema: dict[str, object], context: dict[str, object], client: object):
        try:
            prompt = (Path(__file__).resolve().parents[3] / "prompts" / f"{operation}.md").read_text(encoding="utf-8")
            response = client.responses.create(
                model=self._model,
                input=[{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                text={"format": {"type": "json_schema", "name": operation, "strict": True, "schema": schema}},
            )
            return output_type.model_validate(json.loads(response.output_text.strip()))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise SemanticOutputError("CV tailoring returned invalid structured output.") from exc


def _remove_unsupported_array_bounds(value: object) -> None:
    """Leave array cardinality to Pydantic and deterministic V2B validation.

    OpenAI strict Structured Outputs does not accept ``minItems`` or ``maxItems``.
    The reused schema builder adds these from Pydantic limits and also uses
    ``maxItems: 0`` to encode an empty selected section. The application validates
    the parsed draft against the Pydantic model and selected-evidence boundary.
    """
    if isinstance(value, dict):
        value.pop("minItems", None)
        value.pop("maxItems", None)
        for child in value.values():
            _remove_unsupported_array_bounds(child)
    elif isinstance(value, list):
        for child in value:
            _remove_unsupported_array_bounds(child)

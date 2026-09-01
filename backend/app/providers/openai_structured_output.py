"""Strict JSON Schema construction shared by OpenAI-compatible structured outputs."""

import json
from typing import Any

from pydantic import BaseModel


class StrictStructuredOutputSchemaError(RuntimeError):
    """The installed SDK could not derive a strict provider schema."""


def strict_schema_from_pydantic_model(model: type[BaseModel]) -> dict[str, Any]:
    """Build the SDK's strict schema and remove unsupported default keywords.

    The OpenAI SDK parser makes every object property required while retaining
    nullable types for application-optional fields. Pydantic defaults are not
    accepted by the strict provider subset, so remove them recursively without
    changing the canonical application model.
    """
    try:
        from openai.lib._parsing import type_to_response_format_param
    except ImportError as exc:  # pragma: no cover - guarded by the installed SDK
        raise StrictStructuredOutputSchemaError(
            "The installed OpenAI SDK cannot build a native Structured Outputs schema."
        ) from exc

    response_format = type_to_response_format_param(model)
    json_schema = response_format.get("json_schema")
    schema = json_schema.get("schema") if isinstance(json_schema, dict) else None
    if not isinstance(schema, dict):
        raise StrictStructuredOutputSchemaError(
            "The OpenAI SDK could not build a native Structured Outputs schema."
        )

    normalized = json.loads(json.dumps(schema))
    _remove_defaults(normalized)
    return normalized


def _remove_defaults(value: object) -> None:
    if isinstance(value, dict):
        value.pop("default", None)
        for child in value.values():
            _remove_defaults(child)
    elif isinstance(value, list):
        for child in value:
            _remove_defaults(child)

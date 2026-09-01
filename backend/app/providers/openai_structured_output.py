"""Strict JSON Schema construction shared by OpenAI-compatible structured outputs."""

import json
from typing import Any

from pydantic import BaseModel


class StrictStructuredOutputSchemaError(RuntimeError):
    """The installed SDK could not derive a strict provider schema."""


def strict_schema_from_pydantic_model(model: type[BaseModel]) -> dict[str, Any]:
    """Build the SDK's strict schema and remove unsupported provider keywords.

    The OpenAI SDK parser makes every object property required while retaining
    nullable types for application-optional fields. Pydantic defaults are not
    accepted by the strict provider subset. Its current string-constraint subset
    also excludes ``minLength`` and ``maxLength``. Remove those provider-only
    restrictions recursively without changing the canonical application model,
    which remains authoritative when it validates the returned data.
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
    _normalize_strict_provider_schema(normalized)
    return normalized


def _normalize_strict_provider_schema(value: object) -> None:
    if isinstance(value, dict):
        for keyword in ("default", "minLength", "maxLength"):
            value.pop(keyword, None)
        for child in value.values():
            _normalize_strict_provider_schema(child)
    elif isinstance(value, list):
        for child in value:
            _normalize_strict_provider_schema(child)

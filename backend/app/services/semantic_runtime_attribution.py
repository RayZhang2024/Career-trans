"""Canonical, privacy-safe historical runtime attribution projection."""

import json

from pydantic import ValidationError

from app.schemas.ai_settings import SemanticOperation
from app.schemas.semantic_runtime_attribution import (
    SemanticRuntimeAttribution,
    SemanticRuntimeAttributionStatus,
    SemanticRuntimeOperationAttribution,
)
from app.services.llm_runtime import ResolvedRuntimeSnapshot


_UNSET = object()


class RuntimeAttributionIntegrityError(ValueError):
    """Persisted runtime attribution is malformed; public text is intentionally bounded."""

    def __init__(self) -> None:
        super().__init__("Persisted runtime attribution is invalid.")


def available_attribution(
    snapshot: ResolvedRuntimeSnapshot,
    operations: set[SemanticOperation] | frozenset[SemanticOperation] | tuple[SemanticOperation, ...],
) -> SemanticRuntimeAttribution:
    projected = {
        SemanticOperation(operation): SemanticRuntimeOperationAttribution(
            model=snapshot.operation(operation).model,
            reasoning_effort=snapshot.operation(operation).reasoning_effort,
        )
        for operation in operations
    }
    return SemanticRuntimeAttribution(
        status=SemanticRuntimeAttributionStatus.AVAILABLE,
        provider=snapshot.provider,
        operations=projected,
    )


def not_used_attribution() -> SemanticRuntimeAttribution:
    return SemanticRuntimeAttribution(
        status=SemanticRuntimeAttributionStatus.NOT_USED,
        provider=None,
        operations={},
    )


def legacy_unavailable_attribution() -> SemanticRuntimeAttribution:
    return SemanticRuntimeAttribution(
        status=SemanticRuntimeAttributionStatus.LEGACY_UNAVAILABLE,
        provider=None,
        operations={},
    )


def canonical_attribution_json(value: SemanticRuntimeAttribution) -> str:
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def read_attribution(
    raw: str | None,
    *,
    null_value: SemanticRuntimeAttribution | None | object = _UNSET,
) -> SemanticRuntimeAttribution | None:
    if raw is None:
        return legacy_unavailable_attribution() if null_value is _UNSET else null_value
    try:
        return SemanticRuntimeAttribution.model_validate_json(raw)
    except (ValidationError, ValueError, TypeError) as exc:
        raise RuntimeAttributionIntegrityError() from exc

"""Privacy-safe aggregation for exported LangSmith run JSON.

This module deliberately has no LangSmith client dependency.  It accepts an
exported run list (or an object containing ``runs``/``child_runs``) and emits
only stage names, safe model configuration, counters, and numeric timing/token
metrics.  Prompts, inputs, outputs, evidence, and provider bodies are never
copied into the result.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


_STAGES = (
    "job_relevance",
    "job_archetype",
    "job_extraction",
    "requirement_matching",
    "career_alignment",
)
_STAGE_ALIASES = {
    "job_relevance_agent": "job_relevance",
    "job_archetype_agent": "job_archetype",
    "job_extraction_agent": "job_extraction",
    "requirement_matching_agent": "requirement_matching",
    "career_alignment_agent": "career_alignment",
}


class StageUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    model: str | None = None
    call_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    retry_count: int = 0


class FunnelCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_jobs: int | None = None
    gated_out: int | None = None
    relevance_screened: int | None = None
    archetyped: int | None = None
    finalists: int | None = None
    deep_analysed: int | None = None


class LLMUsageAuditSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: dict[str, StageUsage] = Field(default_factory=dict)
    funnel: FunnelCounts = Field(default_factory=FunnelCounts)


def summarize_trace_export(export: Any) -> LLMUsageAuditSummary:
    """Summarize a LangSmith JSON export without retaining private content."""
    runs = list(_iter_runs(export))
    stages: dict[str, StageUsage] = {}
    for run in runs:
        stage = _stage_for_run(run)
        if stage is None:
            continue
        usage = stages.setdefault(stage, StageUsage(stage=stage))
        usage.call_count += 1
        input_tokens, cached_tokens, output_tokens, reasoning_tokens, total_tokens = _usage_metrics(run)
        usage.input_tokens += input_tokens
        usage.cached_input_tokens += cached_tokens
        usage.output_tokens += output_tokens
        usage.reasoning_tokens += reasoning_tokens
        usage.total_tokens += total_tokens
        usage.latency_ms += _latency_ms(run)
        if _failed(run):
            usage.failure_count += 1
        else:
            usage.success_count += 1
        attempt = _application_attempt(run)
        if attempt is not None and attempt > 1:
            usage.retry_count += attempt - 1
        model = _model_name(run)
        if usage.model is None and model:
            usage.model = model

    return LLMUsageAuditSummary(
        stages=stages,
        funnel=FunnelCounts(**_funnel_values(export, runs)),
    )


def _iter_runs(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _iter_runs(item)
        return
    if not isinstance(value, dict):
        return
    if _looks_like_run(value):
        yield value
    for key in ("runs", "child_runs", "children", "trace"):
        child = value.get(key)
        if child is not None:
            yield from _iter_runs(child)


def _looks_like_run(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("name", "run_type", "start_time", "id")) or (
        {"inputs", "outputs"}.issubset(value)
        and any(key in value for key in ("metadata", "langsmith", "error"))
    )


def _stage_name(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    normalized = name.casefold()
    if normalized in _STAGES:
        return normalized
    return _STAGE_ALIASES.get(normalized)


def _stage_for_run(run: dict[str, Any]) -> str | None:
    """Infer a provider stage only from exact safe names or explicit mappings."""
    stage = _stage_name(_run_name(run))
    if stage is not None:
        return stage

    # The LangSmith single-run export can omit the displayed provider name.
    # ``application_attempt`` plus the known graph node and provider metadata
    # makes this unambiguous for requirement matching, while excluding the
    # orchestration parent (which has no application attempt).
    metadata = run.get("metadata")
    if (
        isinstance(metadata, dict)
        and metadata.get("langgraph_node") == "match_requirements"
        and isinstance(metadata.get("application_attempt"), (int, float))
        and isinstance(metadata.get("ls_model_name"), str)
        and isinstance(_nested_value(run, "outputs", "usage_metadata"), dict)
    ):
        return "requirement_matching"
    return None


def _run_name(run: dict[str, Any]) -> str | None:
    """Read only explicit safe run-name paths from LangSmith exports."""
    candidates = (
        run.get("name"),
        run.get("run_name"),
        run.get("metadata", {}).get("ls_run_name") if isinstance(run.get("metadata"), dict) else None,
        run.get("metadata", {}).get("operation") if isinstance(run.get("metadata"), dict) else None,
        run.get("metadata", {}).get("stage") if isinstance(run.get("metadata"), dict) else None,
        run.get("langsmith", {}).get("name") if isinstance(run.get("langsmith"), dict) else None,
        run.get("langsmith", {}).get("run_name") if isinstance(run.get("langsmith"), dict) else None,
    )
    return next((value for value in candidates if isinstance(value, str) and value.strip()), None)


def _usage_metrics(run: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Read known usage paths without scanning arbitrary prompt/input/output data."""
    usage = _first_dict(
        run.get("outputs", {}).get("usage_metadata") if isinstance(run.get("outputs"), dict) else None,
        run.get("usage_metadata"),
        run.get("metadata", {}).get("usage_metadata") if isinstance(run.get("metadata"), dict) else None,
        run.get("extra", {}).get("usage_metadata") if isinstance(run.get("extra"), dict) else None,
        run.get("extra", {}).get("metadata", {}).get("usage_metadata")
        if isinstance(run.get("extra"), dict) and isinstance(run.get("extra", {}).get("metadata"), dict)
        else None,
    )
    if usage is None:
        return (0, 0, 0, 0, 0)
    input_tokens = _int_value(usage, "input_tokens", "prompt_tokens")
    cached_tokens = _int_value(usage, "cached_input_tokens", "cache_read_input_tokens")
    if cached_tokens == 0:
        cached_tokens = _int_value(_dict_value(usage, "input_token_details"), "cache_read")
    output_tokens = _int_value(usage, "output_tokens", "completion_tokens")
    reasoning_tokens = _int_value(usage, "reasoning_tokens")
    if reasoning_tokens == 0:
        reasoning_tokens = _int_value(_dict_value(usage, "output_token_details"), "reasoning")
    total_tokens = _int_value(usage, "total_tokens")
    return input_tokens, cached_tokens, output_tokens, reasoning_tokens, total_tokens


def _first_dict(*values: Any) -> dict[str, Any] | None:
    return next((value for value in values if isinstance(value, dict)), None)


def _dict_value(value: Any, key: str) -> dict[str, Any]:
    child = value.get(key) if isinstance(value, dict) else None
    return child if isinstance(child, dict) else {}


def _int_value(value: Any, *keys: str) -> int:
    if not isinstance(value, dict):
        return 0
    for key in keys:
        child = value.get(key)
        if isinstance(child, (int, float)):
            return max(0, int(child))
    return 0


def _model_name(run: dict[str, Any]) -> str | None:
    for value in (
        run.get("model"),
        _nested_value(run, "outputs", "model"),
        _nested_value(run, "metadata", "ls_model_name"),
        _nested_value(run, "metadata", "model_name"),
        _nested_value(run, "extra", "invocation_params", "model"),
        _nested_value(run, "extra", "metadata", "model"),
        _nested_value(run, "extra", "metadata", "model_name"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _application_attempt(run: dict[str, Any]) -> int | None:
    for path in (
        ("metadata", "application_attempt"),
        ("extra", "metadata", "application_attempt"),
    ):
        value = _nested_value(run, *path)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _nested_value(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _failed(run: dict[str, Any]) -> bool:
    status = str(run.get("status", "")).casefold()
    if not status:
        status = str(_nested_value(run, "langsmith", "status") or "").casefold()
    return bool(run.get("error")) or status in {"error", "failed", "failure"}


def _latency_ms(run: dict[str, Any]) -> float:
    explicit = next(
        (
            value
            for value in (
                run.get("latency_ms"),
                run.get("duration_ms"),
                _nested_value(run, "langsmith", "latency_ms"),
            )
            if isinstance(value, (int, float))
        ),
        None,
    )
    if explicit is not None:
        return max(0.0, explicit)
    start = _timestamp(run.get("start_time"))
    end = _timestamp(run.get("end_time"))
    if start is None or end is None:
        start = _timestamp(_nested_value(run, "outputs", "created_at"))
        end = _timestamp(_nested_value(run, "outputs", "completed_at"))
    if start is None or end is None:
        start = _timestamp(_nested_value(run, "outputs", "usage_metadata", "created_at"))
        end = _timestamp(_nested_value(run, "outputs", "usage_metadata", "completed_at"))
    return max(0.0, (end - start) * 1000) if start is not None and end is not None else 0.0


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _funnel_values(export: Any, runs: list[dict[str, Any]]) -> dict[str, int | None]:
    source: dict[str, Any] = export if isinstance(export, dict) else {}
    if isinstance(source.get("outputs"), dict) and any(
        key in source["outputs"]
        for key in ("discovered_count", "semantic_screening", "finalist_count")
    ):
        source = source["outputs"]
    for key in ("funnel", "funnel_counts", "diagnostics"):
        candidate = source.get(key)
        if isinstance(candidate, dict):
            source = candidate
            break
    aliases = {
        "input_jobs": ("input_jobs", "discovered_count"),
        "gated_out": ("gated_out", "gated_out_count"),
        "relevance_screened": ("relevance_screened", "relevance_screened_count"),
        "archetyped": ("archetyped", "archetyped_count"),
        "finalists": ("finalists", "finalist_count"),
        "deep_analysed": ("deep_analysed", "analysed_count"),
    }
    result: dict[str, int | None] = {}
    for output_name, keys in aliases.items():
        result[output_name] = next(
            (int(source[key]) for key in keys if isinstance(source.get(key), (int, float))),
            None,
        )
    semantic_screening = source.get("semantic_screening")
    if result["relevance_screened"] is None and isinstance(semantic_screening, list):
        result["relevance_screened"] = len(semantic_screening)
    if result["archetyped"] is None and isinstance(semantic_screening, list):
        result["archetyped"] = sum(
            1
            for item in semantic_screening
            if isinstance(item, dict) and isinstance(item.get("archetype"), dict)
        )
    return result

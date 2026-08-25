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
        stage = _stage_name(run.get("name"))
        if stage is None:
            continue
        usage = stages.setdefault(stage, StageUsage(stage=stage))
        usage.call_count += 1
        usage.input_tokens += _metric(run, "input_tokens", "prompt_tokens")
        usage.cached_input_tokens += _metric(
            run,
            "cached_input_tokens",
            "cache_read_input_tokens",
            "prompt_cache_hit_tokens",
        )
        usage.output_tokens += _metric(run, "output_tokens", "completion_tokens")
        usage.reasoning_tokens += _metric(run, "reasoning_tokens")
        usage.total_tokens += _metric(run, "total_tokens")
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
    return any(key in value for key in ("name", "run_type", "start_time", "id"))


def _stage_name(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    normalized = name.casefold()
    for stage in _STAGES:
        if normalized == stage or stage in normalized:
            return stage
    return None


def _metric(run: dict[str, Any], *names: str) -> int:
    aliases = {name.casefold() for name in names}
    found = _find_numeric(run, aliases)
    return max(0, int(found or 0))


def _find_numeric(value: Any, aliases: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in aliases and isinstance(child, (int, float)):
                return float(child)
            found = _find_numeric(child, aliases)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_numeric(child, aliases)
            if found is not None:
                return found
    return None


def _model_name(run: dict[str, Any]) -> str | None:
    for value in (
        run.get("model"),
        _nested_value(run, "extra", "invocation_params", "model"),
        _nested_value(run, "extra", "metadata", "model"),
        _nested_value(run, "extra", "metadata", "model_name"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _application_attempt(run: dict[str, Any]) -> int | None:
    value = _nested_value(run, "extra", "metadata", "application_attempt")
    return int(value) if isinstance(value, (int, float)) else None


def _nested_value(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _failed(run: dict[str, Any]) -> bool:
    status = str(run.get("status", "")).casefold()
    return bool(run.get("error")) or status in {"error", "failed", "failure"}


def _latency_ms(run: dict[str, Any]) -> float:
    explicit = _find_numeric(run, {"latency_ms", "duration_ms"})
    if explicit is not None:
        return max(0.0, explicit)
    start = _timestamp(run.get("start_time"))
    end = _timestamp(run.get("end_time"))
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
    return result

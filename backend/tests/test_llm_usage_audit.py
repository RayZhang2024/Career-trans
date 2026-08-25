from app.services.llm_usage_audit import (
    combine_normalized_trace_exports,
    normalize_trace_exports,
    summarize_trace_export,
)


def _run(name: str, *, usage: dict[str, int], **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "status": "success",
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-01T00:00:01Z",
        "extra": {"invocation_params": {"model": "gpt-5.6-terra"}, **extra},
        "usage_metadata": usage,
    }


def test_aggregates_stage_tokens_cache_reasoning_latency_and_retry_metadata() -> None:
    export = {
        "runs": [
            _run(
                "requirement_matching",
                usage={
                    "input_tokens": 100,
                    "cache_read_input_tokens": 40,
                    "output_tokens": 20,
                    "reasoning_tokens": 5,
                    "total_tokens": 120,
                },
                metadata={"application_attempt": 1},
            ),
            _run(
                "requirement_matching",
                usage={
                    "input_tokens": 100,
                    "cache_read_input_tokens": 80,
                    "output_tokens": 25,
                    "reasoning_tokens": 7,
                    "total_tokens": 125,
                },
                metadata={"application_attempt": 2, "previous_failure_kind": "invalid_output"},
            ),
        ],
        "funnel": {
            "input_jobs": 10,
            "gated_out": 3,
            "relevance_screened": 7,
            "archetyped": 4,
            "finalists": 2,
            "deep_analysed": 2,
        },
    }
    summary = summarize_trace_export(export)
    stage = summary.stages["requirement_matching"]
    assert stage.call_count == 2
    assert stage.input_tokens == 200
    assert stage.cached_input_tokens == 120
    assert stage.output_tokens == 45
    assert stage.reasoning_tokens == 12
    assert stage.total_tokens == 245
    assert stage.latency_ms == 2000
    assert stage.retry_count == 1
    assert stage.success_count == 2
    assert summary.funnel.deep_analysed == 2


def test_handles_missing_reasoning_and_incomplete_runs_without_private_content() -> None:
    private = "candidate evidence and secret prompt"
    summary = summarize_trace_export(
        [{
            "name": "job_relevance",
            "status": "error",
            "error": private,
            "inputs": private,
            "outputs": private,
            "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
        }]
    )
    stage = summary.stages["job_relevance"]
    assert stage.reasoning_tokens == 0
    assert stage.failure_count == 1
    serialized = summary.model_dump_json()
    assert private not in serialized


def test_benchmark_fixture_preserves_bounded_funnel_counts() -> None:
    fixture = {
        "runs": [{"name": "job_relevance", "usage_metadata": {"input_tokens": 1}}],
        "diagnostics": {
            "discovered_count": 10,
            "gated_out_count": 4,
            "relevance_screened_count": 6,
            "archetyped_count": 3,
            "finalist_count": 2,
            "analysed_count": 2,
        },
    }
    summary = summarize_trace_export(fixture)
    assert summary.funnel.model_dump() == {
        "input_jobs": 10,
        "gated_out": 4,
        "relevance_screened": 6,
        "archetyped": 3,
        "finalists": 2,
        "deep_analysed": 2,
    }


def test_real_issue106_provider_export_shape_is_parsed_without_private_content() -> None:
    fixture = {
        "inputs": {"private_prompt": "candidate evidence must not surface"},
        "outputs": {
            "model": "gpt-5.6-terra",
            "created_at": "2026-08-25T20:00:00Z",
            "completed_at": "2026-08-25T20:00:12.500Z",
            "usage_metadata": {
                "input_tokens": 4576,
                "input_token_details": {"cache_read": 4044},
                "output_tokens": 2674,
                "output_token_details": {"reasoning": 201},
                "total_tokens": 7250,
            },
        },
        "error": None,
            "metadata": {
            "langgraph_node": "match_requirements",
            "ls_model_name": "gpt-5.6-terra",
            "application_attempt": 2,
            "previous_failure_kind": "unknown_evidence_ids",
        },
        "langsmith": {"run_id": "synthetic-run", "run_type": "llm"},
    }

    summary = summarize_trace_export(fixture)

    stage = summary.stages["requirement_matching"]
    assert stage.model == "gpt-5.6-terra"
    assert stage.call_count == 1
    assert stage.input_tokens == 4576
    assert stage.cached_input_tokens == 4044
    assert stage.output_tokens == 2674
    assert stage.reasoning_tokens == 201
    assert stage.total_tokens == 7250
    assert stage.latency_ms == 12500
    assert stage.retry_count == 1
    assert "candidate evidence" not in summary.model_dump_json()


def test_exact_career_alignment_provider_child_is_counted_once_not_its_parent() -> None:
    export = [
        {
            "name": "assess_career_alignment",
            "usage_metadata": {"input_tokens": 999, "output_tokens": 999, "total_tokens": 1998},
        },
        {
            "name": "career_alignment",
            "outputs": {
                "model": "gpt-5.6-terra",
                "usage_metadata": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
            },
            "status": "success",
        },
    ]

    summary = summarize_trace_export(export)

    stage = summary.stages["career_alignment"]
    assert stage.call_count == 1
    assert stage.input_tokens == 120
    assert stage.output_tokens == 30
    assert stage.total_tokens == 150


def test_trace_parser_does_not_search_arbitrary_input_output_for_token_fields() -> None:
    summary = summarize_trace_export(
        {
            "inputs": {"input_tokens": 999999},
            "outputs": {"output_tokens": 888888},
            "metadata": {"ls_run_name": "job_relevance"},
            "langsmith": {},
        }
    )
    stage = summary.stages["job_relevance"]
    assert stage.input_tokens == 0
    assert stage.output_tokens == 0


def test_five_real_style_unnamed_provider_exports_normalize_with_explicit_labels() -> None:
    stages = (
        "job_relevance",
        "job_archetype",
        "job_extraction",
        "requirement_matching",
        "career_alignment",
    )
    exports = {
        stage: {
            "inputs": {"private": "do not retain"},
            "outputs": {
                "model": "gpt-5.6-terra",
                "created_at": "2026-08-25T20:00:00Z",
                "completed_at": "2026-08-25T20:00:02Z",
                "usage_metadata": {
                    "input_tokens": 10,
                    "input_token_details": {"cache_read": 3},
                    "output_tokens": 4,
                    "output_token_details": {"reasoning": 1},
                    "total_tokens": 14,
                },
            },
            "error": None,
            "metadata": {"ls_model_name": "gpt-5.6-terra"},
            "langsmith": {"run_id": "safe-id"},
        }
        for stage in stages
    }

    normalized = normalize_trace_exports(exports)
    assert [run["name"] for run in normalized["runs"]] == list(stages)
    assert set(normalized["runs"][0]) == {"name", "metadata", "outputs", "error"}
    assert "private" not in str(normalized)
    summary = summarize_trace_export(normalized)
    assert set(summary.stages) == set(stages)
    assert sum(stage.call_count for stage in summary.stages.values()) == 5
    assert sum(stage.total_tokens for stage in summary.stages.values()) == 70


def test_normalization_preserves_repeated_stages_and_requirement_retry_attempts() -> None:
    normalized = normalize_trace_exports(
        [
            (
                "job_relevance",
                _run("provider", usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}),
            ),
            (
                "job_relevance",
                _run("provider", usage={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}),
            ),
            (
                "job_archetype",
                _run("provider", usage={"input_tokens": 20, "output_tokens": 4, "total_tokens": 24}),
            ),
            (
                "requirement_matching",
                _run("provider", usage={"input_tokens": 30, "output_tokens": 5, "total_tokens": 35})
                | {"metadata": {"application_attempt": 1}},
            ),
            (
                "requirement_matching",
                _run("provider", usage={"input_tokens": 31, "output_tokens": 6, "total_tokens": 37})
                | {
                    "metadata": {
                        "application_attempt": 2,
                        "previous_failure_kind": "invalid_output",
                    }
                },
            ),
        ]
    )

    summary = summarize_trace_export(normalized)
    assert summary.stages["job_relevance"].call_count == 2
    assert summary.stages["job_archetype"].call_count == 1
    matching = summary.stages["requirement_matching"]
    assert matching.call_count == 2
    assert matching.retry_count == 1
    assert matching.total_tokens == 72


def test_funnel_diagnostics_combine_without_copying_job_or_candidate_payloads() -> None:
    normalized = normalize_trace_exports(
        [
            (
                "job_relevance",
                _run("provider", usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}),
            )
        ]
    )
    combined = combine_normalized_trace_exports(
        normalized,
        {
            "candidate_context": "private candidate evidence",
            "outputs": {
                "discovered_count": 10,
                "gated_out_count": 3,
                "semantic_screening": [
                    {"job": "private job one", "archetype": {"name": "research"}},
                    {"job": "private job two", "archetype": None},
                ],
                "finalist_count": 1,
                "analysed_count": 1,
            },
        },
    )

    assert combined["funnel"] == {
        "input_jobs": 10,
        "gated_out": 3,
        "relevance_screened": 2,
        "archetyped": 1,
        "finalists": 1,
        "deep_analysed": 1,
    }
    assert "private candidate evidence" not in str(combined)
    assert "private job one" not in str(combined)


def test_authoritative_warm_cache_one_job_baseline_totals_are_regression_locked() -> None:
    stages = (
        ("job_relevance", 1829, 1826, 153, 0, 1982, 3),
        ("job_archetype", 1259, 1256, 174, 117, 1433, 3),
        ("job_extraction", 2331, 2328, 3226, 361, 5557, 21),
        ("requirement_matching", 4675, 0, 3025, 708, 7700, 24),
        ("career_alignment", 5464, 0, 1008, 134, 6472, 12),
    )

    def provider_export(
        input_tokens: int,
        cached_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        total_tokens: int,
        duration_seconds: int,
    ) -> dict[str, object]:
        return {
            "outputs": {
                "model": "gpt-5.6-luna",
                "created_at": "2026-08-25T20:00:00Z",
                "completed_at": f"2026-08-25T20:00:{duration_seconds:02d}Z",
                "usage_metadata": {
                    "input_tokens": input_tokens,
                    "input_token_details": {"cache_read": cached_tokens},
                    "output_tokens": output_tokens,
                    "output_token_details": {"reasoning": reasoning_tokens},
                    "total_tokens": total_tokens,
                },
            },
            "metadata": {"ls_model_name": "gpt-5.6-luna"},
            "error": None,
            "langsmith": {"run_id": "sanitized-warm-cache"},
        }

    normalized = normalize_trace_exports(
        [
            (stage, provider_export(input_, cached, output, reasoning, total, latency))
            for stage, input_, cached, output, reasoning, total, latency in stages
        ]
    )
    summary = summarize_trace_export(normalized)

    assert sum(item.call_count for item in summary.stages.values()) == 5
    assert sum(item.input_tokens for item in summary.stages.values()) == 15558
    assert sum(item.cached_input_tokens for item in summary.stages.values()) == 5410
    assert sum(item.output_tokens for item in summary.stages.values()) == 7586
    assert sum(item.reasoning_tokens for item in summary.stages.values()) == 1320
    assert sum(item.total_tokens for item in summary.stages.values()) == 23144
    assert all(item.model == "gpt-5.6-luna" for item in summary.stages.values())
    assert summary.stages["job_relevance"].latency_ms == 3000
    assert summary.stages["job_extraction"].latency_ms == 21000

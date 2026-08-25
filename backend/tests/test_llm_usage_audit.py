from app.services.llm_usage_audit import summarize_trace_export


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

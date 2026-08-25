import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.callbacks import CallbackManager
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import run_helpers

from app.agents.requirement_matching import (
    OpenAIRequirementMatcher,
    RequirementMatchingError,
)
from app.providers.llm import (
    SemanticProviderRequestError,
    SemanticStructuredOutputSchemaError,
)
from app.schemas.candidate import CandidateContext, CandidateMatchingProfile, CareerEvidence
from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)
from app.schemas.matching import (
    MatchType,
    SemanticRequirementMatch,
    SemanticRequirementMatchSet,
)
from app.services.requirement_matching_service import RequirementMatchingService
from app.workflows.career_analysis_graph import CareerAnalysisGraph


REQUIREMENTS = [
    JobRequirement(
        text="Professional Python experience",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.TECHNICAL,
    ),
    JobRequirement(
        text="Customer-facing delivery experience",
        importance=RequirementImportance.DESIRABLE,
        category=RequirementCategory.CUSTOMER,
    ),
]
JOB_PROFILE = JobProfile(title="Technical role", requirements=REQUIREMENTS)
CANDIDATE = CandidateMatchingProfile(
    profile_summary="Experienced technical professional.",
    evidence=[
        CareerEvidence(
            evidence_id="EVIDENCE-1",
            title="Python delivery",
            text="Delivered Python systems for customers.",
            skills=["Python"],
        )
    ],
)


class _FakeResponses:
    def __init__(self, outputs: list[str | Exception]) -> None:
        self._outputs = iter(outputs)
        self.calls: list[dict[str, object]] = []
        self.current_run_trees: list[object | None] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        self.current_run_trees.append(run_helpers.get_current_run_tree())
        output = next(self._outputs)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(output_text=output)


class _FakeClient:
    def __init__(self, outputs: list[str | Exception]) -> None:
        self.responses = _FakeResponses(outputs)


def _valid_result(*, requirements: list[JobRequirement] = REQUIREMENTS) -> str:
    return SemanticRequirementMatchSet(
        matches=[
            SemanticRequirementMatch(
                requirement_index=index,
                match_type=MatchType.DEMONSTRATED,
                score=0.9,
                evidence_ids=["EVIDENCE-1"],
                reasoning="Supported by the supplied evidence.",
            )
            for index, requirement in enumerate(requirements)
        ]
    ).model_dump_json()


def _matcher(outputs: list[str | Exception]) -> tuple[OpenAIRequirementMatcher, _FakeResponses]:
    client = _FakeClient(outputs)
    return OpenAIRequirementMatcher(api_key="", model="test-model", client=client), client.responses


class _MetadataRecorder:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []

    def __call__(self, _run_tree: object | None, metadata: dict[str, object]) -> None:
        self.runs.append(dict(metadata))


class _NoopLangChainTracer(LangChainTracer):
    """Exercise callback propagation without making a LangSmith network request."""

    def _persist_run_single(self, run) -> None:  # type: ignore[no-untyped-def]
        return None


def test_matcher_uses_native_strict_schema_and_accepts_valid_result() -> None:
    matcher, responses = _matcher([_valid_result()])

    result = matcher.match(JOB_PROFILE, CANDIDATE)

    assert result.matches[0].requirement == REQUIREMENTS[0]
    assert len(responses.calls) == 1
    response_format = responses.calls[0]["text"]["format"]  # type: ignore[index]
    assert response_format["type"] == "json_schema"
    assert response_format["name"] == "requirement_match_set"
    assert response_format["strict"] is True
    assert response_format["schema"]["additionalProperties"] is False


def test_matcher_reattaches_exact_canonical_requirement_not_present_in_model_output() -> None:
    requirement = JobRequirement(
        text="Build reliable distributed systems",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.TECHNICAL,
        source_text="What we're looking for: build reliable distributed systems.",
    )
    profile = JobProfile(title="Engineering role", requirements=[requirement])
    payload = SemanticRequirementMatchSet(
        matches=[
            SemanticRequirementMatch(
                requirement_index=0,
                match_type=MatchType.TRANSFERABLE,
                score=0.65,
                evidence_ids=["EVIDENCE-1"],
                reasoning="The supplied systems-delivery evidence is adjacent.",
            )
        ]
    ).model_dump_json()
    matcher, _ = _matcher([payload])

    result = matcher.match(profile, CANDIDATE)

    assert result.matches[0].requirement == requirement
    assert result.matches[0].requirement.source_text == requirement.source_text
    assert result.matches[0].requirement.importance == RequirementImportance.ESSENTIAL
    assert result.matches[0].requirement.category == RequirementCategory.TECHNICAL


def test_sdk_schema_preserves_nested_nullable_fields_without_defaults() -> None:
    schema = OpenAIRequirementMatcher._openai_strict_schema()

    serialized = json.dumps(schema)
    semantic_match = schema["$defs"]["SemanticRequirementMatch"]

    assert '"default"' not in serialized
    assert "JobRequirement" not in schema["$defs"]
    assert semantic_match["additionalProperties"] is False
    assert set(semantic_match["required"]) == set(semantic_match["properties"])
    assert "requirement" not in semantic_match["properties"]
    assert "evidence_refs" not in semantic_match["properties"]
    assert semantic_match["properties"]["score"]["minimum"] == 0.0
    assert semantic_match["properties"]["score"]["maximum"] == 1.0


def test_malformed_output_retries_once_without_leaking_candidate_context() -> None:
    matcher, responses = _matcher(["not json", "still not json"])

    with pytest.raises(RequirementMatchingError) as exc_info:
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert exc_info.value.kind == "invalid_output"
    assert len(responses.calls) == 2
    assert "Delivered Python systems" not in str(exc_info.value)
    assert "not json" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("payload", "expected_kind"),
    [
        (
            SemanticRequirementMatchSet(
                matches=[
                    SemanticRequirementMatch(
                        requirement_index=0,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["EVIDENCE-1"],
                        reasoning="Only one result.",
                    )
                ]
            ).model_dump_json(),
            "wrong_match_count",
        ),
        (
            SemanticRequirementMatchSet(
                matches=[
                    SemanticRequirementMatch(
                        requirement_index=1,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["EVIDENCE-1"],
                        reasoning="Repeated index.",
                    ),
                    SemanticRequirementMatch(
                        requirement_index=1,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["EVIDENCE-1"],
                        reasoning="Repeated index.",
                    ),
                ]
            ).model_dump_json(),
            "invalid_indexes",
        ),
        (
            SemanticRequirementMatchSet(
                matches=[
                    SemanticRequirementMatch(
                        requirement_index=0,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["EVIDENCE-1"],
                        reasoning="First requirement.",
                    ),
                    SemanticRequirementMatch(
                        requirement_index=2,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["EVIDENCE-1"],
                        reasoning="Out-of-range requirement.",
                    ),
                ]
            ).model_dump_json(),
            "invalid_indexes",
        ),
    ],
)
def test_wrong_count_or_indexes_remain_rejected(payload: str, expected_kind: str) -> None:
    matcher, responses = _matcher([payload, payload])

    with pytest.raises(RequirementMatchingError) as exc_info:
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert exc_info.value.kind == expected_kind
    assert len(responses.calls) == 2


def test_unknown_evidence_ids_remain_rejected() -> None:
    payload = SemanticRequirementMatchSet(
        matches=[
            SemanticRequirementMatch(
                requirement_index=index,
                match_type=MatchType.DEMONSTRATED,
                score=0.9,
                evidence_ids=["UNKNOWN-EVIDENCE"],
                reasoning="Unknown evidence reference.",
            )
            for index in range(len(REQUIREMENTS))
        ]
    ).model_dump_json()
    matcher, responses = _matcher([payload, payload])

    with pytest.raises(RequirementMatchingError) as exc_info:
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert exc_info.value.kind == "unknown_evidence_ids"
    assert len(responses.calls) == 2
    assert "UNKNOWN-EVIDENCE" not in str(exc_info.value)


def test_provider_failure_is_not_retried_or_exposed() -> None:
    matcher, responses = _matcher([SemanticProviderRequestError("provider-secret")])

    with pytest.raises(RequirementMatchingError) as exc_info:
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert exc_info.value.kind == "provider_failure"
    assert len(responses.calls) == 1
    assert "provider-secret" not in str(exc_info.value)


def test_structured_schema_rejection_is_not_retried_or_exposed() -> None:
    matcher, responses = _matcher(
        [SemanticStructuredOutputSchemaError("provider-response-private-data")]
    )

    with pytest.raises(RequirementMatchingError) as exc_info:
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert exc_info.value.kind == "structured_output_schema_rejected"
    assert len(responses.calls) == 1
    assert "provider-response-private-data" not in str(exc_info.value)


def test_many_requirement_fixture_completes_with_canonical_results() -> None:
    requirements = [
        JobRequirement(text=f"Requirement {index}", category=RequirementCategory.TECHNICAL)
        for index in range(8)
    ]
    profile = JobProfile(title="Many requirements", requirements=requirements)
    matcher, responses = _matcher([_valid_result(requirements=requirements)])

    result = matcher.match(profile, CANDIDATE)

    assert [match.requirement for match in result.matches] == requirements
    assert len(responses.calls) == 1


def test_valid_first_attempt_is_traced_once_with_safe_metadata(monkeypatch) -> None:
    recorder = _MetadataRecorder()
    matcher, responses = _matcher([_valid_result()])
    monkeypatch.setattr(matcher, "_record_attempt_metadata", recorder)

    with run_helpers.tracing_context(enabled=False):
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert len(responses.calls) == 1
    assert recorder.runs == [
        {
            "attempt": 1,
            "max_attempts": 2,
            "requirement_count": 2,
            "candidate_evidence_count": 1,
            "attempt_scope": "application_structural",
            "validation_status": "passed",
            "failure_kind": None,
            "retrying": False,
            "result_match_count": 2,
        }
    ]


def test_retryable_failure_exposes_failure_kind_and_retrying_without_sensitive_data(monkeypatch) -> None:
    recorder = _MetadataRecorder()
    matcher, responses = _matcher(["not json", _valid_result()])
    monkeypatch.setattr(matcher, "_record_attempt_metadata", recorder)

    with run_helpers.tracing_context(enabled=False):
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert len(responses.calls) == 2
    assert recorder.runs[0]["validation_status"] == "failed"
    assert recorder.runs[0]["failure_kind"] == "invalid_output"
    assert recorder.runs[0]["retrying"] is True
    assert recorder.runs[1]["validation_status"] == "passed"
    assert recorder.runs[1]["retrying"] is False
    serialized = str(recorder.runs)
    assert "EVIDENCE-1" not in serialized
    assert "Delivered Python systems" not in serialized
    assert "not json" not in serialized


def test_provider_failure_is_not_a_structural_retry_and_is_observable(monkeypatch) -> None:
    recorder = _MetadataRecorder()
    matcher, responses = _matcher([SemanticProviderRequestError("secret-provider-detail")])
    monkeypatch.setattr(matcher, "_record_attempt_metadata", recorder)

    with run_helpers.tracing_context(enabled=False), pytest.raises(RequirementMatchingError):
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert len(responses.calls) == 1
    assert recorder.runs[0]["failure_kind"] == "provider_failure"
    assert recorder.runs[0]["retrying"] is False
    assert "secret-provider-detail" not in str(recorder.runs)


def test_brown_style_24_requirement_fixture_passes_validation_and_uses_one_attempt(monkeypatch) -> None:
    recorder = _MetadataRecorder()
    requirements = [
        JobRequirement(
            text=f"Synthetic Brown requirement {index}",
            category=RequirementCategory.TECHNICAL,
            importance=(
                RequirementImportance.ESSENTIAL
                if index < 12
                else RequirementImportance.DESIRABLE
            ),
        )
        for index in range(24)
    ]
    profile = JobProfile(title="Synthetic AI Engineer", requirements=requirements)
    payload = _valid_result(requirements=requirements)
    matcher, responses = _matcher([payload])
    monkeypatch.setattr(matcher, "_record_attempt_metadata", recorder)

    with run_helpers.tracing_context(enabled=False):
        result = matcher.match(profile, CANDIDATE)

    # This is the synthetic replay of the first observed 24-requirement shape;
    # it satisfies _validate_result() and therefore must not trigger a retry.
    matcher._validate_result(  # noqa: SLF001 - explicit regression of the invariant
        SemanticRequirementMatchSet.model_validate(json.loads(payload)),
        profile,
        CANDIDATE,
    )
    assert len(result.matches) == 24
    assert len(responses.calls) == 1
    assert recorder.runs[0]["requirement_count"] == 24
    assert recorder.runs[0]["validation_status"] == "passed"


def test_attempt_span_is_created_under_langgraph_callback_parent(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    tracer = _NoopLangChainTracer(
        project_name="synthetic",
        client=SimpleNamespace(otel_exporter=None),
    )
    parent_id = uuid4()
    tracer.on_chain_start({}, {}, run_id=parent_id, name="match_requirements")
    config = {"callbacks": CallbackManager([tracer], parent_run_id=parent_id)}
    matcher, responses = _matcher([_valid_result()])
    attempt_runs: list[object | None] = []
    original_recorder = matcher._record_attempt_metadata

    def capture_attempt_run(run_tree: object | None, metadata: dict[str, object]) -> None:
        attempt_runs.append(run_tree)
        original_recorder(run_tree, metadata)

    monkeypatch.setattr(matcher, "_record_attempt_metadata", capture_attempt_run)

    graph = CareerAnalysisGraph.__new__(CareerAnalysisGraph)
    graph._requirement_matching_service = RequirementMatchingService(matcher=matcher)  # type: ignore[attr-defined]
    graph._match_requirements(  # type: ignore[attr-defined]
        {
            "job_profile": JOB_PROFILE,
            "candidate_context": CandidateContext(
                profile_text=CANDIDATE.profile_summary,
                evidence=CANDIDATE.evidence,
            ),
        },
        config,
    )

    attempt_run = attempt_runs[0]
    provider_parent = responses.current_run_trees[0]
    assert attempt_run is not None
    assert getattr(attempt_run, "name") == "requirement_matching_attempt"
    assert getattr(attempt_run, "parent_run_id") == parent_id
    assert provider_parent is attempt_run

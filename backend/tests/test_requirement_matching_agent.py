import json
from types import SimpleNamespace

import pytest
from langsmith import run_helpers

from app.agents.requirement_matching import (
    OpenAIRequirementMatcher,
    RequirementMatchingError,
)
from app.providers.llm import (
    SemanticProviderConfigurationError,
    SemanticProviderRequestError,
    SemanticStructuredOutputSchemaError,
)
from app.schemas.candidate import CandidateMatchingProfile, CareerEvidence
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
        self.trace_metadata: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        context = run_helpers.get_tracing_context()
        self.trace_metadata.append(dict(context.get("metadata") or {}))
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
            )
            for index, requirement in enumerate(requirements)
        ]
    ).model_dump_json()


def _matcher(outputs: list[str | Exception]) -> tuple[OpenAIRequirementMatcher, _FakeResponses]:
    client = _FakeClient(outputs)
    return OpenAIRequirementMatcher(api_key="", model="test-model", client=client), client.responses


def _request_payload(call: dict[str, object]) -> dict[str, object]:
    content = call["input"][1]["content"]  # type: ignore[index]
    payload_text = content.split("INPUT:\n", 1)[1].split("\n\nCORRECTIVE RETRY:", 1)[0]
    return json.loads(payload_text)


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
    assert response_format["schema"]["properties"]["matches"]["minItems"] == 2
    assert response_format["schema"]["properties"]["matches"]["maxItems"] == 2
    semantic_match = response_format["schema"]["$defs"]["SemanticRequirementMatch"]
    assert semantic_match["properties"]["requirement_index"]["enum"] == [0, 1]
    assert semantic_match["properties"]["evidence_ids"]["items"]["enum"] == ["EVIDENCE-1"]


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
            )
        ]
    ).model_dump_json()
    matcher, _ = _matcher([payload])

    result = matcher.match(profile, CANDIDATE)

    assert result.matches[0].requirement == requirement
    assert result.matches[0].requirement.source_text == requirement.source_text
    assert result.matches[0].requirement.importance == RequirementImportance.ESSENTIAL
    assert result.matches[0].requirement.category == RequirementCategory.TECHNICAL
    assert result.matches[0].reasoning == (
        "Supplied candidate evidence supports an adjacent transferable capability."
    )


def test_matcher_sends_only_compact_canonical_requirement_fields() -> None:
    requirement = JobRequirement(
        text="Build reliable distributed systems",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.TECHNICAL,
        source_text="Private advert section and duplicated source wording.",
    )
    profile = JobProfile(
        title="Private role title",
        responsibilities=["Private responsibility detail."],
        requirements=[requirement],
        technical_skills=["Python"],
        domain_knowledge=["Private domain context"],
        security_requirements=["Private clearance detail"],
    )
    matcher, responses = _matcher([_valid_result(requirements=[requirement])])

    matcher.match(profile, CANDIDATE)

    payload = _request_payload(responses.calls[0])
    assert payload["job_profile"] == {
        "requirements": [
            {
                "text": "Build reliable distributed systems",
                "importance": "essential",
                "category": "technical",
            }
        ]
    }
    assert payload["matching_contract"] == {
        "expected_match_count": 1,
        "allowed_requirement_indexes": [0],
        "allowed_evidence_ids": ["EVIDENCE-1"],
    }
    assert "Private advert section" not in json.dumps(payload)
    assert "Private responsibility detail" not in json.dumps(payload)


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
    assert "reasoning" not in semantic_match["properties"]
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
                    ),
                    SemanticRequirementMatch(
                        requirement_index=1,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["EVIDENCE-1"],
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
                    ),
                    SemanticRequirementMatch(
                        requirement_index=2,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["EVIDENCE-1"],
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


def test_helsing_style_seventeen_requirement_wrong_count_is_bounded_and_fails_closed() -> None:
    """Offline shape matching the failed live run's safe trace metadata only."""
    requirements = [
        JobRequirement(
            text=f"Synthetic deployment requirement {index}",
            category=RequirementCategory.TECHNICAL,
        )
        for index in range(17)
    ]
    profile = JobProfile(title="Synthetic deployment role", requirements=requirements)
    incomplete = _valid_result(requirements=requirements[:-1])
    matcher, responses = _matcher([incomplete, incomplete])

    with pytest.raises(RequirementMatchingError) as exc_info:
        matcher.match(profile, CANDIDATE)

    assert exc_info.value.kind == "wrong_match_count"
    assert len(responses.calls) == 2
    assert responses.trace_metadata[0]["requirement_count"] == 17
    assert responses.trace_metadata[1]["previous_failure_kind"] == "wrong_match_count"


def test_unknown_evidence_ids_remain_rejected() -> None:
    payload = SemanticRequirementMatchSet(
        matches=[
            SemanticRequirementMatch(
                requirement_index=index,
                match_type=MatchType.DEMONSTRATED,
                score=0.9,
                evidence_ids=["UNKNOWN-EVIDENCE"],
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


def test_wrong_count_retry_receives_safe_correction_and_accepts_valid_second_result() -> None:
    incomplete = _valid_result(requirements=REQUIREMENTS[:1])
    matcher, responses = _matcher([incomplete, _valid_result()])

    result = matcher.match(JOB_PROFILE, CANDIDATE)

    assert len(result.matches) == len(REQUIREMENTS)
    assert len(responses.calls) == 2
    retry_content = responses.calls[1]["input"][1]["content"]  # type: ignore[index]
    assert "CORRECTIVE RETRY:" in retry_content
    assert "Return exactly one match for every allowed requirement index." in retry_content
    assert "not json" not in retry_content


def test_duplicate_indexes_retry_with_safe_correction_and_accept_valid_second_result() -> None:
    duplicate_indexes = SemanticRequirementMatchSet(
        matches=[
            SemanticRequirementMatch(
                requirement_index=0,
                match_type=MatchType.DEMONSTRATED,
                score=0.9,
                evidence_ids=["EVIDENCE-1"],
            ),
            SemanticRequirementMatch(
                requirement_index=0,
                match_type=MatchType.DEMONSTRATED,
                score=0.9,
                evidence_ids=["EVIDENCE-1"],
            ),
        ]
    ).model_dump_json()
    matcher, responses = _matcher([duplicate_indexes, _valid_result()])

    result = matcher.match(JOB_PROFILE, CANDIDATE)

    assert [match.requirement for match in result.matches] == REQUIREMENTS
    assert len(responses.calls) == 2
    assert responses.trace_metadata[1]["previous_failure_kind"] == "invalid_indexes"
    retry_content = responses.calls[1]["input"][1]["content"]  # type: ignore[index]
    corrective_guidance = retry_content.split("CORRECTIVE RETRY:\n", 1)[1]
    assert "Use every allowed requirement index exactly once" in corrective_guidance
    assert duplicate_indexes not in corrective_guidance
    assert "Delivered Python systems" not in corrective_guidance


def test_unknown_evidence_retry_receives_safe_correction_and_accepts_valid_second_result() -> None:
    invalid = SemanticRequirementMatchSet(
        matches=[
            SemanticRequirementMatch(
                requirement_index=index,
                match_type=MatchType.DEMONSTRATED,
                score=0.9,
                evidence_ids=["UNKNOWN-EVIDENCE"],
            )
            for index in range(len(REQUIREMENTS))
        ]
    ).model_dump_json()
    matcher, responses = _matcher([invalid, _valid_result()])

    result = matcher.match(JOB_PROFILE, CANDIDATE)

    assert len(result.matches) == len(REQUIREMENTS)
    assert len(responses.calls) == 2
    retry_content = responses.calls[1]["input"][1]["content"]  # type: ignore[index]
    assert "Use only exact IDs from allowed_evidence_ids" in retry_content
    assert "UNKNOWN-EVIDENCE" not in retry_content


def test_zero_evidence_context_allows_empty_evidence_ids_only() -> None:
    candidate = CANDIDATE.model_copy(update={"evidence": []})
    payload = SemanticRequirementMatchSet(
        matches=[
            SemanticRequirementMatch(
                requirement_index=index,
                match_type=MatchType.MISSING,
                score=0.0,
                evidence_ids=[],
            )
            for index in range(len(REQUIREMENTS))
        ]
    ).model_dump_json()
    matcher, responses = _matcher([payload])

    result = matcher.match(JOB_PROFILE, candidate)

    assert len(result.matches) == len(REQUIREMENTS)
    semantic_match = responses.calls[0]["text"]["format"]["schema"]["$defs"]["SemanticRequirementMatch"]  # type: ignore[index]
    evidence_schema = semantic_match["properties"]["evidence_ids"]
    assert evidence_schema["maxItems"] == 0
    assert "enum" not in evidence_schema["items"]


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


@pytest.mark.parametrize(
    "provider_error",
    [
        SemanticProviderRequestError("provider-secret"),
        SemanticProviderConfigurationError("configuration-secret"),
    ],
)
def test_provider_or_configuration_failures_are_not_structural_retries(
    provider_error: Exception,
) -> None:
    matcher, responses = _matcher([provider_error])

    with pytest.raises(RequirementMatchingError) as exc_info:
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert exc_info.value.kind == "provider_failure"
    assert len(responses.calls) == 1
    assert "secret" not in str(exc_info.value)


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


def test_valid_first_attempt_provider_run_has_safe_application_metadata() -> None:
    matcher, responses = _matcher([_valid_result()])

    with run_helpers.tracing_context(enabled=False):
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert len(responses.calls) == 1
    assert responses.trace_metadata == [
        {
            "application_attempt": 1,
            "max_application_attempts": 2,
            "requirement_count": 2,
            "candidate_evidence_count": 1,
            "attempt_scope": "application_structural",
            "previous_failure_kind": None,
        }
    ]


def test_retryable_failure_annotates_next_provider_run_without_sensitive_data() -> None:
    matcher, responses = _matcher(["not json", _valid_result()])

    with run_helpers.tracing_context(enabled=False):
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert len(responses.calls) == 2
    assert responses.trace_metadata[0]["application_attempt"] == 1
    assert responses.trace_metadata[0]["previous_failure_kind"] is None
    assert responses.trace_metadata[1]["application_attempt"] == 2
    assert responses.trace_metadata[1]["previous_failure_kind"] == "invalid_output"
    serialized = str(responses.trace_metadata)
    assert "EVIDENCE-1" not in serialized
    assert "Delivered Python systems" not in serialized
    assert "not json" not in serialized


def test_provider_failure_is_not_a_structural_retry() -> None:
    matcher, responses = _matcher([SemanticProviderRequestError("secret-provider-detail")])

    with run_helpers.tracing_context(enabled=False), pytest.raises(RequirementMatchingError):
        matcher.match(JOB_PROFILE, CANDIDATE)

    assert len(responses.calls) == 1
    assert responses.trace_metadata[0]["previous_failure_kind"] is None
    assert "secret-provider-detail" not in str(responses.trace_metadata)


def test_brown_style_24_requirement_fixture_passes_validation_and_uses_one_attempt() -> None:
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
    assert responses.trace_metadata[0]["requirement_count"] == 24
    assert responses.trace_metadata[0]["application_attempt"] == 1
    assert responses.trace_metadata[0]["previous_failure_kind"] is None

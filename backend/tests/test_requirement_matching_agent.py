import json
from types import SimpleNamespace

import pytest

from app.agents.requirement_matching import (
    OpenAIRequirementMatcher,
    RequirementMatchingError,
)
from app.providers.llm import (
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

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
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

import json
from types import SimpleNamespace

import pytest

from app.agents.job_extraction import JobExtractionError, OpenAIJobExtractor
from app.providers.llm import (
    SemanticProviderRequestError,
    SemanticStructuredOutputModelError,
    SemanticStructuredOutputSchemaError,
)
from app.providers.openai_structured_output import strict_schema_from_pydantic_model
from app.schemas.job import JobProfile, JobRequirement, RequirementCategory, RequirementImportance


class _FakeResponses:
    def __init__(self, payload: dict[str, object] | str) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        output_text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(output_text=output_text)


class _FakeClient:
    def __init__(self, payload: dict[str, object] | str) -> None:
        self.responses = _FakeResponses(payload)


class _RaisingResponses:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def create(self, **_kwargs: object) -> SimpleNamespace:
        raise self._error


class _RaisingClient:
    def __init__(self, error: Exception) -> None:
        self.responses = _RaisingResponses(error)


def _extract(
    *,
    text: str,
    importance: RequirementImportance,
    source_text: str,
) -> tuple[JobRequirement, _FakeResponses]:
    client = _FakeClient(
        {
            "requirements": [
                {
                    "text": text,
                    "importance": importance.value,
                    "category": "technical",
                    "source_text": source_text,
                }
            ]
        }
    )
    profile = OpenAIJobExtractor(api_key="", model="test", client=client).extract(
        "A sufficiently long job advert used only for extraction testing."
    )
    return profile.requirements[0], client.responses


@pytest.mark.parametrize(
    ("text", "importance", "source_text"),
    [
        (
            "Professional Python experience",
            RequirementImportance.ESSENTIAL,
            "You must have professional Python experience.",
        ),
        (
            "Kubernetes experience",
            RequirementImportance.DESIRABLE,
            "Nice to have: Kubernetes experience.",
        ),
        (
            "Product judgement",
            RequirementImportance.UNSPECIFIED,
            "The role involves product judgement.",
        ),
        (
            "Distributed systems experience",
            RequirementImportance.ESSENTIAL,
            "Minimum qualifications: distributed systems experience.",
        ),
        (
            "Shipping LLM product experiences",
            RequirementImportance.ESSENTIAL,
            "You may be a good fit if: you have shipped LLM product experiences.",
        ),
        (
            "End-to-end ownership",
            RequirementImportance.ESSENTIAL,
            "What we're looking for: ownership of ambiguous problems end to end.",
        ),
        (
            "Customer-facing engineering experience",
            RequirementImportance.ESSENTIAL,
            "Who you are: a customer-facing engineer who communicates clearly.",
        ),
        (
            "Start-up experience",
            RequirementImportance.DESIRABLE,
            "Preferred qualifications: start-up experience.",
        ),
        (
            "Evaluation framework experience",
            RequirementImportance.DESIRABLE,
            "Nice to have: evaluation framework experience.",
        ),
        (
            "Developer tooling experience",
            RequirementImportance.DESIRABLE,
            "Bonus / a plus: developer tooling experience.",
        ),
        (
            "Own ambiguous problems",
            RequirementImportance.UNSPECIFIED,
            "Responsibilities: own ambiguous problems end to end.",
        ),
    ],
)
def test_extraction_preserves_evidence_supported_importance_labels(
    text: str,
    importance: RequirementImportance,
    source_text: str,
) -> None:
    requirement, _ = _extract(
        text=text,
        importance=importance,
        source_text=source_text,
    )

    assert requirement.importance == importance
    if importance is not RequirementImportance.UNSPECIFIED:
        assert requirement.source_text == source_text


def test_extraction_keeps_unspecified_without_textual_importance_evidence() -> None:
    requirement, _ = _extract(
        text="Strong software engineering ability",
        importance=RequirementImportance.UNSPECIFIED,
        source_text="Strong software engineering ability.",
    )

    assert requirement.importance == RequirementImportance.UNSPECIFIED


def test_extraction_uses_one_model_call_and_supplies_conservative_importance_contract() -> None:
    _, responses = _extract(
        text="Python",
        importance=RequirementImportance.ESSENTIAL,
        source_text="Required: Python.",
    )

    assert len(responses.calls) == 1
    system_prompt = responses.calls[0]["input"][0]["content"]  # type: ignore[index]
    normalized_prompt = " ".join(system_prompt.split())
    assert "section context" in normalized_prompt
    assert "must, required, need" in system_prompt
    assert "You may be a good fit if" in system_prompt
    assert "What we’re looking for" in system_prompt
    assert "primary candidate-criteria section" in normalized_prompt
    assert "Preferred qualifications" in normalized_prompt
    assert "Better to have" in normalized_prompt
    assert "ordinary responsibility" in system_prompt
    assert "model intuition" in system_prompt

    response_format = responses.calls[0]["text"]["format"]  # type: ignore[index]
    assert response_format["type"] == "json_schema"
    assert response_format["name"] == "job_profile"
    assert response_format["strict"] is True
    assert response_format["schema"] == strict_schema_from_pydantic_model(JobProfile)


def test_extraction_emits_provider_compatible_canonical_job_profile_schema() -> None:
    schema = strict_schema_from_pydantic_model(JobProfile)
    serialized = json.dumps(schema)

    assert schema != JobProfile.model_json_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
    assert '"default"' not in serialized
    assert '"minLength"' not in serialized
    assert '"maxLength"' not in serialized

    requirement = schema["$defs"]["JobRequirement"]
    assert requirement["additionalProperties"] is False
    assert requirement["required"] == list(requirement["properties"])
    assert schema["properties"]["title"]["anyOf"][1]["type"] == "null"
    assert requirement["properties"]["source_text"]["anyOf"][1]["type"] == "null"
    assert schema["$defs"]["RequirementCategory"]["enum"] == [item.value for item in RequirementCategory]
    assert "collaboration" not in schema["$defs"]["RequirementCategory"]["enum"]
    assert schema["$defs"]["RequirementImportance"]["enum"] == [item.value for item in RequirementImportance]


@pytest.mark.parametrize(
    ("payload", "case"),
    [
        ({"requirements": [{"text": "Team collaboration", "importance": "essential", "category": "collaboration"}]}, "unknown_category"),
        ({"requirements": [{"text": "Python", "importance": "mandatory", "category": "technical"}]}, "unknown_importance"),
        ({"requirements": [], "unexpected": True}, "extra_root_field"),
        ({"requirements": [{"text": "Python", "importance": "essential", "category": "technical", "unexpected": True}]}, "extra_nested_field"),
        ({"requirements": [{"text": "", "importance": "essential", "category": "technical"}]}, "application_min_length"),
        ("not json", "malformed_json"),
    ],
)
def test_extraction_keeps_application_validation_fail_closed(
    payload: dict[str, object] | str,
    case: str,
) -> None:
    del case
    extractor = OpenAIJobExtractor(api_key="", model="test", client=_FakeClient(payload))

    with pytest.raises(JobExtractionError) as exc_info:
        extractor.extract("A sufficiently long job advert used only for extraction testing.")

    assert exc_info.value.safe_kind == "invalid_output"
    assert "A sufficiently long job advert" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("provider_error", "expected_kind"),
    [
        (SemanticStructuredOutputModelError("private model detail"), "structured_output_model_unsupported"),
        (SemanticStructuredOutputSchemaError("private schema detail"), "structured_output_schema_rejected"),
        (SemanticProviderRequestError("private provider detail"), "provider_failure"),
    ],
)
def test_extraction_maps_provider_structured_output_failures_safely(
    provider_error: Exception,
    expected_kind: str,
) -> None:
    extractor = OpenAIJobExtractor(api_key="", model="test", client=_RaisingClient(provider_error))

    with pytest.raises(JobExtractionError) as exc_info:
        extractor.extract("A sufficiently long job advert used only for extraction testing.")

    assert exc_info.value.safe_kind == expected_kind
    assert "private" not in str(exc_info.value)


def test_extraction_reports_prompt_load_failure_without_path_leakage(tmp_path) -> None:
    missing_prompt = tmp_path / "private-job-extraction-prompt.md"
    extractor = OpenAIJobExtractor(
        api_key="",
        model="test",
        prompt_path=missing_prompt,
        client=_FakeClient({"requirements": []}),
    )

    with pytest.raises(JobExtractionError) as exc_info:
        extractor.extract("A sufficiently long job advert used only for extraction testing.")

    assert exc_info.value.safe_kind == "prompt_load_failure"
    assert str(missing_prompt) not in str(exc_info.value)


def test_extraction_seniority_contract_uses_scope_as_well_as_title() -> None:
    _, responses = _extract(
        text="Systems engineering leadership",
        importance=RequirementImportance.UNSPECIFIED,
        source_text="Role overview.",
    )

    system_prompt = responses.calls[0]["input"][0]["content"]  # type: ignore[index]
    normalized_prompt = " ".join(system_prompt.split())

    assert "title-level word" in normalized_prompt
    assert "ownership or decision authority" in normalized_prompt
    assert "architecture or technical leadership" in normalized_prompt
    assert "use `null`" in system_prompt


def test_extraction_normalizes_clearly_separable_grouped_criteria() -> None:
    client = _FakeClient(
        {
            "requirements": [
                {
                    "text": "Familiarity with version control, testing, and CI/CD",
                    "importance": "essential",
                    "category": "technical",
                    "source_text": (
                        "About you: Familiarity with modern software engineering "
                        "practices (version control, testing, CI/CD)"
                    ),
                }
            ]
        }
    )

    profile = OpenAIJobExtractor(api_key="", model="test", client=client).extract(
        "A sufficiently long job advert used only for extraction testing."
    )

    assert [requirement.text for requirement in profile.requirements] == [
        "Familiarity with CI/CD",
        "Familiarity with testing",
        "Familiarity with version control",
    ]

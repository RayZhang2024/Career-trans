import json
from types import SimpleNamespace

import pytest

from app.agents.job_extraction import OpenAIJobExtractor
from app.schemas.job import RequirementImportance


class _FakeResponses:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self.payload))


class _FakeClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.responses = _FakeResponses(payload)


def _extract(
    *,
    text: str,
    importance: RequirementImportance,
    source_text: str,
) -> tuple[RequirementImportance, _FakeResponses]:
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
    return profile.requirements[0].importance, client.responses


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
            "Start-up experience",
            RequirementImportance.DESIRABLE,
            "Preferred qualifications: start-up experience.",
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
    actual_importance, _ = _extract(
        text=text,
        importance=importance,
        source_text=source_text,
    )

    assert actual_importance == importance


def test_extraction_keeps_unspecified_without_textual_importance_evidence() -> None:
    actual_importance, _ = _extract(
        text="Strong software engineering ability",
        importance=RequirementImportance.UNSPECIFIED,
        source_text="Strong software engineering ability.",
    )

    assert actual_importance == RequirementImportance.UNSPECIFIED


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
    assert "Preferred qualifications" in system_prompt
    assert "ordinary responsibility" in system_prompt
    assert "model intuition" in system_prompt

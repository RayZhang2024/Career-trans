import json
from types import SimpleNamespace

from app.agents.career_alignment import OpenAICareerAlignmentAgent
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import CareerAlignmentDimension
from app.schemas.job import JobProfile


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text=json.dumps(
                {
                    "confidence": "medium",
                    "dimensions": [
                        {
                            "dimension": dimension.value,
                            "score": 0.5,
                            "reasoning": "Supported by the supplied facts.",
                        }
                        for dimension in CareerAlignmentDimension
                    ],
                    "strategic_strengths": [],
                    "strategic_tradeoffs": [],
                    "reasoning": "Scope evidence is incomplete.",
                }
            )
        )


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


def test_career_alignment_uses_bounded_job_and_candidate_scope_without_extra_call() -> None:
    client = _FakeClient()
    agent = OpenAICareerAlignmentAgent(
        api_key="",
        model="test",
        client=client,
    )

    agent.assess(
        JobProfile(
            title="Engineer",
            seniority="Scope includes architecture ownership and mentoring.",
            responsibilities=[
                "Own technical architecture decisions for a customer programme.",
                "Mentor engineers and influence cross-functional delivery.",
            ],
        ),
        CandidateContext(
            profile_text=(
                "Delivered customer-facing systems with technical ownership and "
                "mentored engineers."
            ),
            career_strategy_text="Grow into roles with wider technical leadership.",
            job_search_criteria_text="Prefer permanent roles with clear responsibility.",
        ),
        FitAssessment(fit_score=50.0),
    )

    assert len(client.responses.calls) == 1
    request = client.responses.calls[0]
    prompt = request["input"][0]["content"]  # type: ignore[index]
    payload = json.loads(request["input"][1]["content"].split("INPUT:\n", 1)[1])  # type: ignore[index]

    assert "Assess supported role scope, not title wording alone" in prompt
    assert "technical ownership" in payload["candidate_context"]["profile_summary"]
    assert payload["job_profile"]["responsibilities"] == [
        "Own technical architecture decisions for a customer programme.",
        "Mentor engineers and influence cross-functional delivery.",
    ]
    assert "requirements" not in payload["job_profile"]

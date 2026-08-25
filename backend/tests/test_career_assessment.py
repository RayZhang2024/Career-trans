from collections.abc import Mapping

import pytest

from app.agents.career_alignment import CareerAlignmentError
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import (
    AlignmentConfidence,
    CareerAlignmentDimension,
    CareerAlignmentJudgement,
    CareerDimensionAssessment,
)
from app.schemas.job import JobProfile
from app.workflows.career_analysis_graph import CareerAnalysisGraph
from app.services.career_assessment_service import CareerAssessmentService


class FakeCareerAlignmentAgent:
    def __init__(
        self,
        scores: Mapping[CareerAlignmentDimension, float],
        *,
        confidence: AlignmentConfidence = AlignmentConfidence.HIGH,
        duplicate: CareerAlignmentDimension | None = None,
    ) -> None:
        self._scores = scores
        self._confidence = confidence
        self._duplicate = duplicate
        self.received_contexts: list[CandidateContext] = []
        self.received_fit_assessments: list[FitAssessment] = []

    def assess(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateContext,
        fit_assessment: FitAssessment,
    ) -> CareerAlignmentJudgement:
        self.received_contexts.append(candidate_context)
        self.received_fit_assessments.append(fit_assessment)
        dimensions = [
            CareerDimensionAssessment(
                dimension=dimension,
                score=score,
                reasoning=f"Test reasoning for {dimension.value}.",
            )
            for dimension, score in self._scores.items()
        ]
        if self._duplicate is not None:
            dimensions.append(
                CareerDimensionAssessment(
                    dimension=self._duplicate,
                    score=0.5,
                    reasoning="Duplicate test dimension.",
                )
            )

        return CareerAlignmentJudgement(
            confidence=self._confidence,
            dimensions=dimensions,
            strategic_strengths=["Relevant growth opportunity."],
            strategic_tradeoffs=["Some role information is uncertain."],
            reasoning="Assessment based only on supplied strategy and job facts.",
        )


def all_scores(score: float) -> dict[CareerAlignmentDimension, float]:
    return {dimension: score for dimension in CareerAlignmentDimension}


def rich_candidate(**updates: str) -> CandidateContext:
    values = {
        "career_strategy_text": (
            "Move toward product-focused software leadership while building "
            "production engineering and customer ownership capabilities."
        ),
        "job_search_criteria_text": (
            "Seek permanent hybrid roles in London with clear ownership and "
            "progression opportunities."
        ),
    }
    values.update(updates)
    return CandidateContext(**values)


def informative_job(**updates: object) -> JobProfile:
    values: dict[str, object] = {
        "title": "Software Product Lead",
        "seniority": "Lead",
        "location": "London",
        "work_arrangement": "Hybrid",
        "employment_type": "Permanent",
        "responsibilities": ["Own a customer-facing software product."],
        "domain_knowledge": ["Software products"],
    }
    values.update(updates)
    return JobProfile(**values)


def fit(score: float) -> FitAssessment:
    return FitAssessment(
        fit_score=score,
        essential_score=score,
        desirable_score=None,
    )


def test_all_dimensions_are_accepted_and_weighted_in_python() -> None:
    agent = FakeCareerAlignmentAgent(
        {
            CareerAlignmentDimension.TARGET_ROLE: 1.0,
            CareerAlignmentDimension.CAPABILITY_GROWTH: 0.8,
            CareerAlignmentDimension.INDUSTRY_DOMAIN: 0.6,
            CareerAlignmentDimension.SENIORITY_PROGRESSION: 0.4,
            CareerAlignmentDimension.LONG_TERM_OPTIONALITY: 0.2,
            CareerAlignmentDimension.PREFERENCE_CONSTRAINT: 0.0,
        }
    )

    assessment = CareerAssessmentService(agent).assess(
        informative_job(),
        rich_candidate(),
        fit(75.0),
    )

    assert assessment.career_alignment_score == 63.0
    assert len(assessment.dimensions) == 6
    assert assessment.confidence == AlignmentConfidence.HIGH


def test_missing_required_dimension_is_rejected() -> None:
    scores = all_scores(0.7)
    scores.pop(CareerAlignmentDimension.PREFERENCE_CONSTRAINT)

    with pytest.raises(CareerAlignmentError, match="Missing"):
        CareerAssessmentService(FakeCareerAlignmentAgent(scores)).assess(
            informative_job(),
            rich_candidate(),
            fit(70.0),
        )


def test_duplicate_dimension_is_rejected() -> None:
    agent = FakeCareerAlignmentAgent(
        all_scores(0.7),
        duplicate=CareerAlignmentDimension.TARGET_ROLE,
    )

    with pytest.raises(CareerAlignmentError, match="duplicate"):
        CareerAssessmentService(agent).assess(
            informative_job(),
            rich_candidate(),
            fit(70.0),
        )


def test_career_alignment_remains_independent_from_fit() -> None:
    candidate = rich_candidate()
    job = informative_job()
    strong_fit = fit(95.0)
    stretch_fit = fit(35.0)

    low_agent = FakeCareerAlignmentAgent(all_scores(0.2))
    high_agent = FakeCareerAlignmentAgent(all_scores(0.9))
    low_alignment = CareerAssessmentService(low_agent).assess(
        job,
        candidate,
        strong_fit,
    )
    high_alignment = CareerAssessmentService(high_agent).assess(
        job,
        candidate,
        stretch_fit,
    )

    assert low_alignment.career_alignment_score == 20.0
    assert high_alignment.career_alignment_score == 90.0
    assert low_agent.received_fit_assessments[0].fit_score == 95.0
    assert high_agent.received_fit_assessments[0].fit_score == 35.0


def test_weak_strategy_caps_confidence_at_low() -> None:
    assessment = CareerAssessmentService(
        FakeCareerAlignmentAgent(all_scores(0.7))
    ).assess(
        informative_job(),
        CandidateContext(career_strategy_text="I want a good job."),
        fit(70.0),
    )

    assert assessment.confidence == AlignmentConfidence.LOW


def test_unknown_job_facts_reduce_confidence_without_reducing_alignment() -> None:
    assessment = CareerAssessmentService(
        FakeCareerAlignmentAgent(all_scores(0.8))
    ).assess(
        JobProfile(title="Role with limited advert detail"),
        rich_candidate(),
        fit(70.0),
    )

    assert assessment.career_alignment_score == 80.0
    assert assessment.confidence == AlignmentConfidence.MEDIUM


def test_confidence_cap_counts_merged_known_listing_metadata() -> None:
    candidate = rich_candidate()
    extracted = JobProfile(title="Extracted title")
    from app.schemas.discovery import JobListing

    merged = CareerAnalysisGraph._merge_known_listing_metadata(
        extracted,
        JobListing(
            source="external",
            title="Known title",
            location="London",
            work_arrangement="Hybrid",
            employment_type="Permanent",
            url="https://jobs.example.test/known",
        ),
    )

    assert CareerAssessmentService._confidence_cap(candidate, merged) is AlignmentConfidence.HIGH


@pytest.mark.parametrize(
    ("source_name", "strategy", "job_title"),
    [
        (
            "technical_specialist",
            "Transition from technical research into production AI and software "
            "engineering while retaining deep specialist problem solving.",
            "Machine Learning Engineer",
        ),
        (
            "project_manager",
            "Progress into programme leadership with wider delivery ownership, "
            "people leadership, and cross-functional strategic responsibility.",
            "Senior Programme Manager",
        ),
        (
            "graduate",
            "Secure a first professional role that provides broad learning, "
            "mentoring, practical responsibility, and transferable capabilities.",
            "Graduate Analyst",
        ),
    ],
)
def test_varied_candidate_strategies_are_passed_dynamically(
    source_name: str,
    strategy: str,
    job_title: str,
) -> None:
    candidate = rich_candidate(
        source_name=source_name,
        career_strategy_text=strategy,
    )
    job = informative_job(title=job_title)
    agent = FakeCareerAlignmentAgent(all_scores(0.7))

    assessment = CareerAssessmentService(agent).assess(job, candidate, fit(60.0))

    assert assessment.career_alignment_score == 70.0
    assert agent.received_contexts[0] == candidate

import pytest

from app.schemas.assessment import FitAssessment, Gap, GapSeverity, GapType
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.job import JobRequirement, RequirementImportance
from app.schemas.recommendation import Recommendation
from app.services.recommendation_service import (
    RecommendationService,
    RecommendationThresholds,
)


def fit_assessment(
    score: float,
    *,
    hard_blockers: list[int] | None = None,
    strengths: list[int] | None = None,
    gaps: list[Gap] | None = None,
) -> FitAssessment:
    return FitAssessment(
        fit_score=score,
        essential_score=score,
        hard_blockers=hard_blockers or [],
        strengths=strengths or [],
        gaps=gaps or [],
    )


def career_assessment(
    score: float,
    *,
    confidence: AlignmentConfidence = AlignmentConfidence.HIGH,
    strengths: list[str] | None = None,
    tradeoffs: list[str] | None = None,
) -> CareerAssessment:
    return CareerAssessment(
        career_alignment_score=score,
        confidence=confidence,
        dimensions=[],
        strategic_strengths=strengths or [],
        strategic_tradeoffs=tradeoffs or [],
        reasoning="Synthetic career assessment.",
    )


def test_hard_blocker_always_produces_skip() -> None:
    result = RecommendationService().assess(
        fit_assessment(99.0, hard_blockers=[2]),
        career_assessment(99.0),
    )

    assert result.recommendation == Recommendation.SKIP
    assert result.rule_id == "hard_blocker"
    assert result.hard_blockers == [2]


def test_recommendation_uses_total_fit_when_essential_diagnostic_is_absent() -> None:
    result = RecommendationService().assess(
        FitAssessment(fit_score=80.0, essential_score=None),
        career_assessment(70.0),
    )

    assert result.recommendation == Recommendation.APPLY
    assert result.rule_id == "strong_fit_strong_alignment"


@pytest.mark.parametrize(
    ("fit", "alignment", "expected", "rule_id"),
    [
        (80.0, 70.0, Recommendation.APPLY, "strong_fit_strong_alignment"),
        (79.9, 70.0, Recommendation.CONSIDER, "mixed_fit_alignment"),
        (80.0, 69.9, Recommendation.CONSIDER, "mixed_fit_alignment"),
        (70.0, 85.0, Recommendation.APPLY, "strategic_stretch"),
        (69.9, 85.0, Recommendation.CONSIDER, "mixed_fit_alignment"),
        (70.0, 84.9, Recommendation.CONSIDER, "mixed_fit_alignment"),
    ],
)
def test_apply_threshold_boundaries(
    fit: float,
    alignment: float,
    expected: Recommendation,
    rule_id: str,
) -> None:
    result = RecommendationService().assess(
        fit_assessment(fit),
        career_assessment(alignment),
    )

    assert result.recommendation == expected
    assert result.rule_id == rule_id


def test_high_fit_with_weak_alignment_is_consider() -> None:
    result = RecommendationService().assess(
        fit_assessment(92.0),
        career_assessment(45.0),
    )

    assert result.recommendation == Recommendation.CONSIDER


def test_moderate_fit_with_strong_alignment_below_stretch_is_consider() -> None:
    result = RecommendationService().assess(
        fit_assessment(69.9),
        career_assessment(95.0),
    )

    assert result.recommendation == Recommendation.CONSIDER


@pytest.mark.parametrize(
    ("fit", "alignment", "expected", "rule_id"),
    [
        (39.9, 100.0, Recommendation.SKIP, "very_low_fit"),
        (40.0, 69.9, Recommendation.SKIP, "low_fit_low_alignment"),
        (49.9, 69.9, Recommendation.SKIP, "low_fit_low_alignment"),
        (50.0, 69.9, Recommendation.CONSIDER, "mixed_fit_alignment"),
        (40.0, 70.0, Recommendation.CONSIDER, "mixed_fit_alignment"),
    ],
)
def test_skip_threshold_boundaries(
    fit: float,
    alignment: float,
    expected: Recommendation,
    rule_id: str,
) -> None:
    result = RecommendationService().assess(
        fit_assessment(fit),
        career_assessment(alignment),
    )

    assert result.recommendation == expected
    assert result.rule_id == rule_id


def test_low_alignment_confidence_prevents_score_based_apply() -> None:
    result = RecommendationService().assess(
        fit_assessment(86.0),
        career_assessment(82.0, confidence=AlignmentConfidence.LOW),
    )

    assert result.recommendation == Recommendation.CONSIDER
    assert result.rule_id == "low_alignment_confidence"


def test_low_confidence_does_not_automatically_skip() -> None:
    result = RecommendationService().assess(
        fit_assessment(60.0),
        career_assessment(80.0, confidence=AlignmentConfidence.LOW),
    )

    assert result.recommendation == Recommendation.CONSIDER
    assert result.rule_id == "mixed_fit_alignment"


def test_source_scores_and_assessments_remain_unchanged() -> None:
    gap = Gap(
        requirement_index=1,
        requirement=JobRequirement(
            text="Production deployment experience",
            importance=RequirementImportance.ESSENTIAL,
        ),
        gap_type=GapType.EVIDENCE_GAP,
        severity=GapSeverity.MEDIUM,
        reason="Production deployment experience needs stronger evidence.",
    )
    fit = fit_assessment(82.0, strengths=[0], gaps=[gap])
    career = career_assessment(
        78.0,
        strengths=["Builds a stated target capability."],
        tradeoffs=["Location details are unknown."],
    )
    fit_before = fit.model_dump()
    career_before = career.model_dump()

    result = RecommendationService().assess(fit, career)

    assert result.fit_score == 82.0
    assert result.career_alignment_score == 78.0
    assert result.key_strengths == [
        "Strong evidence for requirement 1.",
        "Builds a stated target capability.",
    ]
    assert result.key_tradeoffs == [
        "Production deployment experience needs stronger evidence.",
        "Location details are unknown.",
    ]
    assert fit.model_dump() == fit_before
    assert career.model_dump() == career_before


def test_thresholds_can_be_calibrated_without_changing_rule_logic() -> None:
    service = RecommendationService(
        RecommendationThresholds(strong_fit=75.0, positive_alignment=65.0)
    )

    result = service.assess(
        fit_assessment(75.0),
        career_assessment(65.0),
    )

    assert result.recommendation == Recommendation.APPLY
    assert result.rule_id == "strong_fit_strong_alignment"

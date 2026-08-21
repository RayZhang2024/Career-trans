from dataclasses import dataclass

from app.schemas.assessment import FitAssessment
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.recommendation import (
    Recommendation,
    RecommendationAssessment,
)


@dataclass(frozen=True)
class RecommendationThresholds:
    strong_fit: float = 80.0
    positive_alignment: float = 70.0
    stretch_fit: float = 70.0
    stretch_alignment: float = 85.0
    very_low_fit: float = 40.0
    low_fit: float = 50.0
    low_alignment: float = 70.0


DEFAULT_RECOMMENDATION_THRESHOLDS = RecommendationThresholds()


class RecommendationService:
    """Combine fit and career value through explicit deterministic rules."""

    def __init__(
        self,
        thresholds: RecommendationThresholds = DEFAULT_RECOMMENDATION_THRESHOLDS,
    ) -> None:
        self._thresholds = thresholds

    def assess(
        self,
        fit_assessment: FitAssessment,
        career_assessment: CareerAssessment,
    ) -> RecommendationAssessment:
        fit = fit_assessment.fit_score
        alignment = career_assessment.career_alignment_score

        if fit_assessment.hard_blockers:
            return self._build(
                Recommendation.SKIP,
                "hard_blocker",
                "The role contains a confirmed hard blocker that prevents "
                "credible pursuit.",
                fit_assessment,
                career_assessment,
            )

        if fit < self._thresholds.very_low_fit:
            return self._build(
                Recommendation.SKIP,
                "very_low_fit",
                "Current-role fit is too low for credible pursuit, even if the "
                "role may have strategic value.",
                fit_assessment,
                career_assessment,
            )

        if (
            fit < self._thresholds.low_fit
            and alignment < self._thresholds.low_alignment
        ):
            return self._build(
                Recommendation.SKIP,
                "low_fit_low_alignment",
                "Both current-role fit and career alignment are below the viable "
                "V1 thresholds.",
                fit_assessment,
                career_assessment,
            )

        strong_case = (
            fit >= self._thresholds.strong_fit
            and alignment >= self._thresholds.positive_alignment
        )
        stretch_case = (
            fit >= self._thresholds.stretch_fit
            and alignment >= self._thresholds.stretch_alignment
        )

        if (
            career_assessment.confidence == AlignmentConfidence.LOW
            and (strong_case or stretch_case)
        ):
            return self._build(
                Recommendation.CONSIDER,
                "low_alignment_confidence",
                "The scores otherwise support applying, but career-alignment "
                "confidence is low; confirm the missing strategic information.",
                fit_assessment,
                career_assessment,
            )

        if strong_case:
            return self._build(
                Recommendation.APPLY,
                "strong_fit_strong_alignment",
                "Strong current-role fit and positive career alignment, with no "
                "hard blockers.",
                fit_assessment,
                career_assessment,
            )

        if stretch_case:
            return self._build(
                Recommendation.APPLY,
                "strategic_stretch",
                "Credible current-role fit and very strong career alignment support "
                "a deliberate strategic stretch application.",
                fit_assessment,
                career_assessment,
            )

        return self._build(
            Recommendation.CONSIDER,
            "mixed_fit_alignment",
            "Fit and career alignment present a mixed or borderline case that "
            "requires human judgement.",
            fit_assessment,
            career_assessment,
        )

    @staticmethod
    def _build(
        recommendation: Recommendation,
        rule_id: str,
        reasoning: str,
        fit_assessment: FitAssessment,
        career_assessment: CareerAssessment,
    ) -> RecommendationAssessment:
        return RecommendationAssessment(
            recommendation=recommendation,
            fit_score=fit_assessment.fit_score,
            career_alignment_score=career_assessment.career_alignment_score,
            career_alignment_confidence=career_assessment.confidence,
            rule_id=rule_id,
            reasoning=reasoning,
            key_strengths=[
                f"Strong evidence for requirement {index + 1}."
                for index in fit_assessment.strengths
            ]
            + career_assessment.strategic_strengths,
            key_tradeoffs=[gap.reason for gap in fit_assessment.gaps]
            + career_assessment.strategic_tradeoffs,
            hard_blockers=list(fit_assessment.hard_blockers),
        )

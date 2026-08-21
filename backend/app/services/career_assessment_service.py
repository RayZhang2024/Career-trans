from app.agents.career_alignment import (
    CareerAlignmentAgent,
    CareerAlignmentError,
)
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import (
    AlignmentConfidence,
    CareerAlignmentDimension,
    CareerAssessment,
)
from app.schemas.job import JobProfile


CAREER_ALIGNMENT_WEIGHTS: dict[CareerAlignmentDimension, float] = {
    CareerAlignmentDimension.TARGET_ROLE: 0.25,
    CareerAlignmentDimension.CAPABILITY_GROWTH: 0.25,
    CareerAlignmentDimension.INDUSTRY_DOMAIN: 0.15,
    CareerAlignmentDimension.SENIORITY_PROGRESSION: 0.15,
    CareerAlignmentDimension.LONG_TERM_OPTIONALITY: 0.15,
    CareerAlignmentDimension.PREFERENCE_CONSTRAINT: 0.05,
}

_CONFIDENCE_RANK = {
    AlignmentConfidence.LOW: 0,
    AlignmentConfidence.MEDIUM: 1,
    AlignmentConfidence.HIGH: 2,
}


class CareerAssessmentService:
    def __init__(self, agent: CareerAlignmentAgent) -> None:
        self._agent = agent

    def assess(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateContext,
        fit_assessment: FitAssessment,
    ) -> CareerAssessment:
        judgement = self._agent.assess(
            job_profile,
            candidate_context,
            fit_assessment,
        )
        by_dimension = {
            item.dimension: item for item in judgement.dimensions
        }

        if len(by_dimension) != len(judgement.dimensions):
            raise CareerAlignmentError(
                "Career alignment contains duplicate dimensions."
            )

        required = set(CAREER_ALIGNMENT_WEIGHTS)
        returned = set(by_dimension)
        if returned != required:
            missing = sorted(dimension.value for dimension in required - returned)
            unexpected = sorted(dimension.value for dimension in returned - required)
            raise CareerAlignmentError(
                "Career alignment must contain every required dimension exactly once. "
                f"Missing: {missing}; unexpected: {unexpected}."
            )

        score = sum(
            by_dimension[dimension].score * weight
            for dimension, weight in CAREER_ALIGNMENT_WEIGHTS.items()
        ) * 100.0

        confidence_cap = self._confidence_cap(
            candidate_context,
            job_profile,
        )
        confidence = min(
            (judgement.confidence, confidence_cap),
            key=lambda item: _CONFIDENCE_RANK[item],
        )

        return CareerAssessment(
            career_alignment_score=round(score, 1),
            confidence=confidence,
            dimensions=[
                by_dimension[dimension]
                for dimension in CAREER_ALIGNMENT_WEIGHTS
            ],
            strategic_strengths=judgement.strategic_strengths,
            strategic_tradeoffs=judgement.strategic_tradeoffs,
            reasoning=judgement.reasoning,
        )

    @staticmethod
    def _confidence_cap(
        candidate_context: CandidateContext,
        job_profile: JobProfile,
    ) -> AlignmentConfidence:
        strategy = candidate_context.career_strategy_text.strip()
        criteria = candidate_context.job_search_criteria_text.strip()
        combined_direction = f"{strategy} {criteria}".strip()

        # A short or absent statement cannot support confident strategic inference.
        if len(combined_direction) < 80:
            return AlignmentConfidence.LOW

        job_signals = (
            job_profile.title,
            job_profile.seniority,
            job_profile.location,
            job_profile.work_arrangement,
            job_profile.employment_type,
            job_profile.responsibilities,
            job_profile.domain_knowledge,
        )
        known_job_signals = sum(bool(signal) for signal in job_signals)

        if not strategy or not criteria or known_job_signals < 3:
            return AlignmentConfidence.MEDIUM

        return AlignmentConfidence.HIGH

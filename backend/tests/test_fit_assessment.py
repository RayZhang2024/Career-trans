from app.schemas.assessment import GapType
from app.schemas.job import (
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)
from app.schemas.matching import MatchType, RequirementMatch
from app.services.fit_assessment_service import FitAssessmentService


def make_match(
    index: int,
    text: str,
    importance: RequirementImportance,
    match_type: MatchType,
    score: float,
) -> RequirementMatch:
    return RequirementMatch(
        requirement_index=index,
        requirement=JobRequirement(
            text=text,
            importance=importance,
            category=RequirementCategory.TECHNICAL,
        ),
        match_type=match_type,
        score=score,
        evidence_ids=[],
        reasoning="Test evidence.",
    )


def test_fit_assessment_weights_essential_requirements_more() -> None:
    matches = [
        make_match(
            0,
            "Python",
            RequirementImportance.ESSENTIAL,
            MatchType.DEMONSTRATED,
            0.95,
        ),
        make_match(
            1,
            "React",
            RequirementImportance.DESIRABLE,
            MatchType.INFERRED,
            0.30,
        ),
    ]

    assessment = FitAssessmentService().assess(matches)

    assert assessment.fit_score == 78.7
    assert assessment.essential_score == 95.0
    assert assessment.desirable_score == 30.0


def test_missing_essential_requirement_becomes_hard_blocker() -> None:
    matches = [
        make_match(
            0,
            "Mandatory security clearance",
            RequirementImportance.ESSENTIAL,
            MatchType.MISSING,
            0.0,
        )
    ]

    assessment = FitAssessmentService().assess(matches)

    assert assessment.hard_blockers == [0]
    assert assessment.gaps[0].gap_type == GapType.HARD_BLOCKER


def test_unknown_essential_eligibility_is_an_evidence_gap_not_a_hard_blocker() -> None:
    match = RequirementMatch(
        requirement_index=0,
        requirement=JobRequirement(
            text="Must be located in the UK.",
            importance=RequirementImportance.ESSENTIAL,
            category=RequirementCategory.LOCATION,
        ),
        match_type=MatchType.UNKNOWN,
        score=0.0,
        reasoning="Location eligibility is not provided.",
    )

    assessment = FitAssessmentService().assess([match])

    assert assessment.hard_blockers == []
    assert assessment.gaps[0].gap_type == GapType.EVIDENCE_GAP


def test_confirmed_essential_eligibility_incompatibility_is_a_hard_blocker() -> None:
    match = RequirementMatch(
        requirement_index=0,
        requirement=JobRequirement(
            text="Must be located in the UK.",
            importance=RequirementImportance.ESSENTIAL,
            category=RequirementCategory.LOCATION,
        ),
        match_type=MatchType.INCOMPATIBLE,
        score=0.0,
        reasoning="Candidate location is incompatible.",
    )

    assessment = FitAssessmentService().assess([match])

    assert assessment.hard_blockers == [0]
    assert assessment.gaps[0].gap_type == GapType.HARD_BLOCKER


def test_low_desirable_requirement_becomes_learnable_gap() -> None:
    matches = [
        make_match(
            0,
            "React",
            RequirementImportance.DESIRABLE,
            MatchType.INFERRED,
            0.30,
        )
    ]

    assessment = FitAssessmentService().assess(matches)

    assert assessment.gaps[0].gap_type == GapType.EVIDENCE_GAP

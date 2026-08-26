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
    category: RequirementCategory = RequirementCategory.TECHNICAL,
    source_text: str | None = None,
) -> RequirementMatch:
    return RequirementMatch(
        requirement_index=index,
        requirement=JobRequirement(
            text=text,
            importance=importance,
            category=category,
            source_text=source_text,
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


def test_no_essential_requirements_has_none_diagnostic_without_changing_total_fit() -> None:
    matches = [
        make_match(
            0,
            "General product judgement",
            RequirementImportance.UNSPECIFIED,
            MatchType.DEMONSTRATED,
            0.7,
        ),
        make_match(
            1,
            "Kubernetes",
            RequirementImportance.DESIRABLE,
            MatchType.INFERRED,
            0.3,
        ),
    ]

    assessment = FitAssessmentService().assess(matches)

    assert assessment.fit_score == 56.7
    assert assessment.essential_score is None
    assert assessment.desirable_score == 30.0
    assert assessment.model_dump(mode="json")["essential_score"] is None


def test_zero_essential_match_remains_a_numeric_zero() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Python",
                RequirementImportance.ESSENTIAL,
                MatchType.MISSING,
                0.0,
            )
        ]
    )

    assert assessment.fit_score == 0.0
    assert assessment.essential_score == 0.0
    assert assessment.desirable_score is None


def test_all_unspecified_requirements_keep_numeric_total_and_empty_category_diagnostics() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "System design",
                RequirementImportance.UNSPECIFIED,
                MatchType.TRANSFERABLE,
                0.8,
            )
        ]
    )

    assert assessment.fit_score == 80.0
    assert assessment.essential_score is None
    assert assessment.desirable_score is None


def test_missing_essential_technical_requirement_is_a_capability_gap_not_a_hard_blocker() -> None:
    matches = [
        make_match(
            0,
            "Python",
            RequirementImportance.ESSENTIAL,
            MatchType.MISSING,
            0.0,
        )
    ]

    assessment = FitAssessmentService().assess(matches)

    assert assessment.hard_blockers == []
    assert assessment.gaps[0].gap_type == GapType.MEANINGFUL_CAPABILITY_GAP


def test_missing_essential_cloud_and_domain_capabilities_are_not_blockers() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Cloud IAM and cost management",
                RequirementImportance.ESSENTIAL,
                MatchType.MISSING,
                0.05,
            ),
            make_match(
                1,
                "Insurance domain experience",
                RequirementImportance.ESSENTIAL,
                MatchType.INFERRED,
                0.2,
                category=RequirementCategory.DOMAIN,
            ),
        ]
    )

    assert assessment.hard_blockers == []
    assert [gap.gap_type for gap in assessment.gaps] == [
        GapType.MEANINGFUL_CAPABILITY_GAP,
        GapType.MEANINGFUL_CAPABILITY_GAP,
    ]


def test_mandatory_generic_security_capability_is_not_a_clearance_blocker() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Must have cloud security and IAM experience.",
                RequirementImportance.ESSENTIAL,
                MatchType.INCOMPATIBLE,
                0.0,
                category=RequirementCategory.SECURITY,
            )
        ]
    )

    assert assessment.hard_blockers == []
    assert assessment.gaps[0].gap_type is GapType.MEANINGFUL_CAPABILITY_GAP


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


def test_mandatory_work_authorisation_and_security_incompatibilities_are_hard_blockers() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Candidates must have the right to work in the UK.",
                RequirementImportance.ESSENTIAL,
                MatchType.INCOMPATIBLE,
                0.0,
                category=RequirementCategory.WORK_AUTHORIZATION,
            ),
            make_match(
                1,
                "Active SC clearance is required.",
                RequirementImportance.ESSENTIAL,
                MatchType.INCOMPATIBLE,
                0.0,
                category=RequirementCategory.SECURITY,
            ),
        ]
    )

    assert assessment.hard_blockers == [0, 1]
    assert all(gap.gap_type is GapType.HARD_BLOCKER for gap in assessment.gaps)


def test_mandatory_professional_licence_with_confirmed_incompatibility_is_a_hard_blocker() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Must hold a current professional engineering licence.",
                RequirementImportance.ESSENTIAL,
                MatchType.INCOMPATIBLE,
                0.0,
                category=RequirementCategory.OTHER,
            )
        ]
    )

    assert assessment.hard_blockers == [0]
    assert assessment.gaps[0].gap_type is GapType.HARD_BLOCKER


def test_essential_education_with_equivalent_experience_is_not_a_hard_blocker() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Degree or equivalent practical experience.",
                RequirementImportance.ESSENTIAL,
                MatchType.MISSING,
                0.0,
                category=RequirementCategory.EDUCATION,
            )
        ]
    )

    assert assessment.hard_blockers == []
    assert assessment.gaps[0].gap_type is GapType.MEANINGFUL_CAPABILITY_GAP


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


def test_grouped_and_atomic_matches_from_one_source_have_equal_fit_weight() -> None:
    source_one = "Experience with Skill A, Skill B, and Skill C."
    source_two = "Experience with Skill D."
    grouped = [
        make_match(
            0,
            "Experience with Skill A, Skill B, and Skill C",
            RequirementImportance.ESSENTIAL,
            MatchType.TRANSFERABLE,
            0.6,
            source_text=source_one,
        ),
        make_match(
            1,
            "Experience with Skill D",
            RequirementImportance.ESSENTIAL,
            MatchType.INFERRED,
            0.2,
            source_text=source_two,
        ),
    ]
    atomic = [
        make_match(
            0,
            "Skill A",
            RequirementImportance.ESSENTIAL,
            MatchType.DEMONSTRATED,
            0.9,
            source_text=source_one,
        ),
        make_match(
            1,
            "Skill B",
            RequirementImportance.ESSENTIAL,
            MatchType.TRANSFERABLE,
            0.6,
            source_text=source_one,
        ),
        make_match(
            2,
            "Skill C",
            RequirementImportance.ESSENTIAL,
            MatchType.INFERRED,
            0.3,
            source_text=source_one,
        ),
        make_match(
            3,
            "Experience with Skill D",
            RequirementImportance.ESSENTIAL,
            MatchType.INFERRED,
            0.2,
            source_text=source_two,
        ),
    ]

    grouped_assessment = FitAssessmentService().assess(grouped)
    atomic_assessment = FitAssessmentService().assess(atomic)

    assert grouped_assessment.fit_score == atomic_assessment.fit_score == 40.0
    assert grouped_assessment.essential_score == atomic_assessment.essential_score == 40.0


def test_separate_source_statements_remain_separate_scoring_units() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Skill A",
                RequirementImportance.ESSENTIAL,
                MatchType.DEMONSTRATED,
                0.9,
                source_text="Employer source S1.",
            ),
            make_match(
                1,
                "Skill B",
                RequirementImportance.ESSENTIAL,
                MatchType.INFERRED,
                0.3,
                source_text="Employer source S2.",
            ),
            make_match(
                2,
                "Skill C",
                RequirementImportance.ESSENTIAL,
                MatchType.INFERRED,
                0.3,
                source_text="Employer source S3.",
            ),
        ]
    )

    assert assessment.fit_score == 50.0


def test_source_groups_keep_importance_weighting_and_diagnostics() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Core A",
                RequirementImportance.ESSENTIAL,
                MatchType.DEMONSTRATED,
                0.8,
                source_text="Core criterion.",
            ),
            make_match(
                1,
                "Core B",
                RequirementImportance.ESSENTIAL,
                MatchType.INFERRED,
                0.4,
                source_text="Core criterion.",
            ),
            make_match(
                2,
                "Optional A",
                RequirementImportance.DESIRABLE,
                MatchType.INFERRED,
                0.3,
                source_text="Preferred criterion.",
            ),
            make_match(
                3,
                "Context A",
                RequirementImportance.UNSPECIFIED,
                MatchType.DEMONSTRATED,
                0.9,
                source_text="Context criterion.",
            ),
        ]
    )

    assert assessment.fit_score == 65.0
    assert assessment.essential_score == 60.0
    assert assessment.desirable_score == 30.0


def test_source_grouping_preserves_requirement_level_strengths_and_gaps() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                4,
                "Skill A",
                RequirementImportance.ESSENTIAL,
                MatchType.DEMONSTRATED,
                0.9,
                source_text="Required skills: Skill A and Skill B.",
            ),
            make_match(
                5,
                "Skill B",
                RequirementImportance.ESSENTIAL,
                MatchType.MISSING,
                0.1,
                source_text="Required skills: Skill A and Skill B.",
            ),
        ]
    )

    assert assessment.strengths == [4]
    assert [gap.requirement_index for gap in assessment.gaps] == [5]


def test_confirmed_hard_blocker_is_not_averaged_into_its_source_group() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Candidates must have the right to work in the UK.",
                RequirementImportance.ESSENTIAL,
                MatchType.INCOMPATIBLE,
                0.0,
                category=RequirementCategory.WORK_AUTHORIZATION,
                source_text="Candidates must have the right to work in the UK and relevant skills.",
            ),
            make_match(
                1,
                "Relevant skills",
                RequirementImportance.ESSENTIAL,
                MatchType.DEMONSTRATED,
                0.9,
                source_text="Candidates must have the right to work in the UK and relevant skills.",
            ),
            make_match(
                2,
                "Additional core skill",
                RequirementImportance.ESSENTIAL,
                MatchType.DEMONSTRATED,
                0.9,
                source_text="Additional core skill is required.",
            ),
        ]
    )

    assert assessment.hard_blockers == [0]
    assert assessment.fit_score == 60.0


def test_same_looking_requirements_with_different_sources_do_not_merge() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Same-looking skill",
                RequirementImportance.ESSENTIAL,
                MatchType.DEMONSTRATED,
                0.9,
                source_text="Employer source S1.",
            ),
            make_match(
                1,
                "Same-looking skill",
                RequirementImportance.ESSENTIAL,
                MatchType.INFERRED,
                0.3,
                source_text="Employer source S2.",
            ),
            make_match(
                2,
                "Another skill",
                RequirementImportance.ESSENTIAL,
                MatchType.INFERRED,
                0.3,
                source_text="Employer source S3.",
            ),
        ]
    )

    assert assessment.fit_score == 50.0


def test_missing_or_ambiguous_provenance_falls_back_without_merging() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Skill A",
                RequirementImportance.ESSENTIAL,
                MatchType.DEMONSTRATED,
                0.9,
            ),
            make_match(
                1,
                "Skill B",
                RequirementImportance.ESSENTIAL,
                MatchType.INFERRED,
                0.3,
            ),
            make_match(
                2,
                "Another skill",
                RequirementImportance.ESSENTIAL,
                MatchType.INFERRED,
                0.3,
                source_text="Source A. / Source B.",
            ),
        ]
    )

    assert assessment.fit_score == 50.0


def test_conflicting_importance_in_one_source_falls_back_without_inventing_label() -> None:
    assessment = FitAssessmentService().assess(
        [
            make_match(
                0,
                "Criterion A",
                RequirementImportance.ESSENTIAL,
                MatchType.DEMONSTRATED,
                0.9,
                source_text="Shared employer statement.",
            ),
            make_match(
                1,
                "Criterion B",
                RequirementImportance.DESIRABLE,
                MatchType.INFERRED,
                0.3,
                source_text="Shared employer statement.",
            ),
            make_match(
                2,
                "Criterion C",
                RequirementImportance.ESSENTIAL,
                MatchType.INFERRED,
                0.3,
                source_text="Separate employer statement.",
            ),
        ]
    )

    assert assessment.fit_score == 55.7
    assert assessment.essential_score == 60.0
    assert assessment.desirable_score == 30.0

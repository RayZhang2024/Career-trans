from app.schemas.assessment import FitAssessment, Gap, GapSeverity, GapType
from app.schemas.candidate import CandidateContext
from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)
from app.services.career_alignment_compaction import career_alignment_input


def test_career_alignment_input_preserves_strategy_and_safe_job_signals_only() -> None:
    requirement = JobRequirement(
        text="Private detailed technical requirement",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.TECHNICAL,
        source_text="Private source wording that is not strategic context.",
    )
    profile = JobProfile(
        title="Applied Systems Engineer",
        company="Example",
        location="London",
        work_arrangement="Hybrid",
        seniority="Senior",
        salary="Competitive",
        employment_type="Permanent",
        responsibilities=[
            "Build reliable applied systems for customers.",
            "Build reliable applied systems for customers.",
        ],
        requirements=[requirement],
        technical_skills=["Private technology inventory"],
        domain_knowledge=["Applied AI"],
        security_requirements=["Private security constraint"],
        work_authorization_requirements=["Private eligibility constraint"],
    )
    candidate = CandidateContext(
        profile_text="Technical delivery background.",
        career_strategy_text="Move into applied AI systems delivery and technical leadership.",
        job_search_criteria_text="Prefer permanent hybrid roles in London.",
    )
    fit = FitAssessment(
        fit_score=74.0,
        essential_score=70.0,
        desirable_score=86.0,
        strengths=[0, 1],
        gaps=[
            Gap(
                requirement_index=0,
                requirement=requirement,
                gap_type=GapType.MEANINGFUL_CAPABILITY_GAP,
                severity=GapSeverity.MEDIUM,
                reason="Private detailed fit explanation.",
            )
        ],
        hard_blockers=[],
    )

    payload = career_alignment_input(candidate, profile, fit).model_dump(mode="json")

    assert payload["candidate_context"]["career_strategy_text"] == candidate.career_strategy_text
    assert payload["candidate_context"]["job_search_criteria_text"] == candidate.job_search_criteria_text
    assert payload["job_profile"] == {
        "title": "Applied Systems Engineer",
        "company": "Example",
        "location": "London",
        "work_arrangement": "Hybrid",
        "seniority": "Senior",
        "salary": "Competitive",
        "employment_type": "Permanent",
        "application_deadline": None,
        "responsibilities": ["Build reliable applied systems for customers."],
        "domain_knowledge": ["Applied AI"],
    }
    assert payload["fit_assessment"] == {
        "fit_score": 74.0,
        "essential_score": 70.0,
        "desirable_score": 86.0,
        "strength_count": 2,
        "gap_count": 1,
        "hard_blocker_count": 0,
    }
    rendered = str(payload)
    assert "Private source wording" not in rendered
    assert "Private technology inventory" not in rendered
    assert "Private detailed fit explanation" not in rendered

from app.schemas.candidate import CandidateContext, CareerEvidence
from app.schemas.candidate_adviser import CandidateIntakeProfileData
from app.services.candidate_adviser_projection import (
    candidate_adviser_source_context,
    compact_candidate_intake,
)


def test_candidate_adviser_source_context_is_bounded_and_keeps_early_and_recent_evidence() -> None:
    evidence = [
        CareerEvidence(
            evidence_id=f"E-{index:02d}",
            title=f"Evidence {index}",
            text=("technical delivery " * 200),
            skills=[f"Skill {item}" for item in range(30)],
        )
        for index in range(60)
    ]
    context = CandidateContext(
        profile_text="profile " * 2_000,
        skills_text=", ".join(f"Skill {index}" for index in range(100)),
        career_strategy_text="strategy " * 1_000,
        job_search_criteria_text="criteria " * 1_000,
        evidence=evidence,
    )

    compact = candidate_adviser_source_context(context)

    assert len(compact.profile_summary) <= 6_000
    assert len(compact.career_strategy_text) <= 3_000
    assert len(compact.job_search_criteria_text) <= 3_000
    assert len(compact.skills) <= 80
    assert len(compact.evidence) == 40
    assert compact.evidence[0].evidence_id == "E-00"
    assert compact.evidence[19].evidence_id == "E-19"
    assert compact.evidence[20].evidence_id == "E-40"
    assert compact.evidence[-1].evidence_id == "E-59"
    assert all(len(item.text) <= 1_000 for item in compact.evidence)
    assert all(len(item.skills) <= 20 for item in compact.evidence)


def test_candidate_adviser_intake_compaction_deduplicates_and_bounds_lists_and_text() -> None:
    intake = CandidateIntakeProfileData.model_validate(
        {
            "career_direction": {
                "short_term_goal": "goal " * 1_000,
                "target_role_families": [
                    "",
                    "AI Solutions Engineer",
                    "AI Solutions Engineer",
                    *[f"Role {index}" for index in range(30)],
                ],
            }
        }
    )

    compact = compact_candidate_intake(intake)

    assert compact.career_direction.short_term_goal is not None
    assert len(compact.career_direction.short_term_goal) <= 1_200
    assert len(compact.career_direction.target_role_families) <= 20
    assert compact.career_direction.target_role_families[0] == "AI Solutions Engineer"
    assert compact.career_direction.target_role_families.count("AI Solutions Engineer") == 1

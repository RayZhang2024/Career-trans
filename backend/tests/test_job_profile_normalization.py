from pathlib import Path

from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)
from app.schemas.matching import MatchType, RequirementMatch
from app.services.fit_assessment_service import FitAssessmentService
from app.services.job_profile_normalization import normalize_job_profile


_SOFTWARE_PRACTICES_SOURCE = (
    "About you: Familiarity with modern software engineering practices "
    "(version control, testing, CI/CD)"
)
_AI_TOOLING_SOURCE = (
    "About you: Experience implementing RAG architectures, vector databases, "
    "and semantic search"
)


def _requirement(
    text: str,
    source_text: str,
) -> JobRequirement:
    return JobRequirement(
        text=text,
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.TECHNICAL,
        source_text=source_text,
    )


def test_brown_fixture_is_fixed_and_contains_the_observed_grouped_criterion() -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "brown_and_brown_ai_engineer.txt"
    ).read_text(encoding="utf-8").strip()

    assert "AI Engineer" in fixture
    assert "version control, testing, CI/CD" in fixture
    assert "Experience implementing RAG architectures" in fixture


def test_grouped_and_atomic_variants_normalize_to_same_canonical_requirements() -> None:
    grouped = JobProfile(
        requirements=[
            _requirement(
                "Familiarity with modern software engineering practices "
                "(version control, testing, CI/CD)",
                _SOFTWARE_PRACTICES_SOURCE,
            ),
            _requirement(
                "Experience implementing RAG architectures, vector databases, "
                "and semantic search",
                _AI_TOOLING_SOURCE,
            ),
        ]
    )
    atomic = JobProfile(
        requirements=[
            _requirement("semantic search", _AI_TOOLING_SOURCE),
            _requirement("Familiarity with version control", _SOFTWARE_PRACTICES_SOURCE),
            _requirement("vector databases", _AI_TOOLING_SOURCE),
            _requirement("testing", _SOFTWARE_PRACTICES_SOURCE),
            _requirement("CI/CD", _SOFTWARE_PRACTICES_SOURCE),
            _requirement("RAG architectures", _AI_TOOLING_SOURCE),
        ]
    )

    normalized_grouped = normalize_job_profile(grouped)
    normalized_atomic = normalize_job_profile(atomic)

    assert normalized_grouped.requirements == normalized_atomic.requirements
    assert [item.text for item in normalized_grouped.requirements] == [
        "Experience implementing RAG architectures",
        "Experience implementing semantic search",
        "Experience implementing vector databases",
        "Familiarity with CI/CD",
        "Familiarity with testing",
        "Familiarity with version control",
    ]
    assert all(item.source_text for item in normalized_grouped.requirements)


def test_formatting_order_and_duplicate_variants_are_stable_without_promoting_responsibilities() -> None:
    first = JobProfile(
        responsibilities=["  Build reliable systems. ", "Build reliable systems."],
        requirements=[
            _requirement(" Python   experience ", "About you: Python experience."),
            _requirement("Python experience", "About you: Python experience."),
        ],
    )
    second = JobProfile(
        responsibilities=["Build reliable systems."],
        requirements=[
            _requirement("Python experience", "About you: Python experience."),
        ],
    )

    normalized_first = normalize_job_profile(first)
    normalized_second = normalize_job_profile(second)

    assert normalized_first == normalized_second
    assert normalized_first.responsibilities == ["Build reliable systems."]
    assert normalized_first.requirements[0].importance == RequirementImportance.ESSENTIAL


def test_normalization_keeps_alternatives_and_inseparable_concepts_together() -> None:
    profile = normalize_job_profile(
        JobProfile(
            requirements=[
                _requirement(
                    "Experience with structured and unstructured data",
                    "About you: Experience working with structured and unstructured data.",
                ),
                _requirement(
                    "Hands-on experience with ChatGPT / OpenAI APIs and/or Anthropic Claude",
                    "About you: Hands-on experience with ChatGPT / OpenAI APIs and/or Anthropic Claude.",
                ),
            ]
        )
    )

    assert [item.text for item in profile.requirements] == [
        "Experience with structured and unstructured data",
        "Hands-on experience with ChatGPT / OpenAI APIs and/or Anthropic Claude",
    ]


def test_fit_score_is_unchanged_when_only_grouping_differs() -> None:
    grouped = normalize_job_profile(
        JobProfile(
            requirements=[
                _requirement(
                    "Familiarity with modern software engineering practices "
                    "(version control, testing, CI/CD)",
                    _SOFTWARE_PRACTICES_SOURCE,
                )
            ]
        )
    )
    atomic = normalize_job_profile(
        JobProfile(
            requirements=[
                _requirement("testing", _SOFTWARE_PRACTICES_SOURCE),
                _requirement("CI/CD", _SOFTWARE_PRACTICES_SOURCE),
                _requirement("version control", _SOFTWARE_PRACTICES_SOURCE),
            ]
        )
    )
    scores = {
        "Familiarity with version control": 0.8,
        "Familiarity with testing": 0.6,
        "Familiarity with CI/CD": 0.4,
    }

    def assess(profile: JobProfile):
        matches = [
            RequirementMatch(
                requirement_index=index,
                requirement=requirement,
                match_type=MatchType.DEMONSTRATED,
                score=scores[requirement.text],
                evidence_ids=[],
                reasoning="Synthetic test match.",
            )
            for index, requirement in enumerate(profile.requirements)
        ]
        return FitAssessmentService().assess(matches)

    assert assess(grouped).fit_score == assess(atomic).fit_score

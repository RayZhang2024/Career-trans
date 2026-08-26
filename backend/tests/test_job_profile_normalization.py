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
_BROWN_GROUPED_REQUIREMENTS = [
    (
        "Experience integrating AI services via APIs and building backend services",
        "Experience integrating AI services via APIs and building backend services.",
    ),
    (
        "Familiarity with modern software engineering practices "
        "(version control, testing, CI/CD)",
        _SOFTWARE_PRACTICES_SOURCE,
    ),
    (
        "Practical experience with prompt engineering, system prompts, "
        "and evaluation techniques",
        "Practical experience with prompt engineering, system prompts, "
        "and evaluation techniques.",
    ),
    (
        "Experience implementing RAG architectures, vector databases, "
        "and semantic search",
        _AI_TOOLING_SOURCE,
    ),
    (
        "Understanding of cloud security, IAM, and cost management",
        "Understanding of cloud security, IAM, and cost management.",
    ),
    (
        "Familiarity with vector stores, embeddings, and document processing pipelines",
        "Familiarity with vector stores, embeddings, and document processing pipelines.",
    ),
]
_BROWN_ATOMIC_REQUIREMENTS = [
    "Experience integrating AI services via APIs",
    "Experience building backend services",
    "Familiarity with version control",
    "Familiarity with testing",
    "Familiarity with CI/CD",
    "Practical experience with prompt engineering",
    "Practical experience with system prompts",
    "Practical experience with evaluation techniques",
    "Experience implementing RAG architectures",
    "Experience implementing vector databases",
    "Experience implementing semantic search",
    "Understanding of cloud security",
    "Understanding of IAM",
    "Understanding of cost management",
    "Familiarity with vector stores",
    "Familiarity with embeddings",
    "Familiarity with document processing pipelines",
]


def _requirement(
    text: str,
    source_text: str,
    *,
    importance: RequirementImportance = RequirementImportance.ESSENTIAL,
    category: RequirementCategory = RequirementCategory.TECHNICAL,
) -> JobRequirement:
    return JobRequirement(
        text=text,
        importance=importance,
        category=category,
        source_text=source_text,
    )


def _brown_source_for(atomic_text: str) -> str:
    """Return the advert sentence supporting a 37-like atomic variant."""
    prefixes = (
        "experience implementing ",
        "experience ",
        "familiarity with ",
        "practical experience with ",
        "understanding of ",
    )
    normalized = atomic_text.casefold()
    for grouped_text, source in _BROWN_GROUPED_REQUIREMENTS:
        grouped = grouped_text.casefold()
        if normalized in grouped:
            return source
        if any(
            normalized.startswith(prefix) and normalized.removeprefix(prefix) in grouped
            for prefix in prefixes
        ):
            return source
    raise AssertionError(f"No Brown source found for {atomic_text!r}")


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


def test_conflicting_duplicate_importance_labels_remain_source_grounded() -> None:
    profile = normalize_job_profile(
        JobProfile(
            requirements=[
                _requirement(
                    "Python experience",
                    "Required: production Python experience.",
                    importance=RequirementImportance.ESSENTIAL,
                ),
                _requirement(
                    "Python experience",
                    "Preferred: production Python experience.",
                    importance=RequirementImportance.DESIRABLE,
                ),
            ]
        )
    )

    assert [(item.importance, item.source_text) for item in profile.requirements] == [
        (RequirementImportance.DESIRABLE, "Preferred: production Python experience."),
        (RequirementImportance.ESSENTIAL, "Required: production Python experience."),
    ]


def test_conflicting_duplicate_categories_are_not_chosen_alphabetically() -> None:
    profile = normalize_job_profile(
        JobProfile(
            requirements=[
                _requirement(
                    "Right to work in the UK",
                    "Eligibility: right to work in the UK is required.",
                    category=RequirementCategory.WORK_AUTHORIZATION,
                ),
                _requirement(
                    "Right to work in the UK",
                    "Location: role must be performed from the UK.",
                    category=RequirementCategory.LOCATION,
                ),
            ]
        )
    )

    assert {(item.category, item.source_text) for item in profile.requirements} == {
        (
            RequirementCategory.WORK_AUTHORIZATION,
            "Eligibility: right to work in the UK is required.",
        ),
        (
            RequirementCategory.LOCATION,
            "Location: role must be performed from the UK.",
        ),
    }


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


def test_normalization_does_not_split_optional_or_example_lists() -> None:
    protected_requirements = [
        (
            "Strong experience with GCP, Vertex AI, and BigQuery",
            "Strong experience with GCP, ideally Vertex AI, and BigQuery.",
        ),
        (
            "Experience with cloud services such as Vertex AI, BigQuery, and Cloud Run",
            "Experience with cloud services such as Vertex AI, BigQuery, and Cloud Run.",
        ),
        (
            "Preferred experience with Python, SQL, and Docker",
            "Preferred experience with Python, SQL, and Docker.",
        ),
        (
            "Nice to have experience with LangChain, RAG, and vector stores",
            "Nice to have experience with LangChain, RAG, and vector stores.",
        ),
        (
            "Experience including observability, logging, and monitoring",
            "Experience including observability, logging, and monitoring.",
        ),
        (
            "Experience with cloud tooling, e.g. Terraform, Kubernetes, and Helm",
            "Experience with cloud tooling, e.g. Terraform, Kubernetes, and Helm.",
        ),
    ]
    profile = normalize_job_profile(
        JobProfile(
            requirements=[
                _requirement(text, source, importance=RequirementImportance.DESIRABLE)
                for text, source in protected_requirements
            ]
        )
    )

    assert {item.text for item in profile.requirements} == {
        text for text, _ in protected_requirements
    }
    assert len(profile.requirements) == len(protected_requirements)


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


def test_brown_material_grouping_variants_normalize_to_equivalent_fit() -> None:
    """Cover all material multi-capability criteria in the observed 24/37 variants."""
    grouped = normalize_job_profile(
        JobProfile(
            requirements=[
                _requirement(text, source)
                for text, source in _BROWN_GROUPED_REQUIREMENTS
            ]
        )
    )
    atomic = normalize_job_profile(
        JobProfile(
            requirements=[
                _requirement(
                    atomic_text,
                    _brown_source_for(atomic_text),
                )
                for atomic_text in _BROWN_ATOMIC_REQUIREMENTS
            ]
        )
    )

    assert grouped.requirements == atomic.requirements

    def assess(profile: JobProfile):
        matches = [
            RequirementMatch(
                requirement_index=index,
                requirement=requirement,
                match_type=MatchType.DEMONSTRATED,
                score=0.4 + index / 100,
                evidence_ids=[],
                reasoning="Synthetic test match.",
            )
            for index, requirement in enumerate(profile.requirements)
        ]
        return FitAssessmentService().assess(matches)

    assert assess(grouped).fit_score == assess(atomic).fit_score

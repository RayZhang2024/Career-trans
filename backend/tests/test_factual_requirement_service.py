from app.schemas.candidate import CandidateContext, CandidateEligibility
from app.schemas.job import (
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)
from app.schemas.matching import MatchType
from app.schemas.matching import EvidenceSourceType
from app.services.factual_requirement_service import FactualRequirementService


def test_uk_work_authorisation_is_matched_deterministically() -> None:
    context = CandidateContext(
        eligibility=CandidateEligibility(
            work_authorisation=["United Kingdom"]
        )
    )

    requirement = JobRequirement(
        text="Right to work in the UK",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.WORK_AUTHORIZATION,
    )

    result = FactualRequirementService().match(
        0,
        requirement,
        context,
    )

    assert result.match_type == MatchType.DEMONSTRATED
    assert result.score == 1.0
    assert len(result.evidence_refs) == 1
    assert result.evidence_refs[0].source_type == EvidenceSourceType.CANDIDATE_ELIGIBILITY
    assert result.evidence_refs[0].source_ref == "work_authorisation"
    assert result.evidence_refs[0].value == "United Kingdom"


def test_missing_work_authorisation_returns_missing() -> None:
    context = CandidateContext()

    requirement = JobRequirement(
        text="Right to work in the UK",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.WORK_AUTHORIZATION,
    )

    result = FactualRequirementService().match(
        0,
        requirement,
        context,
    )

    assert result.match_type == MatchType.MISSING
    assert result.score == 0.0
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

    assert result.match_type == MatchType.UNKNOWN
    assert result.score == 0.0


def test_location_eligibility_matches_an_allowed_country() -> None:
    context = CandidateContext(
        eligibility=CandidateEligibility(locations=["United Kingdom / London"])
    )
    requirement = JobRequirement(
        text="Must be located in the US, UK, or Canada.",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.LOCATION,
    )

    result = FactualRequirementService().match(0, requirement, context)

    assert result.match_type == MatchType.DEMONSTRATED
    assert result.score == 1.0
    assert result.evidence_refs[0].source_ref == "locations"
    assert result.evidence_refs[0].value == "United Kingdom"


def test_known_incompatible_location_is_distinct_from_unknown_eligibility() -> None:
    requirement = JobRequirement(
        text="Must be located in the US, UK, or Canada.",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.LOCATION,
    )

    incompatible = FactualRequirementService().match(
        0,
        requirement,
        CandidateContext(eligibility=CandidateEligibility(locations=["Germany"])),
    )
    unknown = FactualRequirementService().match(0, requirement, CandidateContext())

    assert incompatible.match_type == MatchType.INCOMPATIBLE
    assert unknown.match_type == MatchType.UNKNOWN


def test_country_matching_does_not_treat_must_as_the_us() -> None:
    requirement = JobRequirement(
        text="Must be located in the UK.",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.LOCATION,
    )

    result = FactualRequirementService().match(
        0,
        requirement,
        CandidateContext(eligibility=CandidateEligibility(locations=["United States"])),
    )

    assert result.match_type == MatchType.INCOMPATIBLE


def test_security_clearance_matching_is_deterministic_and_unknown_when_absent() -> None:
    requirement = JobRequirement(
        text="Active SC clearance is required.",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.SECURITY,
    )
    service = FactualRequirementService()

    demonstrated = service.match(
        0,
        requirement,
        CandidateContext(eligibility=CandidateEligibility(security_clearances=["SC"])),
    )
    incompatible = service.match(
        0,
        requirement,
        CandidateContext(eligibility=CandidateEligibility(security_clearances=["DV clearance"])),
    )
    unknown = service.match(0, requirement, CandidateContext())

    assert demonstrated.match_type is MatchType.DEMONSTRATED
    assert demonstrated.evidence_refs[0].source_ref == "security_clearances"
    assert incompatible.match_type is MatchType.INCOMPATIBLE
    assert unknown.match_type is MatchType.UNKNOWN


def test_unrecognised_named_security_clearance_remains_unknown() -> None:
    requirement = JobRequirement(
        text="NATO security clearance is required.",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.SECURITY,
    )

    result = FactualRequirementService().match(
        0,
        requirement,
        CandidateContext(eligibility=CandidateEligibility(security_clearances=["SC"])),
    )

    assert result.match_type is MatchType.UNKNOWN
    assert result.score == 0.0

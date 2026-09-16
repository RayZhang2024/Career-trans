import pytest

from app.schemas.candidate import CareerEvidence
from app.schemas.job import JobProfile, JobRequirement, RequirementCategory
from app.schemas.matching import RequirementEvidencePlan, RequirementEvidenceScope
from app.services.candidate_profile_compaction import requirement_evidence_plan


def _evidence(evidence_id: str, text: str, *, evidence_type: str = "other") -> CareerEvidence:
    return CareerEvidence(
        evidence_id=evidence_id,
        title=text,
        text=text,
        skills=text.split(),
        evidence_type=evidence_type,
    )


def test_plan_round_robins_scopes_and_exposes_only_provider_union_ids() -> None:
    plan = requirement_evidence_plan(
        [
            _evidence("DATA-1", "Python data engineering"),
            _evidence("DATA-2", "Python data engineering"),
            _evidence("SECURITY-1", "Security architecture"),
        ],
        JobProfile(requirements=[
            JobRequirement(text="Python data engineering"),
            JobRequirement(text="Security architecture"),
        ]),
        limit=2,
    )

    assert [item.evidence_id for item in plan.provider_evidence] == ["DATA-1", "SECURITY-1"]
    assert [scope.allowed_evidence_ids for scope in plan.scopes] == [
        ("DATA-1",), ("SECURITY-1",)
    ]


def test_plan_is_invariant_to_evidence_input_permutation() -> None:
    evidence = [
        _evidence("B", "Python systems"),
        _evidence("A", "Python systems"),
        _evidence("C", "Security architecture"),
    ]
    profile = JobProfile(requirements=[
        JobRequirement(text="Python systems"),
        JobRequirement(text="Security architecture"),
    ])

    first = requirement_evidence_plan(evidence, profile)
    second = requirement_evidence_plan(list(reversed(evidence)), profile)

    assert first == second


def test_stronger_lexical_relevance_beats_category_type_preference() -> None:
    plan = requirement_evidence_plan(
        [
            _evidence("EDU", "Python", evidence_type="education"),
            _evidence("PROJECT", "Machine learning Python degree", evidence_type="project"),
        ],
        JobProfile(requirements=[
            JobRequirement(text="Machine learning Python degree", category=RequirementCategory.EDUCATION)
        ]),
    )

    assert plan.scopes[0].allowed_evidence_ids[0] == "PROJECT"


def test_education_preference_and_unknown_type_remain_eligible_on_equal_overlap() -> None:
    plan = requirement_evidence_plan(
        [
            _evidence("UNKNOWN", "Computer science", evidence_type="free_form"),
            _evidence("EDU", "Computer science", evidence_type="education"),
        ],
        JobProfile(requirements=[
            JobRequirement(text="Computer science", category=RequirementCategory.EDUCATION)
        ]),
    )

    assert plan.scopes[0].allowed_evidence_ids == ("EDU", "UNKNOWN")


def test_shared_evidence_is_sent_once_and_permitted_by_each_matching_scope() -> None:
    plan = requirement_evidence_plan(
        [_evidence("SHARED", "Python customer delivery")],
        JobProfile(requirements=[
            JobRequirement(text="Python delivery"),
            JobRequirement(text="Customer delivery"),
        ]),
    )

    assert [item.evidence_id for item in plan.provider_evidence] == ["SHARED"]
    assert [scope.allowed_evidence_ids for scope in plan.scopes] == [("SHARED",), ("SHARED",)]


def test_no_lexical_overlap_creates_an_explicit_empty_scope() -> None:
    plan = requirement_evidence_plan(
        [_evidence("PYTHON", "Python engineering")],
        JobProfile(requirements=[JobRequirement(text="Clinical research")]),
    )

    assert plan.provider_evidence == ()
    assert plan.scopes == (RequirementEvidenceScope(requirement_index=0),)


def test_repeated_identical_candidate_evidence_id_is_deduplicated_in_scope_and_union() -> None:
    repeated = _evidence("PYTHON", "Python delivery")
    plan = requirement_evidence_plan(
        [repeated, repeated.model_copy(deep=True)],
        JobProfile(requirements=[JobRequirement(text="Python delivery")]),
    )

    assert [item.evidence_id for item in plan.provider_evidence] == ["PYTHON"]
    assert plan.scopes[0].allowed_evidence_ids == ("PYTHON",)


def test_conflicting_duplicate_candidate_evidence_id_fails_before_plan_creation() -> None:
    with pytest.raises(ValueError, match="conflicting content"):
        requirement_evidence_plan(
            [_evidence("SAME", "Python delivery"), _evidence("SAME", "Security architecture")],
            JobProfile(requirements=[JobRequirement(text="Python delivery")]),
        )


@pytest.mark.parametrize(
    "plan",
    [
        RequirementEvidencePlan(scopes=(RequirementEvidenceScope(requirement_index=1),)),
        RequirementEvidencePlan(
            provider_evidence=(
                _evidence("A", "Python"), _evidence("A", "Python duplicate"),
            ),
            scopes=(RequirementEvidenceScope(requirement_index=0, allowed_evidence_ids=("A",)),),
        ),
        RequirementEvidencePlan(
            provider_evidence=(_evidence("A", "Python"),),
            scopes=(RequirementEvidenceScope(requirement_index=0, allowed_evidence_ids=("B",)),),
        ),
    ],
)
def test_malformed_plan_fails_closed_before_provider_invocation(plan: RequirementEvidencePlan) -> None:
    with pytest.raises(ValueError):
        plan.validate_for(requirement_count=1, provider_limit=8, per_requirement_limit=3)


def test_plan_rejects_duplicate_ids_within_one_scope() -> None:
    plan = RequirementEvidencePlan(
        provider_evidence=(_evidence("A", "Python"),),
        scopes=(RequirementEvidenceScope(requirement_index=0, allowed_evidence_ids=("A", "A")),),
    )

    with pytest.raises(ValueError, match="scope contains duplicate IDs"):
        plan.validate_for(requirement_count=1, provider_limit=8, per_requirement_limit=3)


def test_plan_rejects_provider_union_exceeding_global_limit() -> None:
    plan = RequirementEvidencePlan(
        provider_evidence=tuple(_evidence(f"E-{index}", f"Python {index}") for index in range(9)),
        scopes=(RequirementEvidenceScope(requirement_index=0, allowed_evidence_ids=("E-0",)),),
    )

    with pytest.raises(ValueError, match="provider union exceeds"):
        plan.validate_for(requirement_count=1, provider_limit=8, per_requirement_limit=3)


def test_plan_rejects_scope_exceeding_per_requirement_limit() -> None:
    plan = RequirementEvidencePlan(
        provider_evidence=tuple(_evidence(f"E-{index}", f"Python {index}") for index in range(4)),
        scopes=(
            RequirementEvidenceScope(
                requirement_index=0,
                allowed_evidence_ids=("E-0", "E-1", "E-2", "E-3"),
            ),
        ),
    )

    with pytest.raises(ValueError, match="scope exceeds"):
        plan.validate_for(requirement_count=1, provider_limit=8, per_requirement_limit=3)

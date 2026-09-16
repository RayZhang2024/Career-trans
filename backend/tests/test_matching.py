from fastapi.testclient import TestClient

from app.api.deps import get_requirement_matching_service
from app.main import app as fastapi_app
from app.schemas.candidate import (
    CandidateContext,
    CandidateEligibility,
    CandidateMatchingProfile,
    CareerEvidence,
)
from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)
from app.schemas.matching import (
    EvidenceSourceType,
    MatchType,
    RequirementMatch,
    RequirementMatchSet,
    RequirementEvidencePlan,
)
from app.services.requirement_matching_service import RequirementMatchingService


PYTHON_REQUIREMENT = JobRequirement(
    text="Professional experience with Python",
    importance=RequirementImportance.ESSENTIAL,
    category=RequirementCategory.TECHNICAL,
)
REACT_REQUIREMENT = JobRequirement(
    text="Experience with React",
    importance=RequirementImportance.DESIRABLE,
    category=RequirementCategory.TECHNICAL,
)

JOB_PROFILE = JobProfile(
    title="AI Solutions Engineer",
    requirements=[PYTHON_REQUIREMENT, REACT_REQUIREMENT],
)

CANDIDATE_CONTEXT = CandidateContext(
    source_name="demo",
    profile_text="Experienced technical professional.",
    evidence=[
        CareerEvidence(
            evidence_id="EVIDENCE-PY-001",
            title="Python application development",
            text="Built Python applications and automated technical workflows.",
            skills=["Python"],
        )
    ],
)


class FakeRequirementMatcher:
    def match(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateMatchingProfile,
        **kwargs: object,
    ) -> RequirementMatchSet:
        assert job_profile == JOB_PROFILE
        assert [item.model_dump() for item in candidate_context.evidence] == [
            {
                "evidence_id": item.evidence_id,
                "evidence_type": item.evidence_type,
                "title": item.title,
                "text": item.text,
                "skills": item.skills,
            }
            for item in CANDIDATE_CONTEXT.evidence
        ]
        return RequirementMatchSet(
            matches=[
                RequirementMatch(
                    requirement_index=0,
                    requirement=PYTHON_REQUIREMENT,
                    match_type=MatchType.DEMONSTRATED,
                    score=0.92,
                    evidence_ids=["EVIDENCE-PY-001"],
                    reasoning="Direct Python application-development evidence is supplied.",
                ),
                RequirementMatch(
                    requirement_index=1,
                    requirement=REACT_REQUIREMENT,
                    match_type=MatchType.MISSING,
                    score=0.05,
                    evidence_ids=[],
                    reasoning="No React evidence is supplied in the candidate context.",
                ),
            ]
        )


def test_match_job_returns_requirement_level_matches(client: TestClient) -> None:
    fake_service = RequirementMatchingService(matcher=FakeRequirementMatcher())
    fastapi_app.dependency_overrides[get_requirement_matching_service] = (
        lambda: fake_service
    )

    try:
        response = client.post(
            "/api/v1/jobs/match",
            json={
                "job_profile": JOB_PROFILE.model_dump(mode="json"),
                "candidate_context": CANDIDATE_CONTEXT.model_dump(mode="json"),
            },
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_requirement_matching_service, None)

    assert response.status_code == 200
    matches = response.json()["matches"]

    assert len(matches) == 2
    assert matches[0]["match_type"] == "demonstrated"
    assert matches[0]["evidence_ids"] == ["EVIDENCE-PY-001"]
    assert matches[0]["evidence_refs"] == [
        {
            "source_type": "career_evidence",
            "source_ref": "EVIDENCE-PY-001",
            "value": None,
        }
    ]
    assert matches[1]["match_type"] == "missing"
    assert matches[1]["evidence_ids"] == []
    assert matches[1]["evidence_refs"] == []


def test_factual_requirement_bypasses_semantic_matcher() -> None:
    work_auth_requirement = JobRequirement(
        text="Right to work in the UK",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.WORK_AUTHORIZATION,
    )

    job_profile = JobProfile(
        requirements=[
            PYTHON_REQUIREMENT,
            work_auth_requirement,
        ]
    )

    candidate_context = CandidateContext(
        source_name="demo",
        eligibility=CandidateEligibility(
            work_authorisation=["United Kingdom"]
        ),
        evidence=[
            CareerEvidence(
                evidence_id="EVIDENCE-PY-001",
                title="Python application development",
                text="Built Python applications and automated technical workflows.",
                skills=["Python"],
            )
        ],
    )

    class FakeSemanticMatcher:
        def match(
            self,
            semantic_job_profile: JobProfile,
            supplied_candidate_context: CandidateMatchingProfile,
            **kwargs: object,
        ) -> RequirementMatchSet:
            assert len(semantic_job_profile.requirements) == 1
            assert semantic_job_profile.requirements[0] == PYTHON_REQUIREMENT
            assert [item.evidence_id for item in supplied_candidate_context.evidence] == [
                item.evidence_id for item in candidate_context.evidence
            ]

            return RequirementMatchSet(
                matches=[
                    RequirementMatch(
                        requirement_index=0,
                        requirement=PYTHON_REQUIREMENT,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.8,
                        evidence_ids=["EVIDENCE-PY-001"],
                        reasoning="Semantic Python match.",
                    )
                ]
            )

    service = RequirementMatchingService(
        matcher=FakeSemanticMatcher()
    )

    result = service.match(
        job_profile,
        candidate_context,
    )

    assert len(result.matches) == 2

    semantic_match = result.matches[0]
    assert semantic_match.requirement == PYTHON_REQUIREMENT
    assert semantic_match.score == 0.8
    assert len(semantic_match.evidence_refs) == 1
    assert (
        semantic_match.evidence_refs[0].source_type
        == EvidenceSourceType.CAREER_EVIDENCE
    )
    assert semantic_match.evidence_refs[0].source_ref == "EVIDENCE-PY-001"

    factual_match = result.matches[1]
    assert factual_match.requirement == work_auth_requirement
    assert factual_match.match_type == MatchType.DEMONSTRATED
    assert factual_match.score == 1.0
    assert len(factual_match.evidence_refs) == 1
    assert (
        factual_match.evidence_refs[0].source_type
        == EvidenceSourceType.CANDIDATE_ELIGIBILITY
    )
    assert factual_match.evidence_refs[0].source_ref == "work_authorisation"
    assert factual_match.evidence_refs[0].value == "United Kingdom"


def test_semantic_subset_indexes_remap_to_original_indexes_independent_of_output_order() -> None:
    work_auth_requirement = JobRequirement(
        text="Right to work in the UK",
        importance=RequirementImportance.ESSENTIAL,
        category=RequirementCategory.WORK_AUTHORIZATION,
    )
    job_profile = JobProfile(
        requirements=[work_auth_requirement, REACT_REQUIREMENT, PYTHON_REQUIREMENT]
    )
    candidate_context = CandidateContext(
        eligibility=CandidateEligibility(work_authorisation=["United Kingdom"]),
        evidence=[
            CareerEvidence(
                evidence_id="EVIDENCE-PY-001",
                title="Python application development",
                text="Built Python applications.",
                skills=["Python"],
            )
        ],
    )

    class FakeSemanticMatcher:
        def match(
            self,
            semantic_job_profile: JobProfile,
            _: CandidateMatchingProfile,
            **kwargs: object,
        ) -> RequirementMatchSet:
            assert semantic_job_profile.requirements == [REACT_REQUIREMENT, PYTHON_REQUIREMENT]
            return RequirementMatchSet(
                matches=[
                    RequirementMatch(
                        requirement_index=1,
                        requirement=PYTHON_REQUIREMENT,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["EVIDENCE-PY-001"],
                        reasoning="Python evidence.",
                    ),
                    RequirementMatch(
                        requirement_index=0,
                        requirement=REACT_REQUIREMENT,
                        match_type=MatchType.MISSING,
                        score=0.0,
                        reasoning="No React evidence.",
                    ),
                ]
            )

    result = RequirementMatchingService(matcher=FakeSemanticMatcher()).match(
        job_profile,
        candidate_context,
    )

    assert [match.requirement_index for match in result.matches] == [0, 1, 2]
    assert [match.requirement for match in result.matches] == job_profile.requirements


def test_semantic_matching_receives_deterministic_top_evidence_only() -> None:
    evidence = [
        CareerEvidence(
            evidence_id=f"EVIDENCE-{index}",
            title=f"Evidence {index}",
            text=("Python deployment work." if index in {2, 7} else "Unrelated work."),
            skills=["Python"] if index in {2, 7} else [],
        )
        for index in range(10)
    ]
    candidate = CandidateContext(
        profile_text="Long profile that should not be sent in full.",
        evidence=evidence,
    )
    received: list[CandidateMatchingProfile] = []

    class FakeSemanticMatcher:
        def match(
            self,
            _: JobProfile,
            matching_profile: CandidateMatchingProfile,
            **kwargs: object,
        ) -> RequirementMatchSet:
            received.append(matching_profile)
            return RequirementMatchSet(
                matches=[
                    RequirementMatch(
                        requirement_index=0,
                        requirement=PYTHON_REQUIREMENT,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["EVIDENCE-2"],
                        reasoning="Direct Python evidence.",
                    )
                ]
            )

    RequirementMatchingService(matcher=FakeSemanticMatcher()).match(
        JobProfile(requirements=[PYTHON_REQUIREMENT]),
        candidate,
    )

    assert [item.evidence_id for item in received[0].evidence] == [
        "EVIDENCE-2",
        "EVIDENCE-7",
    ]


def test_service_constructs_one_plan_and_passes_its_exact_union_to_matcher() -> None:
    received_plans: list[RequirementEvidencePlan] = []
    received_profiles: list[CandidateMatchingProfile] = []

    class FakeSemanticMatcher:
        def match(
            self,
            _: JobProfile,
            matching_profile: CandidateMatchingProfile,
            *,
            evidence_plan: RequirementEvidencePlan,
        ) -> RequirementMatchSet:
            received_plans.append(evidence_plan)
            received_profiles.append(matching_profile)
            return RequirementMatchSet(
                matches=[
                    RequirementMatch(
                        requirement_index=0,
                        requirement=PYTHON_REQUIREMENT,
                        match_type=MatchType.MISSING,
                        score=0.0,
                        evidence_ids=[],
                        reasoning="No matching evidence.",
                    )
                ]
            )

    RequirementMatchingService(matcher=FakeSemanticMatcher()).match(
        JobProfile(requirements=[PYTHON_REQUIREMENT]),
        CandidateContext(evidence=[
            CareerEvidence(evidence_id="PY-1", title="Python", text="Python delivery", skills=["Python"]),
            CareerEvidence(evidence_id="OTHER", title="Other", text="Unrelated work"),
        ]),
    )

    assert len(received_plans) == 1
    assert [item.evidence_id for item in received_profiles[0].evidence] == [
        item.evidence_id for item in received_plans[0].provider_evidence
    ]


def test_requirement_aware_retrieval_keeps_evidence_for_distinct_topics() -> None:
    data_evidence = [
        CareerEvidence(
            evidence_id=f"DATA-{index}",
            title="Data platform delivery",
            text="Built Python data pipelines and Python analytics workflows.",
            skills=["Python", "data"],
        )
        for index in range(9)
    ]
    security_evidence = CareerEvidence(
        evidence_id="SECURITY-1",
        title="Security clearance delivery",
        text="Delivered security architecture and threat-model reviews.",
        skills=["security"],
    )
    data_requirement = JobRequirement(
        text="Professional Python data platform experience",
        category=RequirementCategory.TECHNICAL,
    )
    security_requirement = JobRequirement(
        text="Security architecture and threat modelling experience",
        category=RequirementCategory.SECURITY,
    )
    received: list[CandidateMatchingProfile] = []

    class FakeSemanticMatcher:
        def match(
            self,
            _: JobProfile,
            matching_profile: CandidateMatchingProfile,
            **kwargs: object,
        ) -> RequirementMatchSet:
            received.append(matching_profile)
            return RequirementMatchSet(
                matches=[
                    RequirementMatch(
                        requirement_index=0,
                        requirement=data_requirement,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["DATA-0"],
                        reasoning="Python data evidence.",
                    ),
                    RequirementMatch(
                        requirement_index=1,
                        requirement=security_requirement,
                        match_type=MatchType.DEMONSTRATED,
                        score=0.9,
                        evidence_ids=["SECURITY-1"],
                        reasoning="Security evidence.",
                    ),
                ]
            )

    RequirementMatchingService(matcher=FakeSemanticMatcher()).match(
        JobProfile(
            title="Data platform role",
            requirements=[data_requirement, security_requirement],
        ),
        CandidateContext(evidence=[*data_evidence, security_evidence]),
    )

    retrieved_ids = [item.evidence_id for item in received[0].evidence]
    assert retrieved_ids[:2] == ["DATA-0", "SECURITY-1"]
    assert "SECURITY-1" in retrieved_ids
    assert len(retrieved_ids) <= 8


def test_requirement_aware_retrieval_round_robins_with_a_bounded_budget() -> None:
    requirements = [
        JobRequirement(text="Python data engineering"),
        JobRequirement(text="Security architecture"),
        JobRequirement(text="Cloud infrastructure"),
        JobRequirement(text="Customer discovery"),
    ]
    topics = ["Python data", "Security architecture", "Cloud infrastructure", "Customer discovery"]
    evidence = [
        CareerEvidence(
            evidence_id=f"{topic.split()[0].upper()}-{rank}",
            title=topic,
            text=f"Delivered {topic} work.",
            skills=topic.split(),
        )
        for topic in topics
        for rank in range(3)
    ]

    from app.services.candidate_profile_compaction import top_evidence

    retrieved_ids = [
        item.evidence_id
        for item in top_evidence(
            evidence,
            JobProfile(requirements=requirements),
        )
    ]

    assert len(retrieved_ids) == 8
    assert retrieved_ids[:4] == ["PYTHON-0", "SECURITY-0", "CLOUD-0", "CUSTOMER-0"]
    assert "CUSTOMER-0" in retrieved_ids

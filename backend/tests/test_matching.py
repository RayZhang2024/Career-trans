from fastapi.testclient import TestClient

from app.api.deps import get_requirement_matching_service
from app.main import app as fastapi_app
from app.schemas.candidate import CandidateContext, CandidateEligibility, CareerEvidence
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
        candidate_context: CandidateContext,
    ) -> RequirementMatchSet:
        assert job_profile == JOB_PROFILE
        assert candidate_context == CANDIDATE_CONTEXT
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
            supplied_candidate_context: CandidateContext,
        ) -> RequirementMatchSet:
            assert len(semantic_job_profile.requirements) == 1
            assert semantic_job_profile.requirements[0] == PYTHON_REQUIREMENT
            assert supplied_candidate_context == candidate_context

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

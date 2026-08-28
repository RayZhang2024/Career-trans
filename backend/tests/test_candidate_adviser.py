import json

import pytest
from sqlalchemy import select

from app.models.user import User
from app.providers.llm import SemanticOutputError
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessment,
    CandidateAdviserState,
    CandidateIntakeProfileData,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.job import JobProfile, JobRequirement
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_profile_compaction import (
    candidate_career_profile,
    candidate_matching_profile,
    candidate_search_profile,
)
from app.services.cv_ingestion_service import CVIngestionService, PersistedCandidateContextLoader


def _auth(client, email: str) -> tuple[dict[str, str], str]:
    credentials = {"email": email, "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, email


def _confirm_cv(db_session, email: str) -> str:
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    data = CandidateCVData.model_validate(
        {
            "employment": [
                {
                    "employer": "Example",
                    "title": "Technical Lead",
                    "description": "Led customer-facing technical delivery.",
                }
            ],
            "skills": [{"name": "Python"}],
            "evidence": [
                {
                    "evidence_type": "employment",
                    "title": "Customer delivery",
                    "text": "Translated customer requirements into technical delivery.",
                    "skills": ["Requirements", "Customer delivery"],
                }
            ],
        }
    )
    service = CVIngestionService(db_session)
    draft = service.upload(
        user_id,
        [("cv.json", "application/json", json.dumps(data.model_dump(mode="json")).encode())],
    )
    service.interpret(user_id, draft.id)
    service.confirm(user_id, draft.id)
    return user_id


def _intake() -> CandidateIntakeProfileData:
    return CandidateIntakeProfileData.model_validate(
        {
            "career_direction": {
                "short_term_goal": "Move into applied AI solution delivery.",
                "target_role_families": ["AI Solutions Engineer"],
                "desired_capabilities": ["Production AI", "Cloud deployment"],
            },
            "work_preferences": {
                "preferred_work": ["Customer-facing technical problem solving"],
                "preferred_work_arrangements": ["Hybrid"],
                "preferred_locations": ["London", "Oxford"],
            },
            "constraints": {
                "work_authorisation": ["United Kingdom"],
                "locations": ["United Kingdom"],
                "hard_constraints": ["No graduate roles"],
            },
            "self_assessment": {
                "development_areas": ["Conventional hands-on software engineering depth"]
            },
        }
    )


class _FakeAdviser:
    def __init__(self, *, invalid_ref: bool = False) -> None:
        self.invalid_ref = invalid_ref
        self.calls = []

    def assess(
        self,
        candidate_context,
        intake,
        *,
        allowed_evidence_ids,
        allowed_intake_refs,
    ) -> CandidateAdviserAssessment:
        self.calls.append(
            (candidate_context, intake, allowed_evidence_ids, allowed_intake_refs)
        )
        evidence_ref = "missing-evidence" if self.invalid_ref else allowed_evidence_ids[0]
        return CandidateAdviserAssessment.model_validate(
            {
                "professional_identity": "Customer-facing technical leader transitioning into applied AI delivery.",
                "strengths": [
                    {
                        "text": "Customer requirements translation and technical delivery.",
                        "confidence": "high",
                        "supporting_refs": [
                            {"source_type": "career_evidence", "source_ref": evidence_ref}
                        ],
                    }
                ],
                "transferable_capabilities": [
                    {
                        "text": "Adjacent fit for solution-engineering work.",
                        "confidence": "medium",
                        "supporting_refs": [
                            {
                                "source_type": "candidate_intake",
                                "source_ref": "career_direction.target_role_families",
                            }
                        ],
                    }
                ],
                "development_gaps": [
                    {
                        "text": "Production AI and cloud deployment evidence remains limited.",
                        "confidence": "medium",
                        "supporting_refs": [
                            {
                                "source_type": "candidate_intake",
                                "source_ref": "career_direction.desired_capabilities",
                            }
                        ],
                    }
                ],
                "role_hypotheses": [
                    {
                        "role_family": "AI Solutions Engineer",
                        "rationale": "Builds on customer delivery while increasing AI depth.",
                        "current_fit": "medium",
                        "career_value": "high",
                        "transition_risk": "medium",
                        "confidence": "medium",
                        "supporting_refs": [
                            {"source_type": "career_evidence", "source_ref": evidence_ref},
                            {
                                "source_type": "candidate_intake",
                                "source_ref": "career_direction.target_role_families",
                            },
                        ],
                    }
                ],
                "career_transition_assessment": "Evolutionary transition through customer-facing AI delivery.",
                "open_questions": ["How much production deployment ownership has the candidate had?"],
                "career_strategy_summary": "Prioritise roles that retain customer/technical strengths while adding production AI.",
                "job_search_strategy_summary": "Search AI solutions, applied AI and adjacent technical solution roles.",
            }
        )


def test_confirmed_intake_and_adviser_enrich_strategic_context_without_matching_evidence(
    client, db_session
) -> None:
    _, email = _auth(client, "adviser-context@example.com")
    user_id = _confirm_cv(db_session, email)
    adviser = _FakeAdviser()
    service = CandidateAdviserService(db_session, adviser=adviser)

    saved = service.save_intake(user_id, _intake())
    assert saved.confirmed is False
    service.confirm_intake(user_id)
    review = service.assess(user_id)
    assert review.state == CandidateAdviserState.REVIEW_READY
    confirmed = service.confirm_assessment(user_id)
    assert confirmed.state == CandidateAdviserState.CONFIRMED

    context = PersistedCandidateContextLoader(db_session).load_confirmed(user_id)
    assert context is not None
    assert context.eligibility.work_authorisation == ["United Kingdom"]
    assert context.eligibility.locations == ["United Kingdom"]
    assert "AI Solutions Engineer" in context.career_strategy_text
    assert context.adviser.professional_identity.startswith("Customer-facing")

    search = candidate_search_profile(context)
    career = candidate_career_profile(context)
    matching = candidate_matching_profile(
        context,
        JobProfile(requirements=[JobRequirement(text="Customer requirements")]),
    )
    assert search.adviser.role_hypotheses == ["AI Solutions Engineer"]
    assert career.adviser.development_priorities
    assert not hasattr(matching, "adviser")
    assert [item.title for item in matching.evidence] == ["Customer delivery"]


def test_assessment_becomes_stale_after_confirmed_source_changes(client, db_session) -> None:
    _, email = _auth(client, "adviser-stale@example.com")
    user_id = _confirm_cv(db_session, email)
    service = CandidateAdviserService(db_session, adviser=_FakeAdviser())
    service.save_intake(user_id, _intake())
    service.confirm_intake(user_id)
    service.assess(user_id)
    service.confirm_assessment(user_id)

    changed = _intake().model_copy(deep=True)
    changed.career_direction.target_role_families = ["Applied AI Engineer"]
    service.save_intake(user_id, changed)

    stale = service.read_assessment(user_id)
    assert stale.state == CandidateAdviserState.STALE
    context = PersistedCandidateContextLoader(db_session).load_confirmed(user_id)
    assert context is not None
    assert context.adviser.professional_identity == ""


def test_adviser_rejects_unknown_source_references(client, db_session) -> None:
    _, email = _auth(client, "adviser-invalid-ref@example.com")
    user_id = _confirm_cv(db_session, email)
    service = CandidateAdviserService(db_session, adviser=_FakeAdviser(invalid_ref=True))
    service.save_intake(user_id, _intake())
    service.confirm_intake(user_id)

    with pytest.raises(SemanticOutputError, match="unknown career evidence"):
        service.assess(user_id)


def test_empty_intake_cannot_be_confirmed(client, db_session) -> None:
    _, email = _auth(client, "adviser-empty@example.com")
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    service = CandidateAdviserService(db_session, adviser=_FakeAdviser())
    service.save_intake(user_id, CandidateIntakeProfileData())

    with pytest.raises(ValueError, match="meaningful answer"):
        service.confirm_intake(user_id)


def test_candidate_adviser_api_is_user_scoped(client, db_session) -> None:
    headers_a, email_a = _auth(client, "adviser-a@example.com")
    headers_b, _ = _auth(client, "adviser-b@example.com")
    _confirm_cv(db_session, email_a)

    payload = _intake().model_dump(mode="json")
    saved = client.put("/api/v1/career-adviser/intake", headers=headers_a, json=payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    other = client.get("/api/v1/career-adviser/intake", headers=headers_b)
    assert other.status_code == 200
    assert other.json()["revision"] == 0
    assert other.json()["confirmed"] is False

    missing_assessment = client.get("/api/v1/career-adviser/assessment", headers=headers_b)
    assert missing_assessment.status_code == 404

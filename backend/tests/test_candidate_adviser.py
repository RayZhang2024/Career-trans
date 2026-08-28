import json

import pytest
from sqlalchemy import select

from app.agents.candidate_adviser import SemanticCandidateAdviser
from app.api.deps import get_candidate_adviser_service
from app.main import app
from app.models.candidate_adviser import CandidateAdviserAssessmentRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord
from app.models.user import User
from app.providers.llm import SemanticOutputError
from app.schemas.candidate_adviser import CandidateAdviserAssessmentContent, CandidateAdviserIntake
from app.schemas.job import JobProfile, JobRequirement
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_profile_compaction import candidate_career_profile, candidate_matching_profile, candidate_search_profile
from app.services.cv_ingestion_service import CVIngestionService, PersistedCandidateContextLoader
from app.schemas.cv_ingestion import CandidateCVData


def _user(db_session, email: str) -> str:
    user = User(email=email, password_hash="not-used-in-service-test")
    db_session.add(user)
    db_session.commit()
    return user.id


def _auth(client, email: str) -> tuple[dict[str, str], str]:
    credentials = {"email": email, "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, email


def _confirmed_cv(db_session, user_id: str) -> None:
    data = CandidateCVData.model_validate(
        {
            "employment": [{"employer": "Example", "title": "Engineer", "description": "Built systems."}],
            "skills": [{"name": "Python"}],
            "evidence": [
                {
                    "evidence_type": "employment",
                    "title": "Platform delivery",
                    "text": "Built reliable Python platforms.",
                    "skills": ["Python"],
                }
            ],
        }
    )
    service = CVIngestionService(db_session)
    draft = service.upload(user_id, [("cv.json", "application/json", json.dumps(data.model_dump(mode="json")).encode())])
    service.interpret(user_id, draft.id)
    service.confirm(user_id, draft.id)


def _intake() -> CandidateAdviserIntake:
    return CandidateAdviserIntake(
        career_direction="Move toward applied AI delivery.",
        work_preferences=["Hybrid work"],
        constraints=["UK roles"],
        eligibility={"locations": ["United Kingdom"], "work_authorisation": ["UK right to work"]},
    )


def _content(*, evidence_id: str = "", intake_path: str = "career_direction") -> CandidateAdviserAssessmentContent:
    references = [{"source_type": "intake", "reference": intake_path}]
    if evidence_id:
        references.append({"source_type": "career_evidence", "reference": evidence_id})
    insight = {"text": "Source-grounded adviser summary.", "source_references": references}
    return CandidateAdviserAssessmentContent.model_validate(
        {
            "professional_positioning": insight,
            "transferable_strengths": [insight],
            "development_gaps": [],
            "role_hypotheses": [insight],
            "transition_assessment": insight,
            "open_questions": [],
            "career_strategy_summary": insight,
            "job_search_strategy_summary": insight,
        }
    )


class _FakeAdviser:
    def __init__(self, *, bad_evidence_id: bool = False) -> None:
        self.bad_evidence_id = bad_evidence_id
        self.calls = 0

    def assess(self, *, intake, evidence):
        self.calls += 1
        evidence_id = "not-supplied" if self.bad_evidence_id else str(evidence[0]["evidence_id"])
        return _content(evidence_id=evidence_id)


def test_adviser_assessment_is_grounded_stale_and_user_scoped(db_session) -> None:
    user_a = _user(db_session, "adviser-a@example.com")
    user_b = _user(db_session, "adviser-b@example.com")
    _confirmed_cv(db_session, user_a)
    _confirmed_cv(db_session, user_b)
    fake = _FakeAdviser()
    service = CandidateAdviserService(db_session, agent=fake)

    service.save_intake(user_a, _intake())
    assessment = service.assess(user_a)
    assert assessment.status == "current"
    assert fake.calls == 1
    assert service.get_assessment(user_b) is None

    service.save_intake(user_a, _intake())
    assert service.get_assessment(user_a).status == "current"

    changed = _intake().model_copy(update={"constraints": ["UK roles", "No relocation"]})
    service.save_intake(user_a, changed)
    assert service.get_assessment(user_a).status == "stale"
    assert db_session.scalar(select(CandidateAdviserAssessmentRecord.status).where(CandidateAdviserAssessmentRecord.user_id == user_a)) == "stale"

    service.assess(user_a)
    evidence = db_session.scalar(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_a))
    assert evidence is not None
    evidence.fingerprint = "changed-source-evidence"
    db_session.commit()
    assert service.get_assessment(user_a).status == "stale"


def test_adviser_rejects_unsupplied_evidence_reference_without_persisting(db_session) -> None:
    user_id = _user(db_session, "adviser-invalid-reference@example.com")
    _confirmed_cv(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_FakeAdviser(bad_evidence_id=True))
    service.save_intake(user_id, _intake())

    with pytest.raises(ValueError, match="was not supplied"):
        service.assess(user_id)

    assert service.get_assessment(user_id) is None


def test_adviser_projection_enriches_search_and_career_but_not_matching(db_session) -> None:
    user_id = _user(db_session, "adviser-projection@example.com")
    _confirmed_cv(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_FakeAdviser())
    service.save_intake(user_id, _intake())
    service.assess(user_id)

    context = PersistedCandidateContextLoader(db_session).load_confirmed(user_id)
    assert context is not None
    assert context.eligibility.locations == ["United Kingdom"]
    assert "Move toward applied AI delivery." in context.career_strategy_text
    assert "Source-grounded adviser summary." in context.career_strategy_text
    assert "Hybrid work" in context.job_search_criteria_text
    assert "Source-grounded adviser summary." in context.job_search_criteria_text
    assert "Source-grounded adviser summary." in candidate_search_profile(context).career_strategy_text
    assert candidate_career_profile(context).eligibility.locations == ["United Kingdom"]
    matching = candidate_matching_profile(
        context,
        JobProfile(requirements=[JobRequirement(text="Python platform delivery")]),
    )
    assert [item.title for item in matching.evidence] == ["Platform delivery"]
    assert "Source-grounded adviser summary." not in matching.profile_summary


def test_confirmed_cv_without_adviser_intake_remains_usable(db_session) -> None:
    user_id = _user(db_session, "adviser-cv-only@example.com")
    _confirmed_cv(db_session, user_id)

    context = PersistedCandidateContextLoader(db_session).load_confirmed(user_id)

    assert context is not None
    assert [item.title for item in context.evidence] == ["Platform delivery"]
    assert context.career_strategy_text == ""
    assert context.job_search_criteria_text == ""


def test_adviser_api_is_authenticated_and_user_scoped(client, db_session) -> None:
    headers_a, email_a = _auth(client, "adviser-api-a@example.com")
    headers_b, _ = _auth(client, "adviser-api-b@example.com")
    user_a = db_session.scalar(select(User.id).where(User.email == email_a))
    assert user_a is not None
    _confirmed_cv(db_session, user_a)
    service = CandidateAdviserService(db_session, agent=_FakeAdviser())
    app.dependency_overrides[get_candidate_adviser_service] = lambda: service
    try:
        saved = client.put("/api/v1/candidate-adviser/intake", headers=headers_a, json=_intake().model_dump(mode="json"))
        assert saved.status_code == 200
        assert client.get("/api/v1/candidate-adviser/intake", headers=headers_b).status_code == 404
        generated = client.post("/api/v1/candidate-adviser/assessment", headers=headers_a)
        assert generated.status_code == 200
        assert generated.json()["status"] == "current"
        assert client.get("/api/v1/candidate-adviser/assessment", headers=headers_b).status_code == 404
    finally:
        app.dependency_overrides.pop(get_candidate_adviser_service, None)


def test_semantic_adviser_rejects_malformed_structured_output() -> None:
    class _Responses:
        def create(self, **_kwargs):
            return type("Response", (), {"output_text": "not-json"})()

    client = type("Client", (), {"responses": _Responses()})()
    agent = SemanticCandidateAdviser(client, "test-model")

    with pytest.raises(SemanticOutputError, match="invalid structured output"):
        agent.assess(intake=_intake(), evidence=[])

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.agents.candidate_adviser import SemanticCandidateAdviser
from app.api.deps import get_candidate_adviser_service
from app.main import app
from app.models.candidate_adviser import CandidateAdviserAssessmentRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.user import User
from app.providers.llm import SemanticOutputError
from app.schemas.candidate_adviser import CandidateAdviserAssessmentContent, CandidateAdviserIntake, CandidateAdviserSemanticInput
from app.schemas.job import JobProfile, JobRequirement
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_adviser_compaction import compact_candidate_adviser_input
from app.services.candidate_adviser_references import candidate_adviser_reference_catalog
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


def _confirmed_cv(db_session, user_id: str, *, evidence: list[dict[str, object]] | None = None) -> None:
    data = CandidateCVData.model_validate(
        {
            "employment": [{"employer": "Example", "title": "Engineer", "description": "Built systems."}],
            "skills": [{"name": "Python"}],
            "evidence": evidence or [
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


def test_adviser_assessment_schema_requires_every_object_property_and_allows_empty_collections() -> None:
    schema = CandidateAdviserAssessmentContent.model_json_schema()
    required = set(schema["required"])
    properties = schema["properties"]

    # OpenAI strict Structured Outputs requires every object property to be
    # required, while arrays can still be represented by an explicit empty list.
    assert required == set(properties)
    for field in (
        "transferable_strengths",
        "development_gaps",
        "role_hypotheses",
        "open_questions",
    ):
        assert properties[field].get("maxItems") == 12
        assert "default" not in properties[field]

    empty = _content().model_copy(
        update={
            "transferable_strengths": [],
            "development_gaps": [],
            "role_hypotheses": [],
            "open_questions": [],
        }
    )
    assert empty.transferable_strengths == []
    assert empty.development_gaps == []
    assert empty.role_hypotheses == []
    assert empty.open_questions == []


class _FakeAdviser:
    def __init__(self, *, bad_evidence_id: bool = False, intake_path: str = "career_direction") -> None:
        self.bad_evidence_id = bad_evidence_id
        self.intake_path = intake_path
        self.calls = 0

    def assess(self, *, semantic_input):
        self.calls += 1
        self.semantic_input = semantic_input
        evidence_id = "not-supplied" if self.bad_evidence_id else semantic_input.career_evidence[0].evidence_id
        return _content(evidence_id=evidence_id, intake_path=self.intake_path)


def test_adviser_assessment_is_grounded_stale_and_user_scoped(db_session) -> None:
    user_a = _user(db_session, "adviser-a@example.com")
    user_b = _user(db_session, "adviser-b@example.com")
    _confirmed_cv(db_session, user_a)
    _confirmed_cv(db_session, user_b)
    fake = _FakeAdviser()
    service = CandidateAdviserService(db_session, agent=fake)

    service.save_intake(user_a, _intake())
    assessment = service.assess(user_a)
    assert assessment.status == "review_ready"
    assert fake.calls == 1
    assert fake.semantic_input.structured_cv.employment[0].title == "Engineer"
    assert fake.semantic_input.structured_cv.skills[0].name == "Python"
    assert service.get_assessment(user_b) is None

    assert service.confirm_assessment(user_a).status == "confirmed"

    service.save_intake(user_a, _intake())
    assert service.get_assessment(user_a).status == "confirmed"

    changed = _intake().model_copy(update={"constraints": ["UK roles", "No relocation"]})
    service.save_intake(user_a, changed)
    assert service.get_assessment(user_a).status == "stale"
    assert db_session.scalar(select(CandidateAdviserAssessmentRecord.status).where(CandidateAdviserAssessmentRecord.user_id == user_a)) == "confirmed"
    with pytest.raises(ValueError, match="stale"):
        service.confirm_assessment(user_a)

    service.save_intake(user_a, _intake())
    assert service.get_assessment(user_a).status == "confirmed"

    assert service.assess(user_a).status == "review_ready"
    assert service.confirm_assessment(user_a).status == "confirmed"
    assert service.confirm_assessment(user_a).status == "confirmed"
    # Active evidence is rebuilt from the confirmed profile rather than trusting
    # mutable historical rows. A factual structured-profile change is what makes
    # the adviser assessment stale.
    structured = db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_a))
    assert structured is not None
    changed_data = json.loads(structured.structured_json)
    changed_data["evidence"][0]["text"] = "Changed confirmed evidence that reaches the adviser input."
    structured.structured_json = json.dumps(changed_data)
    db_session.commit()
    assert service.get_assessment(user_a).status == "stale"
    assert db_session.scalar(select(CandidateAdviserAssessmentRecord.status).where(CandidateAdviserAssessmentRecord.user_id == user_a)) == "confirmed"


def test_adviser_semantic_evidence_uses_current_cv_order_when_timestamps_tie(db_session) -> None:
    user_id = _user(db_session, "adviser-evidence-order@example.com")
    source_order = [
        {"evidence_type": "project", "title": "Third in database", "text": "Third source statement."},
        {"evidence_type": "employment", "title": "First in database", "text": "First source statement."},
        {"evidence_type": "achievement", "title": "Second in database", "text": "Second source statement."},
    ]
    _confirmed_cv(db_session, user_id, evidence=source_order)
    records = list(db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)))
    assert len(records) == 4
    for record in records:
        record.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.commit()

    fake = _FakeAdviser()
    service = CandidateAdviserService(db_session, agent=fake)
    service.save_intake(user_id, _intake())
    service.assess(user_id)

    assert [item.title for item in fake.semantic_input.career_evidence[:3]] == [
        "Third in database",
        "First in database",
        "Second in database",
    ]


def test_assessment_fingerprint_tracks_compacted_structured_cv_input(db_session) -> None:
    user_id = _user(db_session, "adviser-structured-stale@example.com")
    _confirmed_cv(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_FakeAdviser())
    service.save_intake(user_id, _intake())
    service.assess(user_id)
    service.confirm_assessment(user_id)

    structured = db_session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
    assert structured is not None
    data = json.loads(structured.structured_json)
    data["projects"] = [{"name": "New confirmed project", "description": "Relevant source-backed work."}]
    structured.structured_json = json.dumps(data, sort_keys=True)
    db_session.commit()

    assert service.get_assessment(user_id).status == "stale"


def test_adviser_rejects_unsupplied_evidence_reference_without_persisting(db_session) -> None:
    user_id = _user(db_session, "adviser-invalid-reference@example.com")
    _confirmed_cv(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_FakeAdviser(bad_evidence_id=True))
    service.save_intake(user_id, _intake())

    with pytest.raises(ValueError, match="was not supplied"):
        service.assess(user_id)

    assert service.get_assessment(user_id) is None


def test_adviser_rejects_invented_intake_path_without_exposing_private_content(db_session) -> None:
    user_id = _user(db_session, "adviser-invalid-intake-reference@example.com")
    _confirmed_cv(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_FakeAdviser(intake_path="intake.career_direction"))
    service.save_intake(user_id, _intake())

    with pytest.raises(ValueError, match="intake field that was not supplied") as exc_info:
        service.assess(user_id)

    assert "intake.career_direction" not in str(exc_info.value)
    assert "Move toward applied AI delivery" not in str(exc_info.value)
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
    assert "Source-grounded adviser summary." not in context.career_strategy_text
    assert "Hybrid work" in context.job_search_criteria_text
    assert "Source-grounded adviser summary." not in context.job_search_criteria_text
    assert service.confirm_assessment(user_id).status == "confirmed"

    context = PersistedCandidateContextLoader(db_session).load_confirmed(user_id)
    assert context is not None
    assert "Source-grounded adviser summary." in context.career_strategy_text
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
    assert [item.title for item in context.evidence] == ["Platform delivery", "Engineer at Example"]
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
        assert generated.json()["status"] == "review_ready"
        assert client.post("/api/v1/candidate-adviser/assessment/confirm", headers=headers_a).json()["status"] == "confirmed"
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
        agent.assess(semantic_input=CandidateAdviserSemanticInput(intake=_intake(), structured_cv=CandidateCVData()))


def test_adviser_semantic_projection_is_deterministically_bounded() -> None:
    intake = CandidateAdviserIntake(
        career_direction="direction " * 1_000,
        work_preferences=["preference " * 100 for _ in range(30)],
        constraints=["constraint " * 100 for _ in range(30)],
        self_assessment=["assessment " * 100 for _ in range(30)],
        motivations=["motivation " * 100 for _ in range(30)],
        tradeoffs=["tradeoff " * 100 for _ in range(30)],
        eligibility={"locations": ["location " * 100 for _ in range(30)]},
    )
    structured = CandidateCVData.model_validate(
        {
            "employment": [{"employer": "Employer", "title": "Engineer", "description": "description " * 200} for _ in range(30)],
            "skills": [{"name": "skill " * 100} for _ in range(100)],
            "projects": [{"name": "Project", "description": "description " * 200, "skills": ["skill " * 100] * 30} for _ in range(30)],
        }
    )
    projection = compact_candidate_adviser_input(
        intake=intake,
        structured_cv=structured,
        career_evidence=[
            {"evidence_id": str(index), "title": "title " * 100, "text": "evidence " * 400, "skills": ["skill " * 100] * 30}
            for index in range(40)
        ],
    )

    assert len(projection.intake.career_direction) <= 600
    assert len(projection.intake.work_preferences) == 12
    assert all(len(item) <= 240 for item in projection.intake.work_preferences)
    assert len(projection.intake.eligibility.locations) == 12
    assert len(projection.structured_cv.employment) == 12
    assert len(projection.structured_cv.skills) == 60
    assert len(projection.structured_cv.projects) == 8
    assert len(projection.career_evidence) == 24
    assert all(len(item.text) <= 1_200 and len(item.skills) <= 16 for item in projection.career_evidence)

    calls = []

    class _Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"output_text": json.dumps(_content(intake_path="career_direction").model_dump())})()

    agent = SemanticCandidateAdviser(type("Client", (), {"responses": _Responses()})(), "test-model")
    agent.assess(semantic_input=projection)
    request_content = calls[0]["input"][1]["content"]
    catalog = json.loads(request_content.split("ALLOWED_SOURCE_REFERENCES:\n", 1)[1].split("\n\nINPUT:\n", 1)[0])
    payload = json.loads(request_content.split("INPUT:\n", 1)[1])
    schema = calls[0]["text"]["format"]["schema"]
    assert len(payload["intake"]["career_direction"]) <= 600
    assert len(payload["career_evidence"]) == 24
    assert len(payload["structured_cv"]["skills"]) == 60
    assert set(schema["required"]) == set(schema["properties"])
    assert catalog == candidate_adviser_reference_catalog(projection)
    assert catalog["intake"] == [
        "career_direction",
        "work_preferences",
        "constraints",
        "self_assessment",
        "motivations",
        "tradeoffs",
        "eligibility.locations",
    ]
    assert catalog["career_evidence"] == [str(index) for index in range(24)]


def test_adviser_reference_catalog_includes_only_populated_exact_intake_tokens() -> None:
    semantic_input = CandidateAdviserSemanticInput(
        intake=CandidateAdviserIntake(
            career_direction="Applied AI delivery.",
            eligibility={"work_authorisation": ["UK right to work"], "locations": ["London"]},
        ),
        structured_cv=CandidateCVData(),
        career_evidence=[],
    )

    assert candidate_adviser_reference_catalog(semantic_input) == {
        "intake": ["career_direction", "eligibility.work_authorisation", "eligibility.locations"],
        "career_evidence": [],
        "clarification": [],
    }

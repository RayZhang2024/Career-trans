import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.agents.candidate_adviser import SemanticCandidateAdviser
from app.api.deps import get_user_candidate_adviser_service
from app.main import app
from app.models.candidate_adviser import CandidateAdviserAssessmentRecord, CandidateAdviserClarificationRecord
from app.models.candidate_adviser import CandidateAdviserRefinementJourneyRecord, CandidateAdviserClarificationAreaRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.user import User
from app.providers.llm import SemanticOutputError, SemanticProviderConfigurationError
from app.schemas.candidate_adviser import AdviserOpenQuestion, CandidateAdviserAssessmentContent, CandidateAdviserAssessmentStatus, CandidateAdviserClarificationAnswer, CandidateAdviserClarificationStatus, CandidateAdviserIntake, CandidateAdviserSemanticInput, ClarificationInterpretation, ProviderCandidateAdviserAssessmentContent
from app.schemas.candidate_adviser import AdviserClarificationArea, CandidateAdviserAreaSelectionRequest, CandidateAdviserQuestionGenerationRequest, ProviderRoundQuestionSet
from app.schemas.job import JobProfile, JobRequirement
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_adviser_compaction import compact_candidate_adviser_input
from app.services.candidate_adviser_references import candidate_adviser_reference_catalog
from app.services.candidate_profile_compaction import candidate_career_profile, candidate_matching_profile, candidate_search_profile
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.cv_ingestion_service import CVIngestionService
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

    # The canonical decoder keeps new area fields optional so historical JSON
    # without those fields remains readable; the provider DTO requires them.
    assert required == set(properties) - {"clarification_areas", "assessment_limitations"}
    for field in (
        "transferable_strengths",
        "development_gaps",
        "role_hypotheses",
        "open_questions",
    ):
        assert properties[field].get("maxItems") == 12
        assert "default" not in properties[field]
    assert properties["clarification_areas"].get("maxItems") == 6
    assert properties["assessment_limitations"].get("maxItems") == 12

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


def test_legacy_assessment_questions_remain_readable_without_choices_and_wire_model_requires_them() -> None:
    legacy = _content().model_dump(mode="json")
    legacy["open_questions"] = [{
        "text": "Historical question?",
        "source_references": [{"source_type": "intake", "reference": "career_direction"}],
    }]
    parsed = CandidateAdviserAssessmentContent.model_validate(legacy)
    assert parsed.open_questions[0].suggested_answers == []

    legacy["open_questions"][0]["suggested_answers"] = ["I led the work", "I contributed"]
    with pytest.raises(ValueError):
        ProviderCandidateAdviserAssessmentContent.model_validate(legacy)


def test_service_rejects_immediate_questions_from_new_area_assessment(db_session) -> None:
    user_id = _user(db_session, "adviser-invalid-generated-options@example.com")
    _confirmed_cv(db_session, user_id)
    content = _content()
    content.open_questions = [AdviserOpenQuestion(
        text="What work did you own?",
        source_references=[{"source_type": "intake", "reference": "career_direction"}],
        suggested_answers=["I led work", " I   led work ", "I supported work"],
    )]

    class _InvalidAgent:
        def assess(self, *, semantic_input):
            return content

    service = CandidateAdviserService(db_session, agent=_InvalidAgent())
    service.save_intake(user_id, _intake())
    with pytest.raises(ValueError, match="must not generate immediate clarification questions"):
        service.assess(user_id)
    assert db_session.scalar(select(CandidateAdviserAssessmentRecord).where(
        CandidateAdviserAssessmentRecord.user_id == user_id
    )) is None


def test_bounded_refinement_commits_area_subset_and_reuses_generated_questions(db_session) -> None:
    from app.services.candidate_adviser_journey_service import CandidateAdviserJourneyService

    user_id = _user(db_session, "adviser-bounded-refinement@example.com")
    _confirmed_cv(db_session, user_id)
    content = _content().model_copy(update={
        "clarification_areas": [
            AdviserClarificationArea(area_key="delivery", title="Delivery ownership", rationale="Clarify the candidate's delivery role.", source_references=[{"source_type": "intake", "reference": "career_direction"}]),
            AdviserClarificationArea(area_key="technical", title="Technical decisions", rationale="Understand the decisions made.", source_references=[{"source_type": "intake", "reference": "career_direction"}]),
        ],
        "open_questions": [],
    })

    class _Agent:
        def assess(self, *, semantic_input):
            self.semantic_input = semantic_input
            return content

    class _Questions:
        calls = 0

        def generate(self, *, generation_input):
            self.calls += 1
            assert [area.area_key for area in generation_input.selected_areas] == ["delivery"]
            return ProviderRoundQuestionSet.model_validate({"area_groups": [{
                "area_key": "delivery",
                "questions": [{
                    "question_key": "delivery-ownership",
                    "text": "What delivery work did you own?",
                    "source_references": [{"source_type": "intake", "reference": "career_direction"}],
                    "suggested_answers": ["I led delivery", "I contributed", "I supported the work"],
                }],
            }]})

    agent = _Agent()
    questions = _Questions()
    service = CandidateAdviserService(db_session, agent=agent, question_generator=questions)
    service.save_intake(user_id, _intake())
    draft = service.assess(user_id)
    assert draft.contract_version.value == "clarification_areas_v1"
    assert len(draft.assessment_authority_token) == 64
    assert all(character in "0123456789abcdef" for character in draft.assessment_authority_token)
    assert draft.content.open_questions == []
    assert [area.area_key for area in draft.content.clarification_areas] == ["delivery", "technical"]
    assert agent.semantic_input.refinement_control.round_number == 1
    service.confirm_assessment(user_id)
    current = service._current_refinement_journey(user_id)
    authority = {
        "expected_refinement_journey_id": current.journey_key,
        "expected_round_number": 1,
        "expected_assessment_fingerprint": draft.input_fingerprint,
        "expected_assessment_authority_token": draft.assessment_authority_token,
    }
    selected = service.select_refinement_areas(user_id, CandidateAdviserAreaSelectionRequest(**authority, selected_area_keys=["delivery"]))
    assert [(area.area_key, area.selection_state) for area in selected] == [("delivery", "selected"), ("technical", "skipped")]
    journey = CandidateAdviserJourneyService(db_session).read(user_id)
    assert journey.refinement_state == "questions_pending"
    assert journey.next_action.value == "generate_round_questions"

    generation_authority = CandidateAdviserQuestionGenerationRequest(**authority)
    generated = service.generate_round_questions(user_id, generation_authority)
    retried = service.generate_round_questions(user_id, generation_authority)
    assert questions.calls == 1
    assert [row.clarification_id for row in generated] == [row.clarification_id for row in retried]
    assert generated[0].parent_area_key == "delivery"
    assert generated[0].round_number == 1
    journey = CandidateAdviserJourneyService(db_session).read(user_id)
    assert journey.refinement_state == "questions_active"
    assert journey.round_question_count == 1
    assert db_session.scalar(select(CandidateAdviserClarificationAreaRecord).where(
        CandidateAdviserClarificationAreaRecord.user_id == user_id,
        CandidateAdviserClarificationAreaRecord.area_key == "technical",
    )).selection_state == "skipped"


def test_bounded_refinement_caps_at_two_rounds_and_allows_terminal_empty_areas(db_session) -> None:
    user_id = _user(db_session, "adviser-bounded-refinement-cap@example.com")
    _confirmed_cv(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=object())
    service.save_intake(user_id, _intake())
    journey = CandidateAdviserRefinementJourneyRecord(
        id="journey-cap", user_id=user_id, journey_key="journey-cap", round_number=2,
        rounds_completed=2, state="assessment_update", origin_context_fingerprint="old",
    )
    db_session.add(journey)
    db_session.commit()
    content = _content().model_copy(update={"open_questions": [], "clarification_areas": []})

    class _Agent:
        def assess(self, *, semantic_input):
            assert semantic_input.refinement_control.round_number == 2
            assert semantic_input.refinement_control.rounds_completed == 2
            assert semantic_input.refinement_control.areas_allowed is False
            return content

    service = CandidateAdviserService(db_session, agent=_Agent())
    # There must be an assessment row for a legitimate update; seed a confirmed
    # legacy record, then the resulting draft upgrades to the area contract.
    fingerprint = service.input_fingerprint(user_id)
    db_session.add(CandidateAdviserAssessmentRecord(
        user_id=user_id, input_fingerprint=fingerprint, contract_version="legacy_questions",
        status="confirmed", assessment_json=json.dumps(content.model_dump(mode="json"), sort_keys=True),
    ))
    db_session.commit()
    draft = service.assess(user_id)
    assert draft.content.clarification_areas == []
    assert service.confirm_assessment(user_id).status is CandidateAdviserAssessmentStatus.CONFIRMED
    stored = db_session.scalar(select(CandidateAdviserRefinementJourneyRecord).where(
        CandidateAdviserRefinementJourneyRecord.user_id == user_id,
    ))
    assert stored.state == "complete"
    assert stored.rounds_completed == 2


def test_material_intake_change_restarts_an_incomplete_area_selection_journey(db_session) -> None:
    user_id = _user(db_session, "adviser-bounded-refinement-refresh@example.com")
    _confirmed_cv(db_session, user_id)
    content = _content().model_copy(update={"open_questions": [], "clarification_areas": [AdviserClarificationArea(
        area_key="delivery", title="Delivery ownership", rationale="Clarify delivery work.",
        source_references=[{"source_type": "intake", "reference": "career_direction"}],
    )]})

    class _Agent:
        def assess(self, *, semantic_input):
            return content

    service = CandidateAdviserService(db_session, agent=_Agent())
    service.save_intake(user_id, _intake())
    service.assess(user_id)
    service.confirm_assessment(user_id)
    first = service._current_refinement_journey(user_id)
    assert first.state == "area_selection"
    service.save_intake(user_id, _intake().model_copy(update={"career_direction": "Explore synthetic platform leadership."}))
    refreshed = service.assess(user_id)
    second = service._current_refinement_journey(user_id)
    assert refreshed.status is CandidateAdviserAssessmentStatus.REVIEW_READY
    assert second.journey_key != first.journey_key
    assert second.state == "initial_assessment_review"
    assert first.state == "superseded"
    assert len(db_session.scalars(select(CandidateAdviserRefinementJourneyRecord).where(
        CandidateAdviserRefinementJourneyRecord.user_id == user_id,
    )).all()) == 2


def test_historical_assessment_read_does_not_run_generation_only_choice_validation(db_session) -> None:
    user_id = _user(db_session, "adviser-historical-options@example.com")
    _confirmed_cv(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_FakeAdviser())
    service.save_intake(user_id, _intake())
    old = _content().model_dump(mode="json")
    old["open_questions"] = [{
        "text": "Historical question?",
        "source_references": [{"source_type": "intake", "reference": "career_direction"}],
    }]
    fingerprint = service.input_fingerprint(user_id)
    db_session.add(CandidateAdviserAssessmentRecord(
        user_id=user_id, input_fingerprint=fingerprint, status="confirmed",
        assessment_json=json.dumps(old),
    ))
    db_session.commit()
    read = service.get_assessment(user_id)
    assert read is not None and read.content.open_questions[0].suggested_answers == []


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

    with pytest.raises(ValueError, match="material candidate-context change"):
        service.assess(user_a)
    assert service.get_assessment(user_a).status == "confirmed"
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

    reader = CanonicalCandidateReadService(db_session)
    context = reader.candidate_context(
        reader.read(user_id), require_structured_profile=True, require_complete_evidence=True
    )
    assert context is not None
    assert context.eligibility.locations == ["United Kingdom"]
    assert "Move toward applied AI delivery." in context.career_strategy_text
    assert "Source-grounded adviser summary." not in context.career_strategy_text
    assert "Hybrid work" in context.job_search_criteria_text
    assert "Source-grounded adviser summary." not in context.job_search_criteria_text
    assert service.confirm_assessment(user_id).status == "confirmed"

    reader = CanonicalCandidateReadService(db_session)
    context = reader.candidate_context(
        reader.read(user_id), require_structured_profile=True, require_complete_evidence=True
    )
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

    reader = CanonicalCandidateReadService(db_session)
    context = reader.candidate_context(
        reader.read(user_id), require_structured_profile=True, require_complete_evidence=True
    )

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
    app.dependency_overrides[get_user_candidate_adviser_service] = lambda: service
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
        app.dependency_overrides.pop(get_user_candidate_adviser_service, None)


def test_adviser_api_keeps_reads_and_prerequisite_failures_provider_free(client, db_session) -> None:
    """Lazy provider factories must only be reached by valid semantic mutations."""
    headers, email = _auth(client, "adviser-provider-boundary@example.com")
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    calls = {"adviser": 0, "clarification": 0}

    def unavailable_adviser():
        calls["adviser"] += 1
        raise SemanticProviderConfigurationError("Synthetic semantic configuration is unavailable.")

    def unavailable_clarification():
        calls["clarification"] += 1
        raise SemanticProviderConfigurationError("Synthetic semantic configuration is unavailable.")

    service = CandidateAdviserService(
        db_session,
        agent_factory=unavailable_adviser,
        clarification_interpreter_factory=unavailable_clarification,
    )
    app.dependency_overrides[get_user_candidate_adviser_service] = lambda: service
    try:
        # Missing intake wins over provider construction even with a CV.
        headers_without_intake, email_without_intake = _auth(client, "adviser-missing-intake@example.com")
        user_without_intake = db_session.scalar(select(User.id).where(User.email == email_without_intake))
        assert user_without_intake is not None
        _confirmed_cv(db_session, user_without_intake)
        assert client.post("/api/v1/candidate-adviser/assessment", headers=headers_without_intake).status_code == 409
        assert calls == {"adviser": 0, "clarification": 0}

        # Database-only reads/writes cannot construct either semantic provider.
        assert client.get("/api/v1/candidate-adviser/intake", headers=headers).status_code == 404
        assert client.put("/api/v1/candidate-adviser/intake", headers=headers, json=_intake().model_dump(mode="json")).status_code == 200
        assert client.get("/api/v1/candidate-adviser/intake", headers=headers).status_code == 200
        assert client.get("/api/v1/candidate-adviser/assessment", headers=headers).status_code == 404
        assert client.post("/api/v1/candidate-adviser/assessment/confirm", headers=headers).status_code == 409
        assert client.get("/api/v1/candidate-adviser/clarifications", headers=headers).status_code == 409
        assert calls == {"adviser": 0, "clarification": 0}

        # Missing confirmed CV wins over provider construction.
        assert client.post("/api/v1/candidate-adviser/assessment", headers=headers).status_code == 409
        assert calls == {"adviser": 0, "clarification": 0}
        # A missing clarification is resolved before its interpreter factory.
        assert client.post(
            "/api/v1/candidate-adviser/clarifications/not-current/answer",
            headers=headers,
            json={"answer_text": "Synthetic answer."},
        ).status_code == 404
        assert calls == {"adviser": 0, "clarification": 0}

        _confirmed_cv(db_session, user_id)
        # With all prerequisites valid, a configuration failure is a safe 503,
        # never incorrectly presented as an adviser lifecycle conflict.
        assert client.post("/api/v1/candidate-adviser/assessment", headers=headers).status_code == 503
        assert calls == {"adviser": 1, "clarification": 0}
    finally:
        app.dependency_overrides.pop(get_user_candidate_adviser_service, None)


def test_adviser_api_clarification_factory_is_lazy_for_currentness_and_confirm(client, db_session) -> None:
    headers, email = _auth(client, "adviser-clarification-provider-boundary@example.com")
    user_id = db_session.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    _confirmed_cv(db_session, user_id)
    seeded = CandidateAdviserService(db_session, agent=_FakeAdviser())
    seeded.save_intake(user_id, _intake())
    seeded.assess(user_id)
    confirmed = seeded.confirm_assessment(user_id)
    record = CandidateAdviserClarificationRecord(
        user_id=user_id,
        clarification_id="a" * 64,
        question_key="b" * 64,
        origin_assessment_fingerprint=confirmed.input_fingerprint,
        question_text="What positive synthetic delivery fact should be confirmed?",
        question_source_references_json=json.dumps([{"source_type": "intake", "reference": "career_direction"}]),
        priority_index=0,
        status=CandidateAdviserClarificationStatus.REVIEW_READY,
        answer_text="Synthetic answer.",
        interpretation_json=json.dumps({"answer_kind": "preference_intent", "confirmed_context_summary": "Synthetic preference context.", "proposed_evidence": []}),
    )
    db_session.add(record)
    db_session.commit()
    calls = {"adviser": 0, "clarification": 0}

    def unavailable_adviser():
        calls["adviser"] += 1
        raise SemanticProviderConfigurationError("Synthetic semantic configuration is unavailable.")

    def unavailable_interpreter():
        calls["clarification"] += 1
        raise SemanticProviderConfigurationError("Synthetic semantic configuration is unavailable.")

    service = CandidateAdviserService(
        db_session,
        agent_factory=unavailable_adviser,
        clarification_interpreter_factory=unavailable_interpreter,
    )
    app.dependency_overrides[get_user_candidate_adviser_service] = lambda: service
    try:
        response = client.get("/api/v1/candidate-adviser/clarifications", headers=headers)
        assert response.status_code == 200, response.json()
        assert calls == {"adviser": 0, "clarification": 0}
        # Bad currentness resolves before a provider client is built.
        assert client.post("/api/v1/candidate-adviser/clarifications/not-current/answer", headers=headers, json={"answer_text": "Synthetic"}).status_code == 404
        assert calls == {"adviser": 0, "clarification": 0}
        # Valid current interpretation reaches the lazy factory and maps its
        # configuration error to 503 rather than an adviser lifecycle 409.
        assert client.post(f"/api/v1/candidate-adviser/clarifications/{'a' * 64}/answer", headers=headers, json={"answer_text": "Synthetic"}).status_code == 503
        assert calls == {"adviser": 0, "clarification": 1}
        # Confirm is deterministic and remains provider-free.
        assert client.post(f"/api/v1/candidate-adviser/clarifications/{'a' * 64}/confirm", headers=headers).status_code == 200
        assert calls == {"adviser": 0, "clarification": 1}
    finally:
        app.dependency_overrides.pop(get_user_candidate_adviser_service, None)


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


def test_regenerating_assessment_drafts_preserves_one_journey_and_prior_round_history(db_session) -> None:
    user_id = _user(db_session, "adviser-regenerate-draft-authority@example.com")
    _confirmed_cv(db_session, user_id)
    assessment_calls = 0
    proposed_keys = [
        ["r1-old-a", "r1-old-b"], ["r1-next-a", "r1-next-b"], ["r1-final-a", "r1-final-b"],
        ["r2-old-a", "r2-old-b"], ["r2-next-a", "r2-next-b"], [], ["forbidden-final-area"], [],
    ]

    class _Agent:
        def assess(self, *, semantic_input):
            nonlocal assessment_calls
            keys = proposed_keys[assessment_calls]
            assessment_calls += 1
            areas = [AdviserClarificationArea(
                area_key=key, title=f"Area {key}", rationale="Clarify this candidate-directed topic.",
                source_references=[{"source_type": "intake", "reference": "career_direction"}],
            ) for key in keys]
            return _content().model_copy(update={"clarification_areas": areas, "open_questions": []})

    class _Questions:
        def generate(self, *, generation_input):
            return ProviderRoundQuestionSet.model_validate({"area_groups": [{
                "area_key": area.area_key,
                "questions": [{
                    "question_key": f"{generation_input.round_number}-{area.area_key}-question",
                    "text": f"What experience relates to {area.title}?",
                    "source_references": [{"source_type": "intake", "reference": "career_direction"}],
                    "suggested_answers": ["I led it", "I contributed", "I supported it"],
                }],
            } for area in generation_input.selected_areas]})

    service = CandidateAdviserService(db_session, agent=_Agent(), question_generator=_Questions())
    service.save_intake(user_id, _intake())
    first = service.assess(user_id)
    assert len(first.assessment_authority_token) == 64
    journey_id = service._current_refinement_journey(user_id).journey_key
    stale_regeneration = service.assess(user_id, regenerate=True)
    stale_authority = {
        "expected_refinement_journey_id": journey_id,
        "expected_round_number": 1,
        "expected_assessment_fingerprint": stale_regeneration.input_fingerprint,
        "expected_assessment_authority_token": stale_regeneration.assessment_authority_token,
    }
    assert stale_regeneration.input_fingerprint == first.input_fingerprint
    assert stale_regeneration.assessment_authority_token != first.assessment_authority_token
    regenerated = service.assess(user_id, regenerate=True)
    assert regenerated.status is CandidateAdviserAssessmentStatus.REVIEW_READY
    assert service._current_refinement_journey(user_id).journey_key == journey_id
    assert len(db_session.scalars(select(CandidateAdviserRefinementJourneyRecord).where(
        CandidateAdviserRefinementJourneyRecord.user_id == user_id,
        CandidateAdviserRefinementJourneyRecord.state != "superseded",
    )).all()) == 1
    assert first.input_fingerprint == regenerated.input_fingerprint
    assert stale_regeneration.assessment_authority_token != regenerated.assessment_authority_token
    round1_areas = [area for area in service._journey_areas(user_id, journey_id) if area.round_number == 1]
    assert [(area.area_key, area.selection_state) for area in round1_areas] == [("r1-final-a", "proposed"), ("r1-final-b", "proposed")]

    service.confirm_assessment(user_id)
    first_authority = {
        "expected_refinement_journey_id": journey_id,
        "expected_round_number": 1,
        "expected_assessment_fingerprint": regenerated.input_fingerprint,
        "expected_assessment_authority_token": regenerated.assessment_authority_token,
    }
    for selected_keys in ([], ["r1-final-a"]):
        with pytest.raises(ValueError, match="authority changed"):
            service.select_refinement_areas(user_id, CandidateAdviserAreaSelectionRequest(**stale_authority, selected_area_keys=selected_keys))
    assert service._current_refinement_journey(user_id).state == "area_selection"
    service.select_refinement_areas(user_id, CandidateAdviserAreaSelectionRequest(**first_authority, selected_area_keys=["r1-final-a"]))
    with pytest.raises(ValueError, match="authority changed"):
        service.generate_round_questions(user_id, CandidateAdviserQuestionGenerationRequest(**stale_authority))
    service.generate_round_questions(user_id, CandidateAdviserQuestionGenerationRequest(**first_authority))
    first_question = service._round_question_records(user_id, journey_id, 1)[0]
    first_question.status = "confirmed"
    first_question.answer_text = "Synthetic confirmed answer"
    first_question.interpretation_json = json.dumps({"answer_kind": "preference_intent", "confirmed_context_summary": "Synthetic Round-1 history.", "proposed_evidence": []})
    first_question.confirmed_at = datetime.now(timezone.utc)
    db_session.commit()

    service.assess(user_id)
    assert service._current_refinement_journey(user_id).state == "round1_assessment_review"
    assert [area.area_key for area in service._journey_areas(user_id, journey_id) if area.round_number == 2] == ["r2-old-a", "r2-old-b"]
    round1_review = service.get_assessment(user_id)
    round1_regenerated = service.assess(user_id, regenerate=True)
    assert round1_review is not None
    assert round1_regenerated.input_fingerprint == round1_review.input_fingerprint
    assert round1_regenerated.assessment_authority_token != round1_review.assessment_authority_token
    assert service._current_refinement_journey(user_id).journey_key == journey_id
    assert service._current_refinement_journey(user_id).rounds_completed == 1
    assert [area.area_key for area in service._journey_areas(user_id, journey_id) if area.round_number == 2] == ["r2-next-a", "r2-next-b"]
    round1_history = [area for area in service._journey_areas(user_id, journey_id) if area.round_number == 1]
    assert [(area.area_key, area.selection_state) for area in round1_history] == [("r1-final-a", "selected"), ("r1-final-b", "skipped")]
    assert first_question.status == "confirmed"

    service.confirm_assessment(user_id)
    second_authority = {
        "expected_refinement_journey_id": journey_id,
        "expected_round_number": 2,
        "expected_assessment_fingerprint": round1_regenerated.input_fingerprint,
        "expected_assessment_authority_token": round1_regenerated.assessment_authority_token,
    }
    service.select_refinement_areas(user_id, CandidateAdviserAreaSelectionRequest(**second_authority, selected_area_keys=["r2-next-a"]))
    service.generate_round_questions(user_id, CandidateAdviserQuestionGenerationRequest(**second_authority))
    second_question = service._round_question_records(user_id, journey_id, 2)[0]
    second_question.status = "confirmed"
    second_question.answer_text = "Synthetic final-round answer"
    second_question.interpretation_json = json.dumps({"answer_kind": "preference_intent", "confirmed_context_summary": "Synthetic Round-2 history.", "proposed_evidence": []})
    second_question.confirmed_at = datetime.now(timezone.utc)
    db_session.commit()

    final_review = service.assess(user_id)
    journey = service._current_refinement_journey(user_id)
    assert journey.state == "round2_assessment_review" and journey.rounds_completed == 2
    assert final_review.content.clarification_areas == []
    with pytest.raises(ValueError, match="Clarification areas are disabled after Round 2"):
        service.assess(user_id, regenerate=True)
    assert service._current_refinement_journey(user_id).journey_key == journey_id
    final_regenerated = service.assess(user_id, regenerate=True)
    assert final_regenerated.content.clarification_areas == []
    assert final_regenerated.assessment_authority_token == final_review.assessment_authority_token
    assert service._current_refinement_journey(user_id).journey_key == journey_id
    assert service._current_refinement_journey(user_id).rounds_completed == 2
    assert assessment_calls == 8
    assert len(db_session.scalars(select(CandidateAdviserRefinementJourneyRecord).where(
        CandidateAdviserRefinementJourneyRecord.user_id == user_id,
        CandidateAdviserRefinementJourneyRecord.state != "superseded",
    )).all()) == 1
    assert {row.id for row in service._round_question_records(user_id, journey_id, 1)} == {first_question.id}
    assert {row.id for row in service._round_question_records(user_id, journey_id, 2)} == {second_question.id}
    assert not [area for area in service._journey_areas(user_id, journey_id) if area.round_number == 2 and area.selection_state == "proposed"]


def test_refinement_mutations_reject_stale_journey_round_and_assessment_authority(db_session) -> None:
    user_id = _user(db_session, "adviser-refinement-mutation-authority@example.com")
    _confirmed_cv(db_session, user_id)

    class _Agent:
        calls = 0

        def assess(self, *, semantic_input):
            self.calls += 1
            area = AdviserClarificationArea(
                area_key="delivery", title=f"Area {self.calls}", rationale="Clarify this.",
                source_references=[{"source_type": "intake", "reference": "career_direction"}],
            )
            return _content().model_copy(update={"clarification_areas": [area], "open_questions": []})

    class _Questions:
        calls = 0

        def generate(self, *, generation_input):
            self.calls += 1
            return ProviderRoundQuestionSet.model_validate({"area_groups": [{
                "area_key": generation_input.selected_areas[0].area_key,
                "questions": [{"question_key": "q", "text": "What did you do?", "source_references": [{"source_type": "intake", "reference": "career_direction"}], "suggested_answers": ["I led", "I contributed", "I observed"]}],
            }]})

    questions = _Questions()
    service = CandidateAdviserService(db_session, agent=_Agent(), question_generator=questions)
    service.save_intake(user_id, _intake())
    first = service.assess(user_id)
    first_journey_id = service._current_refinement_journey(user_id).journey_key
    service.confirm_assessment(user_id)
    service.save_intake(user_id, _intake().model_copy(update={"career_direction": "Changed outside the refinement journey."}))
    second = service.assess(user_id)
    second_journey_id = service._current_refinement_journey(user_id).journey_key
    assert second_journey_id != first_journey_id
    service.confirm_assessment(user_id)
    current_authority = {
        "expected_refinement_journey_id": second_journey_id,
        "expected_round_number": 1,
        "expected_assessment_fingerprint": second.input_fingerprint,
        "expected_assessment_authority_token": second.assessment_authority_token,
    }
    for stale in (
        {**current_authority, "expected_refinement_journey_id": first_journey_id},
        {**current_authority, "expected_round_number": 2},
        {**current_authority, "expected_assessment_fingerprint": "f" * 64},
        {**current_authority, "expected_assessment_authority_token": "f" * 64},
    ):
        with pytest.raises(ValueError, match="authority changed"):
            service.select_refinement_areas(user_id, CandidateAdviserAreaSelectionRequest(**stale, selected_area_keys=[]))
    assert service._current_refinement_journey(user_id).state == "area_selection"

    service.select_refinement_areas(user_id, CandidateAdviserAreaSelectionRequest(**current_authority, selected_area_keys=["delivery"]))
    with pytest.raises(ValueError, match="authority changed"):
        service.generate_round_questions(user_id, CandidateAdviserQuestionGenerationRequest(**{**current_authority, "expected_refinement_journey_id": first_journey_id}))
    assert questions.calls == 0
    valid_generation = CandidateAdviserQuestionGenerationRequest(**current_authority)
    first_result = service.generate_round_questions(user_id, valid_generation)
    retried_result = service.generate_round_questions(user_id, valid_generation)
    assert questions.calls == 1
    assert [row.clarification_id for row in first_result] == [row.clarification_id for row in retried_result]


def test_full_thirty_answer_round_survives_reassessment_and_round_two_question_generation(db_session) -> None:
    user_id = _user(db_session, "adviser-thirty-confirmations@example.com")
    _confirmed_cv(db_session, user_id)
    round1_keys = [f"r1-area-{index}" for index in range(6)]
    round2_key = "r2-area-followup"

    class _Agent:
        calls = 0
        semantic_inputs = []

        def assess(self, *, semantic_input):
            self.calls += 1
            self.semantic_inputs.append(semantic_input)
            keys = round1_keys if self.calls == 1 else [round2_key]
            areas = [AdviserClarificationArea(
                area_key=key, title=f"Area {key}", rationale="Clarify a material uncertainty.",
                source_references=[{"source_type": "intake", "reference": "career_direction"}],
            ) for key in keys]
            return _content().model_copy(update={"clarification_areas": areas, "open_questions": []})

    class _Questions:
        inputs = []

        def generate(self, *, generation_input):
            self.inputs.append(generation_input)
            return ProviderRoundQuestionSet.model_validate({"area_groups": [{
                "area_key": area.area_key,
                "questions": [{
                    "question_key": f"{generation_input.round_number}-{area.area_key}-{slot}",
                    "text": f"Synthetic question {generation_input.round_number} {area.area_key} {slot}?",
                    "source_references": [{"source_type": "intake", "reference": "career_direction"}],
                    "suggested_answers": ["I led it", "I contributed", "I observed it"],
                } for slot in range(5)],
            } for area in generation_input.selected_areas]})

    class _Interpreter:
        calls = 0

        def interpret(self, *, interpretation_input):
            kinds = ["preference_intent", "eligibility_fact", "insufficient"]
            kind = kinds[self.calls % len(kinds)]
            summary = f"Round-1 confirmed summary {self.calls}"
            self.calls += 1
            return ClarificationInterpretation(answer_kind=kind, confirmed_context_summary=summary, proposed_evidence=[])

    agent = _Agent()
    question_agent = _Questions()
    service = CandidateAdviserService(db_session, agent=agent, question_generator=question_agent, clarification_interpreter=_Interpreter())
    service.save_intake(user_id, _intake())
    first_draft = service.assess(user_id)
    journey_id = service._current_refinement_journey(user_id).journey_key
    first_authority = {
        "expected_refinement_journey_id": journey_id,
        "expected_round_number": 1,
        "expected_assessment_fingerprint": first_draft.input_fingerprint,
        "expected_assessment_authority_token": first_draft.assessment_authority_token,
    }
    service.confirm_assessment(user_id)
    service.select_refinement_areas(user_id, CandidateAdviserAreaSelectionRequest(**first_authority, selected_area_keys=round1_keys))
    first_questions = service.generate_round_questions(user_id, CandidateAdviserQuestionGenerationRequest(**first_authority))
    assert len(first_questions) == 30

    for index, read in enumerate(first_questions):
        answer = CandidateAdviserClarificationAnswer(
            selected_option_ids=[read.suggested_answers[0].option_id],
            custom_answer_text="",
            special_selection=None,
        )
        service.answer_clarification(user_id, read.clarification_id, answer)
        service.confirm_clarification(user_id, read.clarification_id)

    round1_reassessment = service.assess(user_id)
    round1_input = agent.semantic_inputs[-1]
    assert len(round1_input.clarifications) == 30
    assert {item.confirmed_context_summary for item in round1_input.clarifications} == {f"Round-1 confirmed summary {index}" for index in range(30)}
    assert {item.answer_kind for item in round1_input.clarifications} >= {"preference_intent", "eligibility_fact", "insufficient"}
    persisted_round1 = service._round_question_records(user_id, journey_id, 1)
    assert all(not json.loads(item.interpretation_json)["proposed_evidence"] for item in persisted_round1)

    service.confirm_assessment(user_id)
    round2_authority = {
        "expected_refinement_journey_id": journey_id,
        "expected_round_number": 2,
        "expected_assessment_fingerprint": round1_reassessment.input_fingerprint,
        "expected_assessment_authority_token": round1_reassessment.assessment_authority_token,
    }
    service.select_refinement_areas(user_id, CandidateAdviserAreaSelectionRequest(**round2_authority, selected_area_keys=[round2_key]))
    service.generate_round_questions(user_id, CandidateAdviserQuestionGenerationRequest(**round2_authority))
    round2_input = question_agent.inputs[-1]
    assert round2_input.round_number == 2
    assert len(round2_input.clarifications) == 30
    assert {item.confirmed_context_summary for item in round2_input.clarifications} == {f"Round-1 confirmed summary {index}" for index in range(30)}

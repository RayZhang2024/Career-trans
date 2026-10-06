from datetime import datetime, timezone

import json

from sqlalchemy import event, select

from app.models.candidate_adviser import CandidateAdviserAssessmentRecord, CandidateAdviserClarificationRecord
from app.models.candidate_adviser_profile_proposal import CandidateAdviserEnrichmentRecord
from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.models.user import User
from app.core.config import Settings
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessmentContent,
    CandidateAdviserClarificationAnswer,
    CandidateAdviserClarificationInterpretationInput,
    CandidateAdviserIntake,
    ClarificationAnswerKind,
    ClarificationInterpretation,
    ClarificationProposedEvidence,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.cv_overlap_review import (
    CVOverlapResolution,
    CVOverlapResolutionAction,
    CVOverlapReviewPatch,
)
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_adviser_journey_service import CandidateAdviserJourneyService
from app.services.profile_revision_service import CandidateProfileRevisionService
from app.services.cv_overlap_review_service import CVOverlapReviewService
from app.services.cv_ingestion_service import CVIngestionService
from tests.test_profile import auth_header, register_and_login


def _adviser_content() -> dict[str, object]:
    insight = {
        "text": "Synthetic source-grounded assessment.",
        "source_references": [{"source_type": "intake", "reference": "career_direction"}],
    }
    return {
        "professional_positioning": insight,
        "transferable_strengths": [],
        "development_gaps": [],
        "role_hypotheses": [],
        "transition_assessment": insight,
        "open_questions": [],
        "career_strategy_summary": insight,
        "job_search_strategy_summary": insight,
    }


def _assessment_state(db_session, user_id: str) -> CandidateAdviserService:
    data = CandidateCVData.model_validate({
        "employment": [{"employer": "Example", "title": "Engineer", "description": "Built systems."}],
        "evidence": [{"evidence_type": "project", "title": "Delivery", "text": "Delivered a reliable system.", "skills": ["Python"]}],
    })
    db_session.add(CandidateStructuredProfile(user_id=user_id, structured_json=data.model_dump_json()))
    db_session.commit()
    ActiveCandidateEvidenceResolver(db_session).resolve(user_id, data)
    db_session.commit()
    service = CandidateAdviserService(db_session)
    service.save_intake(user_id, CandidateAdviserIntake(career_direction="Applied AI delivery"))
    fingerprint = service.input_fingerprint(user_id)
    db_session.add(CandidateAdviserAssessmentRecord(
        user_id=user_id,
        input_fingerprint=fingerprint,
        status="confirmed",
        assessment_json=json.dumps(_adviser_content()),
    ))
    db_session.commit()
    return service


class _Adviser:
    def __init__(self, *, question: str | None = None) -> None:
        self.question = question
        self.calls = 0

    def assess(self, *, semantic_input):
        self.calls += 1
        references = [{"source_type": "intake", "reference": "career_direction"}]
        if semantic_input.career_evidence:
            references.append({"source_type": "career_evidence", "reference": semantic_input.career_evidence[0].evidence_id})
        insight = {"text": "Synthetic grounded assessment.", "source_references": references}
        return CandidateAdviserAssessmentContent.model_validate({
            "professional_positioning": insight,
            "transferable_strengths": [], "development_gaps": [], "role_hypotheses": [],
            "transition_assessment": insight,
            "open_questions": ([{"text": self.question, "source_references": references, "suggested_answers": ["I led the work", "I contributed to the work", "I supported the work"]}] if self.question else []),
            "career_strategy_summary": insight, "job_search_strategy_summary": insight,
        })


class _Interpreter:
    def interpret(self, *, interpretation_input: CandidateAdviserClarificationInterpretationInput) -> ClarificationInterpretation:
        return ClarificationInterpretation(
            answer_kind=ClarificationAnswerKind.CAREER_FACT,
            confirmed_context_summary="Synthetic confirmed factual context.",
            proposed_evidence=[ClarificationProposedEvidence(
                fact_domain="career", evidence_type="project", title="Confirmed delivery",
                text="Confirmed a synthetic delivery fact.", skills=["Python"],
            )],
        )


def _confirm_cv(db_session, user_id: str, text: str) -> None:
    data = CandidateCVData.model_validate({
        "employment": [{"employer": "Example", "title": "Engineer", "description": text}],
        "evidence": [{"evidence_type": "project", "title": "Delivery", "text": text, "skills": ["Python"]}],
    })
    ingestion = CVIngestionService(db_session)
    draft = ingestion.upload(user_id, [("cv.json", "application/json", json.dumps(data.model_dump(mode="json")).encode())])
    ingestion.interpret(user_id, draft.id)
    review_service = CVOverlapReviewService(db_session)
    review = review_service.read(user_id, draft.id)
    resolutions = []
    for item in review.items:
        if not item.resolution_required:
            continue
        if item.relationship.value == "ambiguous":
            resolutions.append(CVOverlapResolution(
                item_key=item.item_key,
                action=CVOverlapResolutionAction.REPLACE_CURRENT,
                target_fingerprint=item.candidate_matches[0].fingerprint,
            ))
        else:
            resolutions.append(CVOverlapResolution(
                item_key=item.item_key,
                action=CVOverlapResolutionAction.REPLACE_CURRENT,
                target_fingerprint=item.target_fingerprint,
            ))
    if resolutions:
        review_service.update(user_id, draft.id, CVOverlapReviewPatch(
            expected_review_revision=review.revision,
            expected_base_structured_fingerprint=review.base_structured_fingerprint,
            expected_draft_fingerprint=review.draft_fingerprint,
            resolutions=resolutions,
        ))
    ingestion.confirm(user_id, draft.id)


def test_onboarding_status_is_user_scoped_and_pure(client, db_session):
    token_a = register_and_login(client, "onboarding-a@example.com")
    token_b = register_and_login(client, "onboarding-b@example.com")
    user_a = db_session.query(User).filter_by(email="onboarding-a@example.com").one()
    db_session.add(CandidateProfile(user_id=user_a.id, headline="Synthetic profile"))
    db_session.add(CandidateStructuredProfile(user_id=user_a.id, structured_json=CandidateCVData().model_dump_json()))
    db_session.commit()
    writes: list[str] = []

    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", observe)
    try:
        response_a = client.get("/api/v1/onboarding/status", headers=auth_header(token_a))
        response_b = client.get("/api/v1/onboarding/status", headers=auth_header(token_b))
    finally:
        event.remove(db_session.bind, "before_cursor_execute", observe)
    assert response_a.status_code == 200
    assert response_a.json()["profile_exists"] is True
    assert response_a.json()["candidate_context_ready"] is True
    assert response_b.json()["profile_exists"] is False
    assert response_b.json()["candidate_context_ready"] is False
    assert writes == []


def test_onboarding_latest_draft_uses_created_time_not_updated_time(client, db_session):
    token = register_and_login(client, "draft-order@example.com")
    user = db_session.query(User).filter_by(email="draft-order@example.com").one()
    older = CandidateCVIngestionDraft(user_id=user.id, state="confirmed", documents_json="[]")
    newer = CandidateCVIngestionDraft(user_id=user.id, state="review_ready", documents_json="[]")
    older.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    newer.created_at = datetime(2025, 2, 1, tzinfo=timezone.utc)
    older.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.add_all([older, newer]); db_session.commit()
    response = client.get("/api/v1/onboarding/status", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["latest_cv_draft"]["id"] == newer.id
    assert response.json()["latest_cv_draft"]["state"] == "review_ready"


def test_default_cors_supports_both_documented_vite_origins():
    origins = str(Settings.model_fields["cors_origins"].default).split(",")
    assert "http://localhost:5173" in origins
    assert "http://127.0.0.1:5173" in origins


def test_onboarding_status_with_assessment_is_provider_free_read_only_and_uses_exact_fingerprint(client, db_session, monkeypatch):
    token = register_and_login(client, "onboarding-assessment@example.com")
    user = db_session.query(User).filter_by(email="onboarding-assessment@example.com").one()
    service = _assessment_state(db_session, user.id)
    normal = service.input_fingerprint(user.id)
    read_only = service.input_fingerprint(user.id, read_only=True)
    assert normal == read_only
    service.save_intake(user.id, CandidateAdviserIntake(career_direction="Changed direction"))
    assert service.get_assessment(user.id).status == "stale"
    assert service.input_fingerprint(user.id, read_only=True) != normal
    service.save_intake(user.id, CandidateAdviserIntake(career_direction="Applied AI delivery"))
    assert service.input_fingerprint(user.id) == service.input_fingerprint(user.id, read_only=True) == normal
    assert service.get_assessment(user.id).status == "confirmed"
    assert db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user.id)).all()

    # A status route must not construct an LLM client or materialise question
    # rows merely to render a read model.
    monkeypatch.setattr(CandidateAdviserService, "list_clarifications", lambda *_args: (_ for _ in ()).throw(AssertionError("must not materialise clarifications")))
    monkeypatch.setattr("app.api.deps.get_user_candidate_adviser_service", lambda: (_ for _ in ()).throw(AssertionError("provider-backed dependency must not be used")))
    writes: list[str] = []
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)
    event.listen(db_session.bind, "before_cursor_execute", observe)
    try:
        response = client.get("/api/v1/onboarding/status", headers=auth_header(token))
    finally:
        event.remove(db_session.bind, "before_cursor_execute", observe)
    assert response.status_code == 200
    assert response.json()["adviser"]["assessment_status"] == "confirmed"
    assert writes == []


def test_shared_journey_projection_reports_followup_without_materializing_clarifications(client, db_session, monkeypatch):
    token = register_and_login(client, "journey-followup@example.com")
    user = db_session.query(User).filter_by(email="journey-followup@example.com").one()
    service = _assessment_state(db_session, user.id)
    record = db_session.scalar(select(CandidateAdviserAssessmentRecord).where(
        CandidateAdviserAssessmentRecord.user_id == user.id
    ))
    payload = _adviser_content()
    payload["open_questions"] = [{
        "text": "What delivery outcome should we understand?",
        "source_references": [{"source_type": "intake", "reference": "career_direction"}],
    }]
    record.assessment_json = json.dumps(payload)
    record.input_fingerprint = service.input_fingerprint(user.id)
    db_session.commit()
    monkeypatch.setattr(CandidateAdviserService, "list_clarifications", lambda *_args: (_ for _ in ()).throw(AssertionError("shared read must not materialize clarifications")))
    writes: list[str] = []

    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", observe)
    try:
        response = client.get("/api/v1/onboarding/status", headers=auth_header(token))
    finally:
        event.remove(db_session.bind, "before_cursor_execute", observe)
    journey = response.json()["adviser"]["journey"]
    assert response.status_code == 200
    assert journey["confirmed_guidance_active"] is True
    assert journey["current_follow_up_available"] is True
    assert journey["next_action"] == "find_jobs"
    assert journey["job_search_ready"] is True
    assert db_session.scalars(select(CandidateAdviserClarificationRecord).where(
        CandidateAdviserClarificationRecord.user_id == user.id
    )).all() == []
    assert writes == []


def test_journey_next_action_prioritizes_enrichment_and_oldest_unresolved_first(db_session):
    from datetime import timedelta

    from app.schemas.candidate_adviser_journey import AdviserNextAction

    user = User(email="journey-order@example.com", password_hash="unused")
    db_session.add(user); db_session.commit()
    _assessment_state(db_session, user.id)
    assessment_record = db_session.scalar(select(CandidateAdviserAssessmentRecord).where(
        CandidateAdviserAssessmentRecord.user_id == user.id
    ))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for clarification_id, confirmed_at in (("b" * 64, now + timedelta(days=1)), ("a" * 64, now)):
        db_session.add(CandidateAdviserClarificationRecord(
            user_id=user.id, clarification_id=clarification_id,
            question_key=clarification_id, origin_assessment_fingerprint=assessment_record.input_fingerprint,
            question_text="Confirmed career fact", question_source_references_json="[]",
            priority_index=0, answer_text="Synthetic factual answer",
            interpretation_json=json.dumps({
                "answer_kind": "career_fact", "confirmed_context_summary": "Synthetic fact",
                "proposed_evidence": [],
            }), status="confirmed", confirmed_at=confirmed_at,
        ))
    db_session.commit()
    # An active draft is exposed to prevent a known proposal-transfer dead end.
    CandidateProfileRevisionService(db_session).create_or_resume(user.id)
    journey = CandidateAdviserJourneyService(db_session).read(user.id)
    assert journey.assessment_status == "stale"
    assert journey.unresolved_profile_enrichment_count == 2
    assert journey.next_enrichment_clarification_id == "a" * 64
    assert journey.next_action is AdviserNextAction.REVIEW_PROFILE_ENRICHMENT
    assert journey.active_profile_draft is True


def test_journey_next_action_precedence_keeps_adviser_optional_and_job_search_separate():
    from app.schemas.candidate_adviser import CandidateAdviserAssessmentStatus
    from app.schemas.candidate_adviser_journey import AdviserNextAction

    precedence = CandidateAdviserJourneyService._next_action
    assert precedence(candidate_context_ready=False, intake_exists=False, assessment_status=None, clarification_review_pending=False, pending_enrichment=False)[0] is AdviserNextAction.COMPLETE_PROFILE
    assert precedence(candidate_context_ready=True, intake_exists=False, assessment_status=None, clarification_review_pending=False, pending_enrichment=False)[0] is AdviserNextAction.START_INTAKE
    assert precedence(candidate_context_ready=True, intake_exists=True, assessment_status=CandidateAdviserAssessmentStatus.REVIEW_READY, clarification_review_pending=True, pending_enrichment=True)[0] is AdviserNextAction.REVIEW_ASSESSMENT
    assert precedence(candidate_context_ready=True, intake_exists=True, assessment_status=CandidateAdviserAssessmentStatus.CONFIRMED, clarification_review_pending=True, pending_enrichment=True)[0] is AdviserNextAction.CONFIRM_CLARIFICATION


def test_onboarding_status_counts_only_persisted_confirmed_clarifications_and_never_creates_them(client, db_session):
    token = register_and_login(client, "onboarding-clarification-count@example.com")
    user = db_session.query(User).filter_by(email="onboarding-clarification-count@example.com").one()
    _assessment_state(db_session, user.id)
    for index, state in enumerate(("confirmed", "review_ready", "unanswered")):
        db_session.add(CandidateAdviserClarificationRecord(
            user_id=user.id,
            clarification_id=f"{index + 1:064x}", question_key=f"{index + 4:064x}",
            origin_assessment_fingerprint="a" * 64, question_text=f"Question {index}",
            question_source_references_json="[]", priority_index=index, status=state,
        ))
    db_session.commit()
    before = [
        (record.id, record.clarification_id, record.status)
        for record in db_session.scalars(
            select(CandidateAdviserClarificationRecord)
            .where(CandidateAdviserClarificationRecord.user_id == user.id)
            .order_by(CandidateAdviserClarificationRecord.id)
        )
    ]
    response = client.get("/api/v1/onboarding/status", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["adviser"]["confirmed_clarification_count"] == 1
    after = [
        (record.id, record.clarification_id, record.status)
        for record in db_session.scalars(
            select(CandidateAdviserClarificationRecord)
            .where(CandidateAdviserClarificationRecord.user_id == user.id)
            .order_by(CandidateAdviserClarificationRecord.id)
        )
    ]
    assert after == before


def test_onboarding_readiness_is_structured_profile_existence_not_draft_or_profile_completeness(client, db_session):
    token = register_and_login(client, "onboarding-readiness@example.com")
    user = db_session.query(User).filter_by(email="onboarding-readiness@example.com").one()
    db_session.add(CandidateProfile(user_id=user.id))
    db_session.add(CandidateCVIngestionDraft(user_id=user.id, state="review_ready", documents_json="[]"))
    db_session.commit()
    first = client.get("/api/v1/onboarding/status", headers=auth_header(token)).json()
    assert first["profile_exists"] is True
    assert first["candidate_context_ready"] is False
    db_session.add(CandidateStructuredProfile(user_id=user.id, structured_json=CandidateCVData().model_dump_json()))
    db_session.commit()
    second = client.get("/api/v1/onboarding/status", headers=auth_header(token)).json()
    assert second["candidate_context_ready"] is True
    assert second["latest_cv_draft"]["state"] == "review_ready"


def test_onboarding_latest_draft_breaks_equal_timestamp_ties_by_id(client, db_session):
    token = register_and_login(client, "draft-tie@example.com")
    user = db_session.query(User).filter_by(email="draft-tie@example.com").one()
    same_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    low = CandidateCVIngestionDraft(id="10000000-0000-0000-0000-000000000000", user_id=user.id, state="uploaded", documents_json="[]", created_at=same_time)
    high = CandidateCVIngestionDraft(id="f0000000-0000-0000-0000-000000000000", user_id=user.id, state="review_ready", documents_json="[]", created_at=same_time)
    db_session.add_all([low, high]); db_session.commit()
    response = client.get("/api/v1/onboarding/status", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["latest_cv_draft"]["id"] == high.id


def test_confirmed_cv_change_has_exact_normal_read_only_and_onboarding_stale_parity(client, db_session):
    token = register_and_login(client, "onboarding-cv-parity@example.com")
    user = db_session.query(User).filter_by(email="onboarding-cv-parity@example.com").one()
    _confirm_cv(db_session, user.id, "Delivered the authoritative CV-A system.")
    adviser = _Adviser()
    service = CandidateAdviserService(db_session, agent=adviser)
    service.save_intake(user.id, CandidateAdviserIntake(career_direction="Applied AI delivery"))
    assessment_a = service.assess(user.id)
    service.confirm_assessment(user.id)
    assert assessment_a.input_fingerprint == service.input_fingerprint(user.id) == service.input_fingerprint(user.id, read_only=True)
    assert client.get("/api/v1/onboarding/status", headers=auth_header(token)).json()["adviser"]["assessment_status"] == "confirmed"

    _confirm_cv(db_session, user.id, "Delivered the authoritative CV-B system with a changed factual scope.")
    normal_b = service.input_fingerprint(user.id)
    read_only_b = service.input_fingerprint(user.id, read_only=True)
    assert normal_b != assessment_a.input_fingerprint
    assert normal_b == read_only_b
    assert service.get_assessment(user.id).status == "stale"
    assert client.get("/api/v1/onboarding/status", headers=auth_header(token)).json()["adviser"]["assessment_status"] == "stale"

    record = db_session.scalar(select(CandidateAdviserAssessmentRecord).where(CandidateAdviserAssessmentRecord.user_id == user.id))
    assert record is not None and record.status == "confirmed"

    assessment_b = service.assess(user.id)
    confirmed_b = service.confirm_assessment(user.id)
    assert assessment_b.input_fingerprint == confirmed_b.input_fingerprint == normal_b == service.input_fingerprint(user.id, read_only=True)
    assert service.get_assessment(user.id).status == "confirmed"
    assert client.get("/api/v1/onboarding/status", headers=auth_header(token)).json()["adviser"]["assessment_status"] == "confirmed"


def test_stale_bounded_assessment_routes_to_update_unless_clarification_session_is_active() -> None:
    from app.schemas.candidate_adviser import CandidateAdviserAssessmentStatus
    from app.schemas.candidate_adviser_journey import AdviserNextAction

    common = dict(
        candidate_context_ready=True,
        intake_exists=True,
        assessment_status=CandidateAdviserAssessmentStatus.STALE,
        clarification_review_pending=False,
        pending_enrichment=False,
        bounded_contract=True,
        refinement_state="area_selection",
    )
    action, _ = CandidateAdviserJourneyService._next_action(
        **common, clarification_session_active=False, clarification_unanswered=False,
    )
    assert action is AdviserNextAction.UPDATE_ASSESSMENT
    action, _ = CandidateAdviserJourneyService._next_action(
        **{**common, "refinement_state": "questions_active"},
        clarification_session_active=True, clarification_unanswered=True,
    )
    assert action is AdviserNextAction.ANSWER_CLARIFICATION


def test_confirmed_clarification_has_exact_normal_read_only_and_onboarding_stale_parity(client, db_session):
    token = register_and_login(client, "onboarding-clarification-parity@example.com")
    user = db_session.query(User).filter_by(email="onboarding-clarification-parity@example.com").one()
    _confirm_cv(db_session, user.id, "Delivered the initial synthetic system.")
    service = CandidateAdviserService(db_session, agent=_Adviser(question="What delivery fact should be confirmed?"), clarification_interpreter=_Interpreter())
    service.save_intake(user.id, CandidateAdviserIntake(career_direction="Applied AI delivery"))
    legacy_content = service._semantic_agent().assess(semantic_input=service._semantic_input(user.id))
    assessment_record = CandidateAdviserAssessmentRecord(
        user_id=user.id,
        input_fingerprint=service.input_fingerprint(user.id),
        contract_version="legacy_questions",
        status="review_ready",
        assessment_json=json.dumps(legacy_content.model_dump(mode="json"), sort_keys=True),
    )
    db_session.add(assessment_record)
    db_session.commit()
    assessment = service.get_assessment(user.id)
    service.confirm_assessment(user.id)
    clarification = service.list_clarifications(user.id)[0]
    service.answer_clarification(user.id, clarification.clarification_id, CandidateAdviserClarificationAnswer(
        selected_option_ids=[clarification.suggested_answers[0].option_id],
        custom_answer_text="Synthetic confirmation.",
        special_selection=None,
    ))
    service.confirm_clarification(user.id, clarification.clarification_id)

    normal = service.input_fingerprint(user.id)
    read_only = service.input_fingerprint(user.id, read_only=True)
    assert normal != assessment.input_fingerprint
    assert normal == read_only
    assert service.get_assessment(user.id).status == "stale"
    assert client.get("/api/v1/onboarding/status", headers=auth_header(token)).json()["adviser"]["assessment_status"] == "stale"

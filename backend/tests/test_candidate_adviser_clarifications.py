import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.candidate_adviser import CandidateAdviserAssessmentRecord, CandidateAdviserClarificationRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.user import User
from app.schemas.candidate_adviser import (
    AdviserInsight,
    CandidateAdviserAssessmentContent,
    CandidateAdviserAssessmentRead,
    CandidateAdviserClarificationAnswer,
    CandidateAdviserIntake,
    ClarificationAnswerKind,
    ClarificationInterpretation,
    ClarificationProposedEvidence,
    CandidateAdviserSemanticInput,
)
from app.schemas.candidate import CareerEvidenceProvenance
from app.schemas.cv_ingestion import CandidateCVData
from app.services.candidate_adviser_service import CandidateAdviserService
from app.agents.candidate_adviser_clarification import SemanticCandidateAdviserClarificationInterpreter
from app.providers.openai_structured_output import strict_schema_from_pydantic_model


def _user(session, email: str) -> str:
    user = User(email=email, password_hash="unused")
    session.add(user)
    session.commit()
    return user.id


def _ready_profile(session, user_id: str) -> None:
    session.add(CandidateStructuredProfile(
        user_id=user_id,
        structured_json=json.dumps(CandidateCVData().model_dump(mode="json")),
    ))
    session.commit()


def _intake() -> CandidateAdviserIntake:
    return CandidateAdviserIntake(career_direction="Synthetic applied engineering direction.")


def _insight(text: str) -> AdviserInsight:
    return AdviserInsight(text=text, source_references=[{"source_type": "intake", "reference": "career_direction"}])


def _content(*questions: str) -> CandidateAdviserAssessmentContent:
    insight = _insight("Synthetic grounded adviser insight.")
    return CandidateAdviserAssessmentContent(
        professional_positioning=insight,
        transferable_strengths=[], development_gaps=[], role_hypotheses=[],
        transition_assessment=insight,
        open_questions=[_insight(question) for question in questions],
        career_strategy_summary=insight,
        job_search_strategy_summary=insight,
    )


class _Adviser:
    def __init__(self, *questions: str) -> None:
        self.content = _content(*questions)
        self.calls = 0

    def assess(self, *, semantic_input):
        self.calls += 1
        self.input = semantic_input
        return self.content


class _Interpreter:
    def __init__(self, interpretation: ClarificationInterpretation) -> None:
        self.interpretation = interpretation
        self.calls = 0

    def interpret(self, *, question_text: str, answer_text: str) -> ClarificationInterpretation:
        self.calls += 1
        self.question_text = question_text
        self.answer_text = answer_text
        return self.interpretation


def _career_fact() -> ClarificationInterpretation:
    return ClarificationInterpretation(
        answer_kind=ClarificationAnswerKind.CAREER_FACT,
        confirmed_context_summary="Candidate confirmed a positive synthetic delivery fact.",
        proposed_evidence=[ClarificationProposedEvidence(
            fact_domain="career", evidence_type="project", title="Synthetic delivery", text="Delivered a synthetic production system.", skills=["Python"],
        )],
    )


def _confirmed_service(session, user_id: str, adviser: _Adviser, interpreter: _Interpreter) -> CandidateAdviserService:
    service = CandidateAdviserService(session, agent=adviser, clarification_interpreter=interpreter)
    service.save_intake(user_id, _intake())
    service.assess(user_id)
    service.confirm_assessment(user_id)
    return service


def test_confirmed_career_clarification_is_the_only_transition_to_active_evidence_and_staleness(db_session) -> None:
    user_id = _user(db_session, "clarification-career@example.com")
    _ready_profile(db_session, user_id)
    adviser = _Adviser("What positive delivery work should be represented?")
    interpreter = _Interpreter(_career_fact())
    service = _confirmed_service(db_session, user_id, adviser, interpreter)

    listed = service.list_clarifications(user_id)
    assert len(listed) == 1 and listed[0].status == "unanswered"
    clarification = service.answer_clarification(user_id, listed[0].clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic answer."))
    assert clarification.status == "review_ready"
    assert interpreter.calls == 1
    assert db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all() == []

    confirmed = service.confirm_clarification(user_id, clarification.clarification_id)
    assert confirmed.status == "confirmed"
    assert service.confirm_clarification(user_id, clarification.clarification_id).status == "confirmed"
    active = db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all()
    assert len(active) == 1
    provenance = json.loads(active[0].provenance_json)
    assert provenance == [{"source_kind": "user_confirmed", "document_sha256": None, "segment_ids": [], "source_ref": f"clarification:{clarification.clarification_id}"}]
    assert service.get_assessment(user_id).status == "stale"
    assert service.list_clarifications(user_id) == []


def test_unconfirmed_siblings_become_noncurrent_after_one_confirmation(db_session) -> None:
    user_id = _user(db_session, "clarification-siblings@example.com")
    _ready_profile(db_session, user_id)
    service = _confirmed_service(db_session, user_id, _Adviser("Question A?", "Question B?"), _Interpreter(_career_fact()))
    first, second = service.list_clarifications(user_id)
    service.answer_clarification(user_id, first.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))
    service.confirm_clarification(user_id, first.clarification_id)

    with pytest.raises(ValueError, match="no longer current"):
        service.answer_clarification(user_id, second.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))


def test_confirmed_clarification_remains_authoritative_after_explicit_reassessment(db_session) -> None:
    user_id = _user(db_session, "clarification-durable-confirmation@example.com")
    _ready_profile(db_session, user_id)
    adviser = _Adviser("What delivery work did you own?", "What stakeholder scope did you own?")
    service = _confirmed_service(db_session, user_id, adviser, _Interpreter(_career_fact()))
    first, sibling = service.list_clarifications(user_id)
    service.answer_clarification(user_id, first.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))
    service.confirm_clarification(user_id, first.clarification_id)
    assert service.get_assessment(user_id).status == "stale"

    assessment_b = service.assess(user_id)
    assert assessment_b.status == "review_ready"
    confirmed_b = service.confirm_assessment(user_id)
    assert confirmed_b.status == "confirmed"

    original = db_session.scalar(select(CandidateAdviserClarificationRecord).where(
        CandidateAdviserClarificationRecord.user_id == user_id,
        CandidateAdviserClarificationRecord.clarification_id == first.clarification_id,
    ))
    assert original is not None
    assert original.status == "confirmed"
    assert original.origin_assessment_fingerprint != confirmed_b.input_fingerprint
    semantic_input = service._semantic_input(user_id)
    assert [item.clarification_id for item in semantic_input.clarifications] == [first.clarification_id]
    assert service.input_fingerprint(user_id) == confirmed_b.input_fingerprint
    active = db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all()
    assert len(active) == 1
    assert service._confirmed_clarification_state(user_id)[0]["clarification_id"] == first.clarification_id
    with pytest.raises(ValueError, match="no longer current"):
        service.answer_clarification(user_id, sibling.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))


@pytest.mark.parametrize("kind", [ClarificationAnswerKind.PREFERENCE_INTENT, ClarificationAnswerKind.ELIGIBILITY_FACT, ClarificationAnswerKind.INSUFFICIENT])
def test_noncareer_and_eligibility_clarifications_never_create_matching_evidence(db_session, kind) -> None:
    user_id = _user(db_session, f"clarification-{kind}@example.com")
    _ready_profile(db_session, user_id)
    interpretation = ClarificationInterpretation(answer_kind=kind, confirmed_context_summary="Synthetic confirmed context.", proposed_evidence=[])
    service = _confirmed_service(db_session, user_id, _Adviser("Synthetic question?"), _Interpreter(interpretation))
    clarification = service.list_clarifications(user_id)[0]
    service.answer_clarification(user_id, clarification.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic answer."))
    service.confirm_clarification(user_id, clarification.clarification_id)
    assert db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all() == []


def test_interpretation_kind_cannot_promote_evidence_when_not_career_or_mixed(db_session) -> None:
    user_id = _user(db_session, "clarification-invalid-kind@example.com")
    _ready_profile(db_session, user_id)
    invalid = ClarificationInterpretation(answer_kind=ClarificationAnswerKind.ELIGIBILITY_FACT, confirmed_context_summary="Context.", proposed_evidence=[ClarificationProposedEvidence(fact_domain="career", evidence_type="project", title="No", text="No", skills=[])])
    service = _confirmed_service(db_session, user_id, _Adviser("Synthetic?"), _Interpreter(invalid))
    clarification = service.list_clarifications(user_id)[0]
    with pytest.raises(ValueError, match="cannot propose"):
        service.answer_clarification(user_id, clarification.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))
    assert db_session.scalars(select(CandidateEvidenceRecord)).all() == []


def test_negative_capability_claim_cannot_be_promoted(db_session) -> None:
    user_id = _user(db_session, "clarification-negative@example.com")
    _ready_profile(db_session, user_id)
    negative = ClarificationInterpretation(
        answer_kind=ClarificationAnswerKind.CAREER_FACT,
        confirmed_context_summary="Candidate stated an absence.",
        proposed_evidence=[ClarificationProposedEvidence(fact_domain="career", evidence_type="project", title="Kubernetes", text="Never used Kubernetes.", skills=[])],
    )
    service = _confirmed_service(db_session, user_id, _Adviser("Synthetic?"), _Interpreter(negative))
    clarification = service.list_clarifications(user_id)[0]
    with pytest.raises(ValueError, match="Negative or absence"):
        service.answer_clarification(user_id, clarification.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))


def test_mixed_answer_excludes_eligibility_only_proposals(db_session) -> None:
    user_id = _user(db_session, "clarification-mixed-eligibility@example.com")
    _ready_profile(db_session, user_id)
    invalid = ClarificationInterpretation(
        answer_kind=ClarificationAnswerKind.MIXED,
        confirmed_context_summary="Mixed context.",
        proposed_evidence=[ClarificationProposedEvidence(fact_domain="career", evidence_type="other", title="Eligibility", text="Has right to work in the UK.", skills=[])],
    )
    service = _confirmed_service(db_session, user_id, _Adviser("Synthetic?"), _Interpreter(invalid))
    clarification = service.list_clarifications(user_id)[0]
    with pytest.raises(ValueError, match="Eligibility claims"):
        service.answer_clarification(user_id, clarification.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))


def test_clarification_schema_structurally_rejects_noncareer_evidence_domain() -> None:
    with pytest.raises(ValueError):
        ClarificationInterpretation.model_validate({
            "answer_kind": "mixed",
            "confirmed_context_summary": "Mixed context.",
            "proposed_evidence": [{
                "fact_domain": "eligibility",
                "evidence_type": "other",
                "title": "Eligibility",
                "text": "Has work authorisation.",
                "skills": [],
            }],
        })


def test_user_confirmed_provenance_shape_is_exact_and_other_kinds_fail_closed() -> None:
    identifier = "a" * 64
    assert CareerEvidenceProvenance(source_kind="user_confirmed", source_ref=f"clarification:{identifier}").source_kind == "user_confirmed"
    with pytest.raises(ValueError):
        CareerEvidenceProvenance(source_kind="user_confirmed", source_ref="clarification:row-id")
    with pytest.raises(ValueError):
        CareerEvidenceProvenance(source_kind="other", source_ref="x")


def test_clarification_interpreter_uses_strict_answer_isolated_payload() -> None:
    result = ClarificationInterpretation(answer_kind="career_fact", confirmed_context_summary="Synthetic summary.", proposed_evidence=[])
    calls: list[dict[str, object]] = []

    class _Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"output_text": result.model_dump_json()})()

    interpreter = SemanticCandidateAdviserClarificationInterpreter(type("Client", (), {"responses": _Responses()})(), "test-model")
    assert interpreter.interpret(question_text="Synthetic question?", answer_text="Synthetic answer.") == result
    request = calls[0]
    payload = json.loads(request["input"][1]["content"].split("INPUT:\n", 1)[1])
    assert set(payload) == {"question", "candidate_answer"}
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["schema"] == strict_schema_from_pydantic_model(ClarificationInterpretation)
    _assert_strict_schema(request["text"]["format"]["schema"])


def test_clarification_interpreter_bounds_answer_before_provider_call() -> None:
    result = ClarificationInterpretation(answer_kind="insufficient", confirmed_context_summary="Synthetic summary.", proposed_evidence=[])
    calls: list[dict[str, object]] = []

    class _Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"output_text": result.model_dump_json()})()

    interpreter = SemanticCandidateAdviserClarificationInterpreter(type("Client", (), {"responses": _Responses()})(), "test-model")
    interpreter.interpret(question_text="Synthetic question?", answer_text="word " * 2_000)
    payload = json.loads(calls[0]["input"][1]["content"].split("INPUT:\n", 1)[1])
    assert len(payload["candidate_answer"]) == 4_000


def _assert_strict_schema(value: object) -> None:
    if isinstance(value, dict):
        assert "default" not in value
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
            assert set(value.get("required", [])) == set(value.get("properties", {}))
        for child in value.values():
            _assert_strict_schema(child)
    elif isinstance(value, list):
        for child in value:
            _assert_strict_schema(child)


def test_confirmed_state_beyond_provider_projection_limit_still_changes_fingerprint(db_session) -> None:
    user_id = _user(db_session, "clarification-fingerprint-bound@example.com")
    _ready_profile(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_Adviser(), clarification_interpreter=_Interpreter(_career_fact()))
    service.save_intake(user_id, _intake())
    interpretation = ClarificationInterpretation(answer_kind="preference_intent", confirmed_context_summary="Context.", proposed_evidence=[]).model_dump(mode="json")
    start = datetime.now(timezone.utc)
    for index in range(13):
        identifier = f"{index:064x}"
        db_session.add(CandidateAdviserClarificationRecord(
            user_id=user_id, clarification_id=identifier, question_key=identifier,
            origin_assessment_fingerprint="f" * 64, question_text=f"Question {index}",
            question_source_references_json="[]", priority_index=index,
            interpretation_json=json.dumps(interpretation), status="confirmed",
            confirmed_at=start + timedelta(seconds=index),
        ))
    db_session.commit()
    before_input = service._semantic_input(user_id)
    before = service.input_fingerprint(user_id, semantic_input=before_input)
    assert len(before_input.clarifications) == 12
    assert [item.clarification_id for item in before_input.clarifications] == [f"{index:064x}" for index in range(1, 13)]
    excluded = db_session.scalar(select(CandidateAdviserClarificationRecord).where(CandidateAdviserClarificationRecord.user_id == user_id, CandidateAdviserClarificationRecord.clarification_id == f"{0:064x}"))
    assert excluded is not None
    changed = dict(interpretation)
    changed["confirmed_context_summary"] = "Changed excluded context."
    excluded.interpretation_json = json.dumps(changed)
    db_session.commit()
    after_input = service._semantic_input(user_id)
    after = service.input_fingerprint(user_id, semantic_input=after_input)
    assert after_input.clarifications == before_input.clarifications
    assert after != before


def test_question_id_is_assessment_specific_but_question_key_is_cross_assessment_stable(db_session) -> None:
    insight = _insight("  What delivery scope did you own? ")
    first = CandidateAdviserService._clarification_identity("a" * 64, insight)
    second = CandidateAdviserService._clarification_identity("b" * 64, insight)
    assert first[0] != second[0]
    assert first[1] == second[1]


def test_materialisation_is_idempotent_and_user_scoped(db_session) -> None:
    user_a = _user(db_session, "clarification-idempotent-a@example.com")
    user_b = _user(db_session, "clarification-idempotent-b@example.com")
    _ready_profile(db_session, user_a)
    _ready_profile(db_session, user_b)
    service = _confirmed_service(db_session, user_a, _Adviser("Question?"), _Interpreter(_career_fact()))
    assert len(service.list_clarifications(user_a)) == 1
    assert len(service.list_clarifications(user_a)) == 1
    record = db_session.scalar(select(CandidateAdviserClarificationRecord).where(CandidateAdviserClarificationRecord.user_id == user_a))
    assert record is not None
    with pytest.raises(LookupError):
        service.answer_clarification(user_b, record.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))


def test_current_assessment_duplicate_questions_materialize_once(db_session) -> None:
    user_id = _user(db_session, "clarification-current-duplicate@example.com")
    _ready_profile(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_Adviser(), clarification_interpreter=_Interpreter(_career_fact()))
    now = datetime.now(timezone.utc)
    first = AdviserInsight(
        text="What delivery scope did you own?",
        source_references=[{"source_type": "intake", "reference": "career_direction"}],
    )
    second = AdviserInsight(
        text=" What  delivery scope did you own? ",
        source_references=[{"source_type": "career_evidence", "reference": "synthetic-evidence"}],
    )
    content = _content()
    content.open_questions = [first, second]
    assessment = CandidateAdviserAssessmentRead(
        input_fingerprint="b" * 64,
        status="confirmed",
        content=content,
        created_at=now,
        updated_at=now,
    )
    service._materialize_clarifications(user_id, assessment)
    records = db_session.scalars(select(CandidateAdviserClarificationRecord).where(
        CandidateAdviserClarificationRecord.user_id == user_id,
        CandidateAdviserClarificationRecord.origin_assessment_fingerprint == assessment.input_fingerprint,
    )).all()
    assert len(records) == 1
    assert records[0].question_text == first.text


def test_confirmation_rolls_back_status_and_evidence_when_reconciliation_fails(db_session, monkeypatch) -> None:
    user_id = _user(db_session, "clarification-confirmation-atomic@example.com")
    _ready_profile(db_session, user_id)
    service = _confirmed_service(db_session, user_id, _Adviser("What delivery work did you own?"), _Interpreter(_career_fact()))
    clarification = service.list_clarifications(user_id)[0]
    service.answer_clarification(user_id, clarification.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))

    def fail_after_confirmation_flush(resolved_user_id: str) -> None:
        persisted = db_session.scalar(select(CandidateAdviserClarificationRecord).where(
            CandidateAdviserClarificationRecord.user_id == resolved_user_id,
            CandidateAdviserClarificationRecord.clarification_id == clarification.clarification_id,
        ))
        assert persisted is not None and persisted.status == "confirmed"
        db_session.add(CandidateEvidenceRecord(
            user_id=resolved_user_id,
            fingerprint="a" * 64,
            evidence_type="project",
            title="Partial evidence",
            text="Must be rolled back.",
            skills_json="[]",
            provenance_json="[]",
        ))
        db_session.flush()
        raise RuntimeError("synthetic reconciliation failure")

    monkeypatch.setattr(service, "_resolve_active_evidence", fail_after_confirmation_flush)
    with pytest.raises(RuntimeError, match="synthetic reconciliation failure"):
        service.confirm_clarification(user_id, clarification.clarification_id)

    db_session.expire_all()
    persisted = db_session.scalar(select(CandidateAdviserClarificationRecord).where(
        CandidateAdviserClarificationRecord.user_id == user_id,
        CandidateAdviserClarificationRecord.clarification_id == clarification.clarification_id,
    ))
    assert persisted is not None
    assert persisted.status == "review_ready"
    assert persisted.confirmed_at is None
    assert db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all() == []
    # No outer rollback: the surrounding session remains usable and commits.
    persisted.answer_text = "Still reviewable after failed confirmation."
    db_session.commit()
    db_session.expire_all()
    assert db_session.scalar(select(CandidateAdviserClarificationRecord.answer_text).where(CandidateAdviserClarificationRecord.id == persisted.id)) == "Still reviewable after failed confirmation."


def test_legacy_fingerprint_remains_current_without_confirmed_clarifications(db_session) -> None:
    user_id = _user(db_session, "clarification-legacy@example.com")
    _ready_profile(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_Adviser(), clarification_interpreter=_Interpreter(_career_fact()))
    service.save_intake(user_id, _intake())
    semantic_input = service._semantic_input(user_id)
    legacy_payload = semantic_input.model_dump(mode="json")
    legacy_payload.pop("clarifications")
    fingerprint = hashlib.sha256(json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    db_session.add(CandidateAdviserAssessmentRecord(user_id=user_id, input_fingerprint=fingerprint, status="confirmed", assessment_json=json.dumps(_content().model_dump(mode="json"))))
    db_session.commit()
    assert service.get_assessment(user_id).status == "confirmed"

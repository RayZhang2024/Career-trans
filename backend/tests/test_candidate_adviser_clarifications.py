import hashlib
import json

import pytest
from sqlalchemy import select

from app.models.candidate_adviser import CandidateAdviserAssessmentRecord, CandidateAdviserClarificationRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.user import User
from app.schemas.candidate_adviser import (
    AdviserInsight,
    CandidateAdviserAssessmentContent,
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
            evidence_type="project", title="Synthetic delivery", text="Delivered a synthetic production system.", skills=["Python"],
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
    invalid = ClarificationInterpretation(answer_kind=ClarificationAnswerKind.ELIGIBILITY_FACT, confirmed_context_summary="Context.", proposed_evidence=[ClarificationProposedEvidence(evidence_type="project", title="No", text="No", skills=[])])
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
        proposed_evidence=[ClarificationProposedEvidence(evidence_type="project", title="Kubernetes", text="Never used Kubernetes.", skills=[])],
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
        proposed_evidence=[ClarificationProposedEvidence(evidence_type="other", title="Eligibility", text="Has right to work in the UK.", skills=[])],
    )
    service = _confirmed_service(db_session, user_id, _Adviser("Synthetic?"), _Interpreter(invalid))
    clarification = service.list_clarifications(user_id)[0]
    with pytest.raises(ValueError, match="Eligibility claims"):
        service.answer_clarification(user_id, clarification.clarification_id, CandidateAdviserClarificationAnswer(answer_text="Synthetic."))


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


def test_confirmed_state_beyond_provider_projection_limit_still_changes_fingerprint(db_session) -> None:
    user_id = _user(db_session, "clarification-fingerprint-bound@example.com")
    _ready_profile(db_session, user_id)
    service = CandidateAdviserService(db_session, agent=_Adviser(), clarification_interpreter=_Interpreter(_career_fact()))
    service.save_intake(user_id, _intake())
    interpretation = ClarificationInterpretation(answer_kind="preference_intent", confirmed_context_summary="Context.", proposed_evidence=[]).model_dump(mode="json")
    for index in range(13):
        identifier = f"{index:064x}"
        db_session.add(CandidateAdviserClarificationRecord(
            user_id=user_id, clarification_id=identifier, question_key=identifier,
            origin_assessment_fingerprint="f" * 64, question_text=f"Question {index}",
            question_source_references_json="[]", priority_index=index,
            interpretation_json=json.dumps(interpretation), status="confirmed",
        ))
    db_session.commit()
    before_input = service._semantic_input(user_id)
    before = service.input_fingerprint(user_id, semantic_input=before_input)
    assert len(before_input.clarifications) == 12
    excluded = db_session.scalar(select(CandidateAdviserClarificationRecord).where(CandidateAdviserClarificationRecord.user_id == user_id, CandidateAdviserClarificationRecord.clarification_id == f"{12:064x}"))
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

import hashlib
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agents.candidate_adviser_profile_proposal import SemanticCandidateAdviserProfileProposalGenerator
from app.main import app
from app.models.candidate_adviser import CandidateAdviserClarificationRecord
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.user import User
from app.providers.llm import (
    SemanticOutputError,
    SemanticProviderConfigurationError,
    SemanticProviderUnavailableError,
)
from app.providers.openai_structured_output import strict_schema_from_pydantic_model
from app.schemas.candidate_adviser import (
    ClarificationAnswerKind,
    ClarificationInterpretation,
    ClarificationProposedEvidence,
)
from app.schemas.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalGeneration,
    CandidateAdviserProfileProposalGenerationInput,
    ProjectProposalUpdate,
    SkillProposalUpdate,
    StructuredProfileSection,
)
from app.schemas.cv_ingestion import CandidateCVData, Project, Skill
from app.services.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalConflict,
    CandidateAdviserProfileProposalNotFound,
    CandidateAdviserProfileProposalService,
    structured_profile_item_fingerprint,
)
from app.services.candidate_adviser_profile_proposal_generation import (
    CandidateAdviserProfileProposalGenerationService,
)
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.llm_runtime import RUNTIME_OPERATION_TO_SETTING
from app.schemas.ai_settings import SemanticOperation


def _user(session, email="proposal-generator@example.com"):
    record = User(email=email, password_hash="unused")
    session.add(record)
    session.commit()
    return record.id


def _source(session, user_id, *, evidence=True, answer_kind=ClarificationAnswerKind.CAREER_FACT, status="confirmed"):
    clarification_id = hashlib.sha256(f"generation:{user_id}".encode()).hexdigest()
    proposed = [ClarificationProposedEvidence(
        fact_domain="career",
        evidence_type="achievement",
        title="Service improvement",
        text="Reduced a service processing delay using Python.",
        skills=["Python"],
    )] if evidence else []
    interpretation = ClarificationInterpretation(
        answer_kind=answer_kind,
        confirmed_context_summary="The user improved a service process.",
        proposed_evidence=proposed,
    )
    session.add(CandidateAdviserClarificationRecord(
        user_id=user_id,
        clarification_id=clarification_id,
        question_key=hashlib.sha256(b"question").hexdigest(),
        origin_assessment_fingerprint="a" * 64,
        question_text="What career result should be recorded?",
        question_source_references_json="[]",
        priority_index=0,
        answer_text="Private answer that must not enter generation outside interpretation.",
        interpretation_json=json.dumps(interpretation.model_dump(mode="json"), sort_keys=True),
        status=status,
    ))
    session.commit()
    return clarification_id


def _skill_update(name="Python", *, operation="add", target=None):
    return SkillProposalUpdate(
        section="skills", operation=operation, target_fingerprint=target,
        item=Skill(name=name, category="language"),
    )


def _project_update(name="Service tool"):
    return ProjectProposalUpdate(
        section="projects", operation="add", target_fingerprint=None,
        item=Project(name=name, description="A source-supported service tool."),
    )


class FakeGenerator:
    def __init__(self, proposals, *, callback=None):
        self.proposals = proposals
        self.callback = callback
        self.calls = 0
        self.received = None

    def generate(self, *, generation_input):
        self.calls += 1
        self.received = generation_input
        if self.callback:
            self.callback(generation_input)
        return CandidateAdviserProfileProposalGeneration(proposals=self.proposals)


def _service(session, generator, factory_calls=None):
    def factory():
        if factory_calls is not None:
            factory_calls.append(True)
        return generator
    return CandidateAdviserProfileProposalGenerationService(session, generator_factory=factory)


def _rows(session, user_id):
    return session.scalars(select(CandidateAdviserProfileProposalRecord).where(
        CandidateAdviserProfileProposalRecord.user_id == user_id
    )).all()


def _seed_structured(session, user_id, data):
    row = session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
    if row is None:
        row = CandidateStructuredProfile(user_id=user_id, structured_json=data.model_dump_json())
        session.add(row)
    else:
        row.structured_json = data.model_dump_json()
    session.commit()
    return row


def test_generation_add_is_source_grounded_bounded_and_downstream_inert(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    data = CandidateCVData(
        skills=[Skill(name="Python", category="language")],
        evidence=[],
    )
    _seed_structured(db_session, user_id, data)
    evidence_row = CandidateEvidenceRecord(
        user_id=user_id, fingerprint="c" * 64, evidence_type="achievement",
        title="Private active evidence", text="Private evidence text", skills_json="[]", provenance_json="[]",
    )
    db_session.add(evidence_row)
    db_session.commit()
    before = CanonicalCandidateReadService(db_session).read(user_id).model_dump(mode="json")
    before_structured = db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )).structured_json
    before_evidence = db_session.scalars(select(CandidateEvidenceRecord)).all()

    fake = FakeGenerator([_skill_update(), _project_update()])
    factory_calls = []
    response = _service(db_session, fake, factory_calls).generate(user_id, clarification_id)

    assert factory_calls == [True]
    assert len(response.proposals) == 2
    assert all(row.state == "pending" for row in response.proposals)
    assert {row.proposed_update.section for row in response.proposals} == {"skills", "projects"}
    payload = fake.received.model_dump(mode="json")
    assert set(payload) == {"source", "target_catalogue"}
    assert payload["source"]["proposed_evidence"][0]["text"] == "Reduced a service processing delay using Python."
    assert "Private answer" not in json.dumps(payload)
    assert "Private active evidence" not in json.dumps(payload)
    assert "Private evidence text" not in json.dumps(payload)
    assert "evidence" not in payload["target_catalogue"]
    after = CanonicalCandidateReadService(db_session).read(user_id).model_dump(mode="json")
    assert after == before
    assert db_session.scalar(select(CandidateStructuredProfile).where(
        CandidateStructuredProfile.user_id == user_id
    )).structured_json == before_structured
    assert db_session.scalars(select(CandidateEvidenceRecord)).all() == before_evidence


def test_generation_returns_zero_without_affirmative_evidence_before_provider(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id, evidence=False)
    factory_calls = []
    fake = FakeGenerator([_skill_update()])
    result = _service(db_session, fake, factory_calls).generate(user_id, clarification_id)
    assert result.proposals == []
    assert factory_calls == []
    assert fake.calls == 0
    assert _rows(db_session, user_id) == []


def test_catalogue_is_bounded_in_source_order_and_marks_truncation(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    skills = [Skill(name=f"Skill {index}") for index in range(12)]
    _seed_structured(db_session, user_id, CandidateCVData(skills=skills))
    fake = FakeGenerator([])
    _service(db_session, fake).generate(user_id, clarification_id)
    catalogue = fake.received.target_catalogue
    assert [target.item.name for target in catalogue.skills] == [f"Skill {index}" for index in range(10)]
    assert catalogue.truncated_sections == [StructuredProfileSection.SKILLS]


@pytest.mark.parametrize("source_kind", ["missing", "unconfirmed", "other_owner"])
def test_missing_or_unconfirmed_source_does_not_construct_generator(db_session, source_kind):
    user_id = _user(db_session, f"source-{source_kind}@example.com")
    if source_kind == "missing":
        clarification_id = "f" * 64
    else:
        source_owner = user_id if source_kind == "unconfirmed" else _user(
            db_session, "proposal-generator-other-owner@example.com"
        )
        clarification_id = _source(
            db_session,
            source_owner,
            status="review_ready" if source_kind == "unconfirmed" else "confirmed",
        )
    calls = []
    exception = (
        CandidateAdviserProfileProposalNotFound
        if source_kind in {"missing", "other_owner"}
        else CandidateAdviserProfileProposalConflict
    )
    with pytest.raises(exception):
        _service(db_session, FakeGenerator([_skill_update()]), calls).generate(user_id, clarification_id)
    assert calls == []
    assert _rows(db_session, user_id) == []


@pytest.mark.parametrize("answer_kind", [
    ClarificationAnswerKind.ELIGIBILITY_FACT,
    ClarificationAnswerKind.PREFERENCE_INTENT,
    ClarificationAnswerKind.INSUFFICIENT,
])
def test_invalid_source_fails_before_provider_construction(db_session, answer_kind):
    user_id = _user(db_session, f"invalid-{answer_kind}@example.com")
    clarification_id = _source(db_session, user_id, answer_kind=answer_kind)
    calls = []
    with pytest.raises(CandidateAdviserProfileProposalConflict):
        _service(db_session, FakeGenerator([_skill_update()]), calls).generate(user_id, clarification_id)
    assert calls == []
    assert _rows(db_session, user_id) == []


def test_unknown_or_other_section_replacement_target_fails_without_persistence(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    skill = Skill(name="Python", category="language")
    _seed_structured(db_session, user_id, CandidateCVData(skills=[skill]))
    fingerprint = structured_profile_item_fingerprint(StructuredProfileSection.SKILLS, skill)

    with pytest.raises(CandidateAdviserProfileProposalConflict, match="catalogue"):
        _service(db_session, FakeGenerator([_skill_update("Rust", operation="replace_exact", target="d" * 64)])).generate(user_id, clarification_id)
    wrong_section = ProjectProposalUpdate(
        section="projects", operation="replace_exact", target_fingerprint=fingerprint,
        item=Project(name="Python project", description="Refined"),
    )
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="same structured section"):
        _service(db_session, FakeGenerator([wrong_section])).generate(user_id, clarification_id)
    assert _rows(db_session, user_id) == []


def test_valid_replace_exact_uses_one_exact_catalogue_target(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    skill = Skill(name="Python", category="language")
    _seed_structured(db_session, user_id, CandidateCVData(skills=[skill]))
    fingerprint = structured_profile_item_fingerprint("skills", skill)
    replacement = _skill_update("Advanced Python", operation="replace_exact", target=fingerprint)
    fake = FakeGenerator([replacement])
    result = _service(db_session, fake).generate(user_id, clarification_id)
    assert result.proposals[0].proposed_update == replacement
    target = fake.received.target_catalogue.skills[0]
    assert target.fingerprint == fingerprint
    assert target.item == skill


def test_target_disappearing_during_provider_call_fails_closed(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    skill = Skill(name="Python", category="language")
    _seed_structured(db_session, user_id, CandidateCVData(skills=[skill]))
    fingerprint = structured_profile_item_fingerprint("skills", skill)

    def remove_target(_input):
        _seed_structured(db_session, user_id, CandidateCVData())

    fake = FakeGenerator([
        _skill_update("Advanced Python", operation="replace_exact", target=fingerprint)
    ], callback=remove_target)
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="exactly one"):
        _service(db_session, fake).generate(user_id, clarification_id)
    assert fake.received.target_catalogue.skills[0].fingerprint == fingerprint
    assert _rows(db_session, user_id) == []


def test_duplicate_current_exact_fingerprints_fail_closed(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    skill = Skill(name="Python", category="language")
    _seed_structured(db_session, user_id, CandidateCVData(skills=[skill, skill]))
    fingerprint = structured_profile_item_fingerprint("skills", skill)
    fake = FakeGenerator([_skill_update("Advanced Python", operation="replace_exact", target=fingerprint)])
    with pytest.raises(CandidateAdviserProfileProposalConflict, match="exactly one"):
        _service(db_session, fake).generate(user_id, clarification_id)
    assert _rows(db_session, user_id) == []


def test_duplicate_output_batch_is_rejected_atomically(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    update = _skill_update()
    with pytest.raises(SemanticOutputError, match="duplicate"):
        _service(db_session, FakeGenerator([update, update])).generate(user_id, clarification_id)
    assert _rows(db_session, user_id) == []


def test_valid_and_invalid_updates_do_not_partially_persist(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    valid = _skill_update("Python")
    invalid = _skill_update("Advanced Python", operation="replace_exact", target="e" * 64)
    with pytest.raises(CandidateAdviserProfileProposalConflict):
        _service(db_session, FakeGenerator([valid, invalid])).generate(user_id, clarification_id)
    assert _rows(db_session, user_id) == []


def test_identical_regeneration_reuses_rejected_history(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    fake = FakeGenerator([_skill_update()])
    service = _service(db_session, fake)
    first = service.generate(user_id, clarification_id).proposals[0]
    rejected = CandidateAdviserProfileProposalService(db_session).reject_pending(
        user_id, first.id, expected_revision=first.revision
    )
    repeated = service.generate(user_id, clarification_id).proposals[0]
    assert repeated.id == first.id
    assert repeated.state == "rejected"
    assert repeated.revision == rejected.revision
    assert len(_rows(db_session, user_id)) == 1


def test_identical_regeneration_preserves_edited_pending_proposal(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)
    fake = FakeGenerator([_skill_update()])
    service = _service(db_session, fake)
    first = service.generate(user_id, clarification_id).proposals[0]
    edited = CandidateAdviserProfileProposalService(db_session).edit_pending(
        user_id, first.id, expected_revision=first.revision, proposed_update=_skill_update("Python 3")
    )
    repeated = service.generate(user_id, clarification_id).proposals[0]
    assert repeated.id == first.id
    assert repeated.state == "pending"
    assert repeated.original_update == first.original_update
    assert repeated.proposed_update == edited.proposed_update
    assert repeated.revision == edited.revision
    assert len(_rows(db_session, user_id)) == 1


@pytest.mark.parametrize("error", [
    SemanticOutputError("invalid"),
    SemanticProviderConfigurationError("unavailable config"),
    SemanticProviderUnavailableError("offline"),
])
def test_provider_failures_leave_no_proposals(db_session, error):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)

    class FailedGenerator:
        def generate(self, *, generation_input):
            raise error

    with pytest.raises(type(error)):
        _service(db_session, FailedGenerator()).generate(user_id, clarification_id)
    assert _rows(db_session, user_id) == []


def test_malformed_semantic_output_leaves_no_proposals(db_session):
    user_id = _user(db_session)
    clarification_id = _source(db_session, user_id)

    class Responses:
        def create(self, **kwargs):
            return type("Response", (), {"output_text": "not-json"})()

    class Client:
        responses = Responses()

    generator = SemanticCandidateAdviserProfileProposalGenerator(Client(), "test-model")
    with pytest.raises(SemanticOutputError):
        _service(db_session, generator).generate(user_id, clarification_id)
    assert _rows(db_session, user_id) == []


def test_generation_provider_schema_is_recursive_sdk_strict():
    schema = strict_schema_from_pydantic_model(CandidateAdviserProfileProposalGeneration)

    def check(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(value.get("properties", {}))
            assert "default" not in value
            assert "minLength" not in value
            assert "maxLength" not in value
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(schema)
    with pytest.raises(ValidationError):
        CandidateAdviserProfileProposalGeneration.model_validate({"proposals": [_skill_update().model_dump(), _skill_update().model_dump()] * 4})


def test_generator_uses_sdk_strict_schema_and_operation_attribution():
    captured = {}

    class Responses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"output_text": json.dumps({"proposals": []})})()

    class Client:
        responses = Responses()

    from app.schemas.candidate_adviser_profile_proposal import (
        ConfirmedClarificationProposalSource,
        StructuredProfileProposalTargetCatalogue,
    )
    payload = CandidateAdviserProfileProposalGenerationInput(
        source=ConfirmedClarificationProposalSource(
            clarification_id="x", question_text="Q", confirmed_context_summary="C",
            proposed_evidence=[ClarificationProposedEvidence(
                fact_domain="career", evidence_type="achievement", title="T", text="A"
            )],
        ),
        target_catalogue=StructuredProfileProposalTargetCatalogue(
            employment=[], education=[], credentials=[], skills=[], projects=[], achievements=[], truncated_sections=[]
        ),
    )
    result = SemanticCandidateAdviserProfileProposalGenerator(Client(), "test-model").generate(generation_input=payload)
    assert result.proposals == []
    assert captured["text"]["format"]["schema"] == strict_schema_from_pydantic_model(CandidateAdviserProfileProposalGeneration)
    assert RUNTIME_OPERATION_TO_SETTING["candidate_adviser_profile_proposal"] == SemanticOperation.CANDIDATE_ADVISER


def test_explicit_generation_endpoint_is_authenticated_and_typed(client, db_session):
    from app.api.deps import get_user_candidate_adviser_profile_proposal_generation_service

    credentials = {"email": "proposal-endpoint@example.com", "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user = db_session.scalar(select(User).where(User.email == credentials["email"]))
    clarification_id = _source(db_session, user.id)
    service = _service(db_session, FakeGenerator([_skill_update()]))
    app.dependency_overrides[get_user_candidate_adviser_profile_proposal_generation_service] = lambda: service
    try:
        assert client.post(f"/api/v1/candidate-adviser/clarifications/{clarification_id}/profile-proposals").status_code == 401
        response = client.post(
            f"/api/v1/candidate-adviser/clarifications/{clarification_id}/profile-proposals",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["proposals"][0]["state"] == "pending"
    finally:
        app.dependency_overrides.pop(get_user_candidate_adviser_profile_proposal_generation_service, None)

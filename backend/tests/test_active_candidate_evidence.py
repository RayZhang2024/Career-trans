import json

import pytest
from sqlalchemy import select

import app.services.active_candidate_evidence as active_candidate_evidence
from app.models.candidate_adviser import CandidateAdviserClarificationRecord
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.user import User
from app.schemas.candidate import CandidateContext
from app.schemas.candidate_adviser import CandidateAdviserIntake, ClarificationInterpretation
from app.schemas.cv_ingestion import CandidateCVData
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver, CanonicalCareerEvidenceDraft
from app.services.candidate_adviser_compaction import compact_candidate_adviser_input
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_profile_compaction import candidate_matching_profile
from app.services.career_evidence_fingerprint import career_evidence_fingerprint, legacy_career_evidence_fingerprint
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.cv_ingestion_service import CVIngestionService
from app.services.cv_merge_service import CVMergeService
from app.schemas.job import JobProfile, JobRequirement


def _user(db_session, email: str = "evidence@example.com") -> str:
    user = User(email=email, password_hash="not-used")
    db_session.add(user)
    db_session.commit()
    return user.id


def _confirm(db_session, user_id: str, data: CandidateCVData) -> None:
    service = CVIngestionService(db_session)
    draft = service.upload(
        user_id,
        [("cv.json", "application/json", json.dumps(data.model_dump(mode="json")).encode())],
    )
    service.interpret(user_id, draft.id)
    service.confirm(user_id, draft.id)


def _data(*, title: str, skills: list[str] | None = None) -> CandidateCVData:
    return CandidateCVData.model_validate(
        {
            "employment": [{"employer": "Example", "title": "Engineer"}],
            "education": [{"institution": "Example University", "qualification": "MSc", "field_of_study": "Engineering"}],
            "skills": [{"name": "Python"}],
            "evidence": [
                {
                    "evidence_type": "project",
                    "title": title,
                    "text": f"{title} source-supported claim.",
                    "skills": skills or ["Python"],
                }
            ],
        }
    )


def test_corrected_profile_excludes_historical_evidence_from_context_and_adviser(db_session) -> None:
    user_id = _user(db_session)
    _confirm(db_session, user_id, _data(title="A-only delivery"))
    _confirm(db_session, user_id, _data(title="B-current delivery"))

    records = list(db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)))
    assert any(record.title == "A-only delivery" for record in records)  # historical storage remains intact

    reader = CanonicalCandidateReadService(db_session)
    snapshot = reader.read(user_id)
    context = reader.candidate_context(
        snapshot, require_structured_profile=True, require_complete_evidence=True
    )
    assert context is not None
    active_titles = [item.title for item in context.evidence]
    assert "B-current delivery" in active_titles
    assert "A-only delivery" not in active_titles
    assert reader.summary(snapshot).evidence_count == len(context.evidence)

    input_data = CandidateAdviserService(db_session)._semantic_input(
        user_id,
        intake=CandidateAdviserIntake(career_direction="Direction"),
    )
    assert "B-current delivery" in [item.title for item in input_data.career_evidence]
    assert "A-only delivery" not in [item.title for item in input_data.career_evidence]


def test_legacy_fingerprint_is_reused_and_reconciliation_is_idempotent(db_session) -> None:
    user_id = _user(db_session, "legacy@example.com")
    data = _data(title="Legacy   delivery")
    item = data.evidence[0]
    record = CandidateEvidenceRecord(
        user_id=user_id,
        fingerprint=legacy_career_evidence_fingerprint(item),
        evidence_type=item.evidence_type,
        title=item.title,
        text=item.text,
        skills_json=json.dumps(item.skills),
        provenance_json="[]",
    )
    db_session.add(record)
    db_session.commit()

    resolver = ActiveCandidateEvidenceResolver(db_session)
    first = resolver.resolve(user_id, data)
    second = resolver.resolve(user_id, data)
    db_session.commit()

    assert first[0].evidence_id == record.id
    assert [item.evidence_id for item in first] == [item.evidence_id for item in second]
    assert len(db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all()) == 3


def test_current_duplicate_metadata_unions_but_corrected_metadata_replaces_history(db_session) -> None:
    user_id = _user(db_session, "metadata@example.com")
    duplicate = CandidateCVData.model_validate(
        {
            "evidence": [
                {"evidence_type": "project", "title": "Delivery", "text": "Built service.", "skills": ["Python"]},
                {"evidence_type": "project", "title": "  delivery ", "text": " Built service. ", "skills": ["Rust"]},
            ]
        }
    )
    resolver = ActiveCandidateEvidenceResolver(db_session)
    current = resolver.resolve(user_id, duplicate)
    assert len(current) == 1
    assert current[0].skills == ["Python", "Rust"]

    corrected = CandidateCVData.model_validate(
        {"evidence": [{"evidence_type": "project", "title": "Delivery", "text": "Built service.", "skills": ["Go"]}]}
    )
    replacement = resolver.resolve(user_id, corrected)
    assert replacement[0].skills == ["Go"]


def test_current_cv_and_confirmed_clarification_same_fingerprint_reconcile_to_one_active_identity(db_session) -> None:
    user_id = _user(db_session, "clarification-cross-source@example.com")
    clarification_id = "c" * 64
    data = CandidateCVData.model_validate({
        "evidence": [{
            "evidence_type": "project",
            "title": "Production delivery",
            "text": "Built the production service.",
            "skills": ["Python"],
            "provenance": [{"document_sha256": "a" * 64, "segment_ids": ["segment-1"]}],
        }],
    })
    interpretation = ClarificationInterpretation.model_validate({
        "answer_kind": "career_fact",
        "confirmed_context_summary": "Candidate confirmed the production delivery.",
        "proposed_evidence": [{
            "fact_domain": "career",
            "evidence_type": "project",
            "title": " Production  delivery ",
            "text": " Built the production service. ",
            "skills": ["Rust"],
        }],
    })
    db_session.add(CandidateAdviserClarificationRecord(
        user_id=user_id,
        clarification_id=clarification_id,
        question_key="q" * 64,
        origin_assessment_fingerprint="f" * 64,
        question_text="What delivery work did you own?",
        question_source_references_json="[]",
        priority_index=0,
        interpretation_json=interpretation.model_dump_json(),
        status="confirmed",
    ))
    db_session.commit()

    cv_claim = CanonicalCareerEvidenceDraft(
        evidence_type="project", title="Production delivery", text="Built the production service.",
    )
    clarification_claim = CanonicalCareerEvidenceDraft(
        evidence_type="project", title=" Production  delivery ", text=" Built the production service. ",
    )
    assert career_evidence_fingerprint(cv_claim) == career_evidence_fingerprint(clarification_claim)

    resolver = ActiveCandidateEvidenceResolver(db_session)
    first = resolver.resolve(user_id, data)
    second = resolver.resolve(user_id, data)

    assert len(first) == len(second) == 1
    assert first[0].evidence_id == second[0].evidence_id
    assert first[0].skills == ["Python", "Rust"]
    assert [item.model_dump(mode="json") for item in first[0].provenance] == [
        {"source_kind": "cv", "document_sha256": "a" * 64, "segment_ids": ["segment-1"], "source_ref": None},
        {"source_kind": "user_confirmed", "document_sha256": None, "segment_ids": [], "source_ref": f"clarification:{clarification_id}"},
    ]
    assert len(db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all()) == 1


def test_credential_evidence_merge_compaction_and_provider_projections(db_session) -> None:
    user_id = _user(db_session, "credentials@example.com")
    data = CandidateCVData.model_validate(
        {
            "credentials": [
                {
                    "name": "Cloud Credential",
                    "credential_type": "certification",
                    "issuer": "Issuer",
                    "issued_date": "2024-01",
                    "expiry_date": "2027-01",
                    "status": "active",
                }
            ],
            "skills": [{"name": "Cloud"}],
        }
    )
    _confirm(db_session, user_id, data)
    reader = CanonicalCandidateReadService(db_session)
    context = reader.candidate_context(
        reader.read(user_id), require_structured_profile=True, require_complete_evidence=True
    )
    assert context is not None
    credential = next(item for item in context.evidence if item.evidence_type == "credential")
    assert "issued 2024-01" in credential.text
    assert "expires 2027-01" in credential.text

    compacted = compact_candidate_adviser_input(
        intake=CandidateAdviserIntake(career_direction="Direction"), structured_cv=data,
        career_evidence=[item.model_dump(mode="json") for item in context.evidence],
    )
    assert compacted.structured_cv.credentials[0].name == "Cloud Credential"
    assert compacted.career_evidence[0].evidence_type
    assert "provenance" not in compacted.career_evidence[0].model_dump(mode="json")

    matching = candidate_matching_profile(
        CandidateContext(evidence=context.evidence),
        JobProfile(requirements=[JobRequirement(text="Cloud Credential")]),
    )
    assert matching.evidence
    assert "provenance" not in matching.evidence[0].model_dump(mode="json")
    merged = CVMergeService().merge([data, data])
    assert len(merged.credentials) == 1


def test_bare_skill_does_not_materialise_substantive_career_evidence(db_session) -> None:
    user_id = _user(db_session, "bare-skill@example.com")
    data = CandidateCVData.model_validate({"skills": [{"name": "Kubernetes"}]})

    active = ActiveCandidateEvidenceResolver(db_session).resolve(user_id, data)

    assert active == []
    assert db_session.scalars(
        select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)
    ).all() == []


def test_reconciliation_savepoint_rolls_back_partial_mutation(db_session, monkeypatch) -> None:
    user_id = _user(db_session, "atomic@example.com")
    original = CandidateEvidenceRecord(
        user_id=user_id,
        fingerprint="a" * 64,
        evidence_type="project",
        title="Existing",
        text="Existing record.",
        skills_json=json.dumps(["Python"]),
        provenance_json="[]",
    )
    db_session.add(original)
    db_session.commit()
    snapshot = (original.fingerprint, original.title, original.text, original.skills_json, original.provenance_json)

    data = CandidateCVData.model_validate(
        {
            "evidence": [
                {"evidence_type": "project", "title": "First", "text": "First current claim."},
                {"evidence_type": "project", "title": "Second", "text": "Second current claim."},
            ]
        }
    )
    original_runtime = active_candidate_evidence._runtime
    runtime_calls = 0

    def fail_after_first_post_flush_runtime(record):
        nonlocal runtime_calls
        runtime_calls += 1
        if runtime_calls == 2:
            raise RuntimeError("injected reconciliation failure")
        return original_runtime(record)

    monkeypatch.setattr(active_candidate_evidence, "_runtime", fail_after_first_post_flush_runtime)
    with pytest.raises(RuntimeError, match="injected reconciliation failure"):
        ActiveCandidateEvidenceResolver(db_session).resolve(user_id, data)
    # _runtime is invoked only immediately after the resolver's explicit flush,
    # so call one proves a reconciliation record was flushed in the savepoint.
    assert runtime_calls == 2
    db_session.expire_all()

    persisted = db_session.scalar(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.id == original.id))
    assert persisted is not None
    assert (persisted.fingerprint, persisted.title, persisted.text, persisted.skills_json, persisted.provenance_json) == snapshot
    assert len(db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all()) == 1
    # The savepoint, rather than an outer rollback, restored state; this outer
    # session can continue and commit normally.
    assert db_session.is_active
    db_session.commit()


def test_active_evidence_change_stales_confirmed_adviser_assessment(db_session) -> None:
    user_id = _user(db_session, "active-stale@example.com")
    _confirm(db_session, user_id, _data(title="Original delivery"))

    from app.schemas.candidate_adviser import CandidateAdviserAssessmentContent

    class Adviser:
        def assess(self, *, semantic_input):
            evidence_id = semantic_input.career_evidence[0].evidence_id
            insight = {
                "text": "Grounded advice.",
                "source_references": [{"source_type": "career_evidence", "reference": evidence_id}],
            }
            return CandidateAdviserAssessmentContent.model_validate(
                {
                    "professional_positioning": insight,
                    "transferable_strengths": [],
                    "development_gaps": [],
                    "role_hypotheses": [],
                    "transition_assessment": insight,
                    "open_questions": [],
                    "career_strategy_summary": insight,
                    "job_search_strategy_summary": insight,
                }
            )

    adviser = CandidateAdviserService(db_session, agent=Adviser())
    adviser.save_intake(user_id, CandidateAdviserIntake(career_direction="Direction"))
    adviser.assess(user_id)
    assert adviser.confirm_assessment(user_id).status == "confirmed"
    _confirm(db_session, user_id, _data(title="Corrected delivery"))
    assert adviser.get_assessment(user_id).status == "stale"

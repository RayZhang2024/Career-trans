import json

import pytest
from sqlalchemy import select

from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.user import User
from app.schemas.candidate import CandidateContext
from app.schemas.candidate_adviser import CandidateAdviserIntake
from app.schemas.cv_ingestion import CandidateCVData
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_adviser_compaction import compact_candidate_adviser_input
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_profile_compaction import candidate_matching_profile
from app.services.career_evidence_fingerprint import legacy_career_evidence_fingerprint
from app.services.cv_ingestion_service import CVIngestionService, PersistedCandidateContextLoader
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

    context = PersistedCandidateContextLoader(db_session).load_confirmed(user_id)
    assert context is not None
    active_titles = [item.title for item in context.evidence]
    assert "B-current delivery" in active_titles
    assert "A-only delivery" not in active_titles
    assert PersistedCandidateContextLoader(db_session).summary(user_id).evidence_count == len(context.evidence)

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
    context = PersistedCandidateContextLoader(db_session).load_confirmed(user_id)
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
    original_flush = db_session.flush
    calls = 0

    def fail_second_flush(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected reconciliation failure")
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", fail_second_flush)
    with pytest.raises(RuntimeError, match="injected reconciliation failure"):
        ActiveCandidateEvidenceResolver(db_session).resolve(user_id, data)
    db_session.rollback()
    db_session.expire_all()

    persisted = db_session.scalar(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.id == original.id))
    assert persisted is not None
    assert (persisted.fingerprint, persisted.title, persisted.text, persisted.skills_json, persisted.provenance_json) == snapshot
    assert len(db_session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)).all()) == 1


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

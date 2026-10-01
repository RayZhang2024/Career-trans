"""Focused provider-free regressions for the Phase 6 canonical job workspace."""

import json
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from app.api.deps import get_user_job_workspace_read_service
from app.core.security import create_access_token
from app.main import app

from app.models.discovered_job_provenance import DiscoveredJobProvenance
from app.models.application_preparation import ApplicationPreparation
from app.models.application_tracking import ApplicationTrackingRecord
from app.models.user_job_discovery import UserJobEvaluation
from app.models.user_job_decision import UserJobDecision
from app.schemas.candidate import CandidateEvidenceMaterializationStatus
from app.schemas.ai_settings import SemanticOperation
from app.schemas.job import JobProfile, JobRequirement
from app.schemas.job_ranking import RankedJobOpportunity
from app.schemas.application_preparation import ApplicationPreparationResult, ApplicationSourceRef, ApplicationTargetSnapshot, TailoredCVContent
from app.schemas.matching import RequirementMatch
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.services.user_job_workspace_service import UserJobWorkspaceReadService
from app.services.application_preparation_service import ApplicationPreparationReadService
from app.services.user_job_decision_service import UserJobDecisionService
from app.schemas.user_job_decision import UserJobDecisionMutation
from app.services.llm_runtime import RuntimePreferenceError
from candidate_read_support import patch_candidate_context, snapshot_for_context, StaticCandidateReader
from test_jobs_read_models import _context, _job, _Ranking, _request, _user


def _make_usable_evaluation(evaluation: UserJobEvaluation, job) -> None:
    stored = RankedJobOpportunity.model_validate_json(evaluation.evaluation_json)
    evaluation.evaluation_json = stored.model_copy(update={
        "job_profile": JobProfile(title=job.title, requirements=[JobRequirement(text="Python", importance="essential", category="technical")]),
        "requirement_matches": [RequirementMatch(requirement_index=0, requirement={"text": "Python", "importance": "essential", "category": "technical"}, match_type="demonstrated", score=0.8, evidence_ids=[], reasoning="safe")],
    }).model_dump_json()


def test_workspace_returns_shared_facts_provenance_and_current_fit_without_provider_work(db_session, monkeypatch, runtime_snapshot_a):
    job = _job(1)
    now = datetime.now(timezone.utc)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    db_session.add_all([
        DiscoveredJobProvenance(job_id=job.id, runtime="later", source_ref="later-ref", discovered_via="import", fingerprint="1" * 64, imported_at=now),
        DiscoveredJobProvenance(job_id=job.id, runtime="earlier", source_ref="earlier-ref", discovered_via="search", fingerprint="2" * 64, imported_at=now - timedelta(seconds=1)),
    ])
    db_session.commit()
    from candidate_read_support import patch_candidate_context
    patch_candidate_context(monkeypatch, _context())
    UserJobDiscoveryService(db_session, ranking_service=_Ranking(), runtime_snapshot=runtime_snapshot_a).start("owner", _request([job.id]))
    evaluation = db_session.query(UserJobEvaluation).filter_by(user_id="owner").one()
    _make_usable_evaluation(evaluation, job)
    db_session.commit()

    workspace = UserJobWorkspaceReadService(db_session, runtime_snapshot_resolver=lambda _: runtime_snapshot_a).read("owner", job.id)

    assert workspace.job.title == job.title
    assert workspace.job.actionable is True
    assert [item.runtime for item in workspace.provenance.items] == ["later", "earlier"]
    assert workspace.current_fit.status == "current"
    assert workspace.current_fit.evaluation is not None
    assert workspace.current_fit.evaluation.applicability == "current"
    assert workspace.evaluations.items[0].applicability == "current"
    assert workspace.provenance.count == 2


def test_workspace_scopes_evaluation_history_and_marks_currentness_unavailable_without_runtime(db_session, monkeypatch, runtime_snapshot_a):
    job = _job(2)
    db_session.add_all([_user("owner"), _user("other"), job])
    db_session.commit()
    from candidate_read_support import patch_candidate_context
    patch_candidate_context(monkeypatch, _context())
    discovery = UserJobDiscoveryService(db_session, ranking_service=_Ranking(), runtime_snapshot=runtime_snapshot_a)
    discovery.start("owner", _request([job.id]))
    db_session.add(UserJobEvaluation(
        user_id="other", discovered_job_id=job.id, job_content_hash=job.content_hash,
        candidate_evaluation_fingerprint="other-candidate", evaluation_contract_fingerprint="other-contract",
        job_snapshot_json="{}", evaluation_json=db_session.query(UserJobEvaluation).filter_by(user_id="owner").one().evaluation_json,
    ))
    db_session.commit()

    workspace = UserJobWorkspaceReadService(db_session).read("owner", job.id)

    assert workspace.current_fit.status == "unavailable"
    assert workspace.current_fit.reason == "runtime_configuration_unavailable"
    assert len(workspace.evaluations.items) == 1
    assert workspace.evaluations.items[0].applicability == "unknown"


def test_workspace_missing_job_is_safe_404_and_endpoint_is_authenticated(db_session, client):
    db_session.add(_user("owner"))
    db_session.commit()
    response = client.get("/api/v1/jobs/workspaces/missing", headers={"Authorization": f"Bearer {create_access_token('owner')}"})
    assert response.status_code == 404


def test_workspace_provenance_count_limit_and_order_are_truthful(db_session):
    job = _job(3)
    now = datetime.now(timezone.utc)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    db_session.add_all([
        DiscoveredJobProvenance(id="b", job_id=job.id, runtime="same-time-b", fingerprint="b" * 64, imported_at=now),
        DiscoveredJobProvenance(id="c", job_id=job.id, runtime="older", fingerprint="c" * 64, imported_at=now - timedelta(seconds=1)),
        DiscoveredJobProvenance(id="a", job_id=job.id, runtime="same-time-a", fingerprint="a" * 64, imported_at=now),
    ])
    db_session.commit()

    workspace = UserJobWorkspaceReadService(db_session).read("owner", job.id, provenance_limit=2)

    assert workspace.provenance.count == 3
    assert workspace.provenance.truncated is True
    assert [item.runtime for item in workspace.provenance.items] == ["same-time-a", "same-time-b"]


def test_workspace_no_evaluation_still_returns_shared_overview(db_session):
    job = _job(4)
    db_session.add_all([_user("owner"), job])
    db_session.commit()

    workspace = UserJobWorkspaceReadService(db_session).read("owner", job.id)

    assert workspace.job.id == job.id
    assert workspace.job.actionable is True
    assert workspace.current_fit.status == "unavailable"
    assert workspace.evaluations.items == []


def test_workspace_projects_all_decision_states_without_touching_fit_or_workflow_rows(db_session):
    job = _job(401)
    other_job = _job(402)
    db_session.add_all([_user("owner"), _user("other"), job, other_job])
    db_session.commit()
    service = UserJobWorkspaceReadService(db_session)
    decision_service = UserJobDecisionService(db_session)
    assert service.read("owner", job.id).decision.revision is None
    db_session.add(UserJobDecision(user_id="owner", discovered_job_id=other_job.id, decision="undecided", revision=4))
    db_session.commit()
    assert service.read("owner", other_job.id).decision.revision == 4

    persisted = decision_service.mutate("owner", job.id, UserJobDecisionMutation(decision="shortlisted"))
    assert service.read("owner", job.id).decision.decision == "shortlisted"
    decision_service.mutate("owner", job.id, UserJobDecisionMutation(decision="dismissed", expected_revision=persisted.revision))
    dismissed = service.read("owner", job.id)
    assert dismissed.decision.decision == "dismissed"
    assert dismissed.current_fit.status == "unavailable"
    assert service.read("other", job.id).decision.decision == "undecided"
    assert service.read("owner", other_job.id).decision.decision == "undecided"
    assert db_session.query(UserJobDecision).count() == 2


def test_workspace_currentness_reason_contract_and_unexpected_errors(db_session, runtime_snapshot_a):
    job = _job(5)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    context = _context()

    not_ready = UserJobWorkspaceReadService(
        db_session,
        candidate_reader=StaticCandidateReader(snapshot_for_context(context, structured_profile_available=False)),
        runtime_snapshot_resolver=lambda _: runtime_snapshot_a,
    ).read("owner", job.id)
    assert not_ready.current_fit.status == "unavailable"
    assert not_ready.current_fit.reason == "candidate_not_ready"

    incomplete = UserJobWorkspaceReadService(
        db_session,
        candidate_reader=StaticCandidateReader(snapshot_for_context(context, evidence_status=CandidateEvidenceMaterializationStatus.INCOMPLETE)),
        runtime_snapshot_resolver=lambda _: runtime_snapshot_a,
    ).read("owner", job.id)
    assert incomplete.current_fit.reason == "candidate_evidence_incomplete"

    runtime_unavailable = UserJobWorkspaceReadService(
        db_session,
        candidate_reader=StaticCandidateReader(snapshot_for_context(context)),
        runtime_snapshot_resolver=lambda _: (_ for _ in ()).throw(RuntimePreferenceError("invalid local runtime")),
    ).read("owner", job.id)
    assert runtime_unavailable.current_fit.reason == "runtime_configuration_unavailable"

    class BrokenCandidateReader(StaticCandidateReader):
        def read(self, user_id: str):
            raise RuntimeError("database failure")

    with pytest.raises(RuntimeError, match="database failure"):
        UserJobWorkspaceReadService(
            db_session,
            candidate_reader=BrokenCandidateReader(snapshot_for_context(context)),
            runtime_snapshot_resolver=lambda _: runtime_snapshot_a,
        ).read("owner", job.id)

    with pytest.raises(RuntimeError, match="resolver failure"):
        UserJobWorkspaceReadService(
            db_session,
            candidate_reader=StaticCandidateReader(snapshot_for_context(context)),
            runtime_snapshot_resolver=lambda _: (_ for _ in ()).throw(RuntimeError("resolver failure")),
        ).read("owner", job.id)


def test_workspace_job_not_actionable_and_history_applicability_are_distinct(db_session):
    job = _job(6)
    job.verification_status = "unverified"
    db_session.add_all([_user("owner"), job])
    db_session.commit()

    workspace = UserJobWorkspaceReadService(db_session).read("owner", job.id)

    assert workspace.current_fit.status == "none"
    assert workspace.current_fit.reason == "job_not_actionable"


def test_workspace_dependency_is_lazy_and_endpoint_returns_authenticated_workspace(db_session, client):
    job = _job(7)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    service = UserJobWorkspaceReadService(db_session)
    app.dependency_overrides[get_user_job_workspace_read_service] = lambda: service

    response = client.get(f"/api/v1/jobs/workspaces/{job.id}", headers={"Authorization": f"Bearer {create_access_token('owner')}"})

    assert response.status_code == 200
    assert response.json()["job"]["id"] == job.id
    assert response.json()["provenance"]["count"] == 0


def test_workspace_evaluation_ordering_bounds_and_none_history_applicability(db_session, monkeypatch, runtime_snapshot_a):
    job = _job(8)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    UserJobDiscoveryService(db_session, ranking_service=_Ranking(), runtime_snapshot=runtime_snapshot_a).start("owner", _request([job.id]))
    base = db_session.query(UserJobEvaluation).filter_by(user_id="owner").one()
    _make_usable_evaluation(base, job)
    now = datetime.now(timezone.utc)
    base.id = "evaluation-c"
    base.created_at = now - timedelta(seconds=2)
    for identifier, created_at in (("evaluation-b", now), ("evaluation-a", now)):
        db_session.add(UserJobEvaluation(
            id=identifier, user_id="owner", discovered_job_id=job.id, job_content_hash=job.content_hash,
            candidate_evaluation_fingerprint=identifier, evaluation_contract_fingerprint=f"contract-{identifier}",
            job_snapshot_json="{}", evaluation_json=base.evaluation_json, created_at=created_at,
        ))
    db_session.commit()

    workspace = UserJobWorkspaceReadService(
        db_session,
        candidate_reader=StaticCandidateReader(snapshot_for_context(_context().model_copy(update={"skills_text": "Different"}))),
        runtime_snapshot_resolver=lambda _: runtime_snapshot_a,
    ).read("owner", job.id, evaluation_limit=2)

    assert workspace.current_fit.status == "none"
    assert workspace.current_fit.reason == "no_current_evaluation"
    assert [item.id for item in workspace.evaluations.items] == ["evaluation-a", "evaluation-b"]
    assert all(item.applicability == "historical" for item in workspace.evaluations.items)
    assert workspace.evaluations.truncated is True


def test_workspace_current_evaluation_outside_history_window_and_legitimacy_projection(db_session, monkeypatch, runtime_snapshot_a):
    job = _job(9)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    UserJobDiscoveryService(db_session, ranking_service=_Ranking(), runtime_snapshot=runtime_snapshot_a).start("owner", _request([job.id]))
    current = db_session.query(UserJobEvaluation).filter_by(user_id="owner").one()
    _make_usable_evaluation(current, job)
    now = datetime.now(timezone.utc)
    current.created_at = now - timedelta(days=1)
    historical_json = current.evaluation_json
    job.posted_at = now
    for identifier, created_at in (("history-a", now), ("history-b", now - timedelta(seconds=1))):
        db_session.add(UserJobEvaluation(
            id=identifier, user_id="owner", discovered_job_id=job.id, job_content_hash=job.content_hash,
            candidate_evaluation_fingerprint=identifier, evaluation_contract_fingerprint=f"contract-{identifier}",
            job_snapshot_json="{}", evaluation_json=historical_json, created_at=created_at,
        ))
    db_session.commit()

    workspace = UserJobWorkspaceReadService(
        db_session,
        candidate_reader=StaticCandidateReader(snapshot_for_context(_context())),
        runtime_snapshot_resolver=lambda _: runtime_snapshot_a,
    ).read("owner", job.id, evaluation_limit=2)

    assert workspace.current_fit.status == "current"
    assert workspace.current_fit.evaluation is not None
    assert workspace.current_fit.evaluation.id == current.id
    assert all(item.applicability == "historical" for item in workspace.evaluations.items)
    assert all(item.id != current.id for item in workspace.evaluations.items)
    assert workspace.current_fit.evaluation.opportunity.legitimacy != workspace.evaluations.items[0].opportunity.legitimacy


def test_workspace_currentness_rechecks_job_candidate_and_runtime_identity(db_session, monkeypatch, runtime_snapshot_a):
    job = _job(10)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    UserJobDiscoveryService(db_session, ranking_service=_Ranking(), runtime_snapshot=runtime_snapshot_a).start("owner", _request([job.id]))
    evaluation = db_session.query(UserJobEvaluation).filter_by(user_id="owner").one()
    _make_usable_evaluation(evaluation, job)
    db_session.commit()

    def read(candidate_reader, runtime):
        return UserJobWorkspaceReadService(
            db_session,
            candidate_reader=candidate_reader,
            runtime_snapshot_resolver=lambda _: runtime,
        ).read("owner", job.id)

    current_reader = StaticCandidateReader(snapshot_for_context(_context()))
    assert read(current_reader, runtime_snapshot_a).current_fit.status == "current"

    original_hash = job.content_hash
    job.content_hash = "f" * 64
    assert read(current_reader, runtime_snapshot_a).current_fit.reason == "no_current_evaluation"
    job.content_hash = original_hash

    changed_candidate = StaticCandidateReader(snapshot_for_context(_context().model_copy(update={"skills_text": "Changed"})))
    assert read(changed_candidate, runtime_snapshot_a).current_fit.reason == "no_current_evaluation"

    changed_operations = tuple(
        (operation, replace(resolved, model="changed-job-relevance") if operation is SemanticOperation.JOB_RELEVANCE else resolved)
        for operation, resolved in runtime_snapshot_a.operations
    )
    changed_runtime = replace(runtime_snapshot_a, operations=changed_operations)
    assert read(current_reader, changed_runtime).current_fit.reason == "no_current_evaluation"


def _workspace_preparation(preparation_id: str, user_id: str, job_id: str | None, content_hash: str, created_at: datetime, *, legacy: bool = False) -> ApplicationPreparation:
    target = ApplicationTargetSnapshot(
        source_kind="discovered_job", canonical_discovered_job_id=job_id, title="Saved role", company="Saved Co",
        location="London", public_url="https://jobs.example.test/saved", job_profile=JobProfile(title="Saved role"),
        job_content_hash=content_hash,
    )
    result = ApplicationPreparationResult(
        cv=TailoredCVContent(
            professional_summary="Saved summary",
            summary_source_refs=[ApplicationSourceRef(source_type="career_evidence", source_ref="e1")],
        ),
        target_pages=2,
        actual_pdf_pages=2,
    )
    return ApplicationPreparation(
        id=preparation_id, user_id=user_id, target_snapshot_json=target.model_dump_json(),
        identity_snapshot_json='{"display_name":"Synthetic Candidate","email":"candidate@example.test"}', preparation_input_fingerprint="a" * 64,
        preparation_contract_fingerprint="b" * 64,
        preparation_result_json=result.model_dump_json() if legacy else '{"persistence_version":2,"result":' + result.model_dump_json() + ',"evidence_snapshot_status":"available","evidence_sources":[]}',
        created_at=created_at,
    )


def test_workspace_application_projection_is_scoped_bounded_ordered_and_set_joined(db_session):
    job = _job(2421)
    other_job = _job(2422)
    db_session.add_all([_user("owner"), _user("other"), job, other_job])
    db_session.commit()
    now = datetime.now(timezone.utc)
    rows = [
        _workspace_preparation("prep-a", "owner", job.id, job.content_hash, now),
        _workspace_preparation("prep-b", "owner", job.id, "old-hash", now),
        _workspace_preparation("prep-c", "owner", job.id, job.content_hash, now - timedelta(seconds=1)),
        _workspace_preparation("prep-other-job", "owner", other_job.id, other_job.content_hash, now),
        _workspace_preparation("prep-other-user", "other", job.id, job.content_hash, now),
        _workspace_preparation("prep-null", "owner", None, job.content_hash, now),
    ]
    db_session.add_all(rows)
    db_session.add(ApplicationTrackingRecord(preparation_id="prep-b", current_status="interview", revision=2, created_at=now, updated_at=now))
    db_session.commit()

    workspace = UserJobWorkspaceReadService(db_session).read("owner", job.id, application_limit=2)

    assert [item.preparation_id for item in workspace.applications.items] == ["prep-a", "prep-b"]
    assert workspace.applications.limit == 2
    assert workspace.applications.truncated is True
    assert workspace.applications.items[0].snapshot_status == "current_job_content"
    assert workspace.applications.items[1].snapshot_status == "historical_job_content"
    assert workspace.applications.items[0].tracking is None
    assert workspace.applications.items[1].tracking is not None
    assert workspace.applications.items[1].tracking.current_status == "interview"


def test_applications_and_workspace_return_the_same_utc_time_for_existing_naive_preparation(db_session):
    job = _job(2640)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    # SQLite strips tzinfo from existing timezone=True values; treat that persisted
    # legacy representation as UTC in both read models.
    created_at = datetime(2026, 10, 1, 5, 20, 56, 216154)
    db_session.add(_workspace_preparation("prep-2640", "owner", job.id, job.content_hash, created_at))
    db_session.commit()

    application = ApplicationPreparationReadService(db_session).list_preparations("owner")[0]
    workspace = UserJobWorkspaceReadService(db_session).read("owner", job.id)
    workspace_application = workspace.applications.items[0]

    assert application.created_at.isoformat() == workspace_application.created_at.isoformat()
    assert application.created_at.utcoffset() == timezone.utc.utcoffset(application.created_at)


def test_workspace_application_projection_safely_excludes_malformed_targets_and_supports_legacy_results(db_session):
    job = _job(2423)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    valid = _workspace_preparation("prep-valid", "owner", job.id, job.content_hash, datetime.now(timezone.utc), legacy=True)
    malformed = _workspace_preparation("prep-malformed", "owner", job.id, job.content_hash, datetime.now(timezone.utc))
    malformed.target_snapshot_json = "{not-json"
    db_session.add_all([valid, malformed])
    db_session.commit()

    workspace = UserJobWorkspaceReadService(db_session).read("owner", job.id)

    assert [item.preparation_id for item in workspace.applications.items] == ["prep-valid"]
    assert workspace.applications.items[0].result_summary is not None
    assert workspace.applications.items[0].result_summary.actual_pdf_pages == 2
    assert workspace.applications.items[0].created_at.tzinfo is not None


def test_workspace_application_projection_filters_structurally_malformed_valid_json_before_bound(db_session):
    job = _job(2424)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    now = datetime.now(timezone.utc)
    malformed = _workspace_preparation("prep-structurally-malformed", "owner", job.id, job.content_hash, now)
    malformed.target_snapshot_json = json.dumps({"source_kind": "discovered_job", "canonical_discovered_job_id": job.id})
    valid_new = _workspace_preparation("prep-valid-new", "owner", job.id, job.content_hash, now - timedelta(seconds=1))
    valid_old = _workspace_preparation("prep-valid-old", "owner", job.id, job.content_hash, now - timedelta(seconds=2))
    db_session.add_all([malformed, valid_new, valid_old])
    db_session.commit()

    workspace = UserJobWorkspaceReadService(db_session).read("owner", job.id, application_limit=1)

    assert [item.preparation_id for item in workspace.applications.items] == ["prep-valid-new"]
    assert workspace.applications.truncated is True


def test_workspace_application_projection_iterates_past_schema_invalid_target_rows(db_session):
    job = _job(2425)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    now = datetime.now(timezone.utc)
    malformed_rows = []
    for index in range(21):
        row = _workspace_preparation(f"prep-invalid-{index:02d}", "owner", job.id, job.content_hash, now - timedelta(seconds=index))
        target = json.loads(row.target_snapshot_json)
        if index % 3 == 0:
            target["source_kind"] = "bad-kind"
        elif index % 3 == 1:
            target["title"] = 42
        else:
            target["public_url"] = {"not": "a string"}
        row.target_snapshot_json = json.dumps(target)
        malformed_rows.append(row)
    valid_new = _workspace_preparation("prep-valid-after-invalid", "owner", job.id, job.content_hash, now - timedelta(seconds=21))
    valid_old = _workspace_preparation("prep-valid-old-after-invalid", "owner", job.id, job.content_hash, now - timedelta(seconds=22))
    db_session.add_all([*malformed_rows, valid_new, valid_old])
    db_session.commit()

    workspace = UserJobWorkspaceReadService(db_session).read("owner", job.id, application_limit=1)

    assert [item.preparation_id for item in workspace.applications.items] == ["prep-valid-after-invalid"]
    assert workspace.applications.truncated is True

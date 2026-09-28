"""Focused provider-free regressions for the Phase 6 canonical job workspace."""

from datetime import datetime, timedelta, timezone

import pytest

from app.api.deps import get_user_job_workspace_read_service
from app.core.security import create_access_token
from app.main import app

from app.models.discovered_job_provenance import DiscoveredJobProvenance
from app.models.user_job_discovery import UserJobEvaluation
from app.schemas.candidate import CandidateEvidenceMaterializationStatus
from app.schemas.job import JobProfile, JobRequirement
from app.schemas.job_ranking import RankedJobOpportunity
from app.schemas.matching import RequirementMatch
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.services.user_job_workspace_service import UserJobWorkspaceReadService
from app.services.llm_runtime import RuntimePreferenceError
from candidate_read_support import snapshot_for_context, StaticCandidateReader
from test_jobs_read_models import _context, _job, _Ranking, _request, _user


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
    stored = RankedJobOpportunity.model_validate_json(evaluation.evaluation_json)
    evaluation.evaluation_json = stored.model_copy(update={
        "job_profile": JobProfile(title=job.title, requirements=[JobRequirement(text="Python", importance="essential", category="technical")]),
        "requirement_matches": [RequirementMatch(requirement_index=0, requirement={"text": "Python", "importance": "essential", "category": "technical"}, match_type="demonstrated", score=0.8, evidence_ids=[], reasoning="safe")],
    }).model_dump_json()
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

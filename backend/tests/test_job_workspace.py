"""Focused provider-free regressions for the Phase 6 canonical job workspace."""

from datetime import datetime, timedelta, timezone

from app.models.discovered_job_provenance import DiscoveredJobProvenance
from app.models.user_job_discovery import UserJobEvaluation
from app.schemas.job import JobProfile, JobRequirement
from app.schemas.job_ranking import RankedJobOpportunity
from app.schemas.matching import RequirementMatch
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.services.user_job_workspace_service import UserJobWorkspaceReadService
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

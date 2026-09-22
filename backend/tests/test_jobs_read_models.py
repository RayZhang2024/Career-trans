"""Contract-focused, provider-free regressions for the Issue #172 Jobs reads."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.api.deps import (
    get_career_analysis_graph, get_job_analysis_service, get_job_archetype_agent,
    get_job_ranking_service, get_job_relevance_agent, get_requirement_matching_service,
    get_user_job_discovery_read_service, get_user_job_discovery_service,
)
from app.core.security import create_access_token
from app.main import app
from app.models.discovered_job import DiscoveredJob
from app.models.discovered_job_provenance import DiscoveredJobProvenance
from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.models.user import User
from app.models.user_job_discovery import DiscoveryRun, DiscoveryRunJob
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext, CareerEvidence, CareerEvidenceProvenance
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.discovery import DiscoveredJobState
from app.schemas.job_ranking import (
    JobArchetype, JobArchetypeAssessment, JobRankingResponse, JobRelevanceAssessment,
    PostingLegitimacy, PostingLegitimacyAssessment, RankedJobOpportunity,
)
from app.schemas.recommendation import Recommendation, RecommendationAssessment
from app.schemas.user_job_discovery import DiscoveryRunCreateRequest
from app.services.cv_ingestion_service import PersistedCandidateContextLoader
from app.services.opportunity_inbox_service import OpportunityInboxService
from app.services.user_job_discovery_service import UserJobDiscoveryService
import app.api.deps as deps_module
import app.services.user_job_discovery_service as discovery_module


def _context() -> CandidateContext:
    return CandidateContext(
        profile_text="Synthetic profile", skills_text="Python",
        evidence=[CareerEvidence(
            evidence_id="evidence-1", title="Synthetic evidence", text="Python delivery",
            skills=["Python"], provenance=[CareerEvidenceProvenance(
                document_sha256="a" * 64, segment_ids=["segment-1"]
            )],
        )],
    )


def _user(identifier: str) -> User:
    return User(id=identifier, email=f"{identifier}@example.test", password_hash="safe")


def _job(index: int, *, company: str | None = "Example") -> DiscoveredJob:
    now = datetime.now(timezone.utc)
    return DiscoveredJob(
        identity_key=f"provider:read-{index}", source="agent_runtime", source_token="board",
        external_id=f"read-{index}", title=f"AI Engineer {index}", company=company,
        location="London", url=f"https://jobs.example.test/read-{index}",
        description="Verified public description with sufficient requirements.",
        detail_authority="verified_employer_detail", verification_status="verified",
        content_hash=f"{index:064x}", state=DiscoveredJobState.UNCHANGED.value,
        first_seen_at=now, last_seen_at=now, last_changed_at=now,
    )


def _request(ids: list[str]) -> DiscoveryRunCreateRequest:
    return DiscoveryRunCreateRequest.model_validate({
        "query": {"keywords": ["AI"], "locations": ["London"]},
        "discovered_job_ids": ids,
        "max_semantic_candidates": 10,
        "max_full_analyses": 5,
        "min_relevance_score": 0.5,
    })


class _Ranking:
    def __init__(self) -> None:
        self.calls = 0

    def rank(self, request):
        self.calls += 1
        results = []
        for rank, job in enumerate(request.jobs, start=1):
            results.append(RankedJobOpportunity(
                job=job,
                relevance=JobRelevanceAssessment(relevant=True, score=.9, reasoning="safe"),
                archetype=JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="safe"),
                fit_assessment=FitAssessment(fit_score=70 + rank),
                career_assessment=CareerAssessment(career_alignment_score=80, confidence=AlignmentConfidence.HIGH, dimensions=[], reasoning="safe"),
                recommendation_assessment=RecommendationAssessment(
                    recommendation=Recommendation.APPLY, fit_score=70 + rank,
                    career_alignment_score=80, career_alignment_confidence=AlignmentConfidence.HIGH,
                    rule_id="safe", reasoning="safe",
                ),
                legitimacy=PostingLegitimacyAssessment(
                    legitimacy=PostingLegitimacy.HIGH_CONFIDENCE, reasoning="safe"
                ), rank=rank,
            ))
        return JobRankingResponse(
            discovered_count=len(request.jobs), gated_out_count=0,
            relevance_screened_count=len(request.jobs), finalist_count=len(results),
            analysed_count=len(results), results=results,
        )


def _headers(identifier: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(identifier)}"}


def _prepared(db_session, monkeypatch, *, count: int = 1, company: str | None = "Example"):
    jobs = [_job(index, company=company) for index in range(1, count + 1)]
    db_session.add_all([_user("owner"), _user("other"), *jobs])
    db_session.commit()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user_id: _context())
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed_read_only", lambda _self, _user_id: _context())
    ranking = _Ranking()
    service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    run = service.start("owner", _request([job.id for job in jobs]))
    return jobs, run, service, ranking


def _override_services(service: UserJobDiscoveryService):
    app.dependency_overrides[get_user_job_discovery_read_service] = lambda: service
    app.dependency_overrides[get_user_job_discovery_service] = lambda: service


def _clear_services() -> None:
    app.dependency_overrides.pop(get_user_job_discovery_read_service, None)
    app.dependency_overrides.pop(get_user_job_discovery_service, None)
    app.dependency_overrides.pop(get_job_ranking_service, None)


def test_read_only_loader_matches_normal_fingerprint_without_sql_writes(db_session, monkeypatch) -> None:
    db_session.add(_user("owner")); db_session.commit()
    context = _context()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user_id: context)
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed_read_only", lambda _self, _user_id: context)
    observed: list[str] = []

    def capture(_conn, _cursor, statement, *_args):
        observed.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        loader = PersistedCandidateContextLoader(db_session)
        normal, read_only = loader.load_confirmed("owner"), loader.load_confirmed_read_only("owner")
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert normal is not None and read_only is not None
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(normal) == UserJobDiscoveryService.candidate_evaluation_fingerprint(read_only)
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in observed)


def test_materialised_confirmed_loader_read_only_is_fingerprint_equivalent_and_write_free(db_session) -> None:
    """Exercise the actual reconciliation-backed authority, not a mocked context."""
    db_session.add(_user("materialised"))
    data = CandidateCVData.model_validate({
        "employment": [{"employer": "Example", "title": "Engineer", "description": "Built systems."}],
        "skills": [{"name": "Python"}],
        "evidence": [{"evidence_type": "project", "title": "Delivery", "text": "Delivered Python systems.", "skills": ["Python"]}],
    })
    db_session.add(CandidateStructuredProfile(user_id="materialised", structured_json=data.model_dump_json()))
    db_session.commit()
    loader = PersistedCandidateContextLoader(db_session)
    normal = loader.load_confirmed("materialised")
    assert normal is not None  # normal authority materialises the current evidence once.
    observed: list[str] = []

    def capture(_conn, _cursor, statement, *_args):
        observed.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        read_only = loader.load_confirmed_read_only("materialised")
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert read_only is not None
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(normal) == UserJobDiscoveryService.candidate_evaluation_fingerprint(read_only)
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in observed)


def test_jobs_dashboard_gets_are_provider_free_and_emit_no_sql_writes(client, db_session, monkeypatch) -> None:
    jobs, run, service, _ranking = _prepared(db_session, monkeypatch, count=2)
    _override_services(UserJobDiscoveryService(db_session))
    # The read-only service must obtain context through this provider-free loader.
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed_read_only", lambda _self, _user_id: _context())
    observed: list[str] = []

    def capture(_conn, _cursor, statement, *_args):
        observed.append(statement)

    app.dependency_overrides[get_job_ranking_service] = lambda: (_ for _ in ()).throw(AssertionError("ranking dependency must not be built for GET"))
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        headers = _headers("owner")
        assert client.get("/api/v1/jobs/opportunities", headers=headers).status_code == 200
        assert client.get("/api/v1/jobs/discovery-runs", headers=headers).status_code == 200
        assert client.get(f"/api/v1/jobs/discovery-runs/{run.id}", headers=headers).status_code == 200
        evaluation_id = run.jobs[0].evaluation_id
        evaluated_job = db_session.get(DiscoveredJob, run.jobs[0].discovered_job_id)
        assert client.get(f"/api/v1/jobs/opportunities/{evaluation_id}", headers=headers).status_code == 200
        assert client.get("/api/v1/jobs/inbox", headers=headers).status_code == 200
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
        _clear_services()
    assert jobs
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in observed)


def test_real_read_dependency_gets_need_no_provider_credentials_or_semantic_factories(client, db_session, monkeypatch) -> None:
    """Use FastAPI's actual get_user_job_discovery_read_service dependency."""
    monkeypatch.setenv("CAREER_TRANS_DEPLOYMENT_REVISION", "provider-free-read-test")
    jobs, run, _setup_service, _ranking = _prepared(db_session, monkeypatch, count=2)
    # Keep the dashboard fixture deterministic while exercising the real read-service dependency.
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed_read_only", lambda _self, _user_id: _context())
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A provider/ranking semantic factory was constructed by a Jobs GET.")

    app.dependency_overrides[get_job_ranking_service] = forbidden
    for dependency in (
        get_job_relevance_agent,
        get_job_archetype_agent,
        get_career_analysis_graph,
        get_job_analysis_service,
        get_requirement_matching_service,
    ):
        app.dependency_overrides[dependency] = forbidden
    monkeypatch.setattr(deps_module, "get_semantic_response_client", forbidden)
    try:
        headers = _headers("owner")
        assert client.get("/api/v1/jobs/opportunities", headers=headers).status_code == 200
        assert client.get("/api/v1/jobs/discovery-runs", headers=headers).status_code == 200
        assert client.get(f"/api/v1/jobs/discovery-runs/{run.id}", headers=headers).status_code == 200
        assert client.get(
            f"/api/v1/jobs/opportunities/{run.jobs[0].evaluation_id}", headers=headers
        ).status_code == 200
    finally:
        _clear_services()
        for dependency in (
            get_job_relevance_agent,
            get_job_archetype_agent,
            get_career_analysis_graph,
            get_job_analysis_service,
            get_requirement_matching_service,
        ):
            app.dependency_overrides.pop(dependency, None)
    assert jobs


def test_post_discovery_run_remains_ranking_backed(client, db_session, monkeypatch) -> None:
    jobs, _run, service, ranking = _prepared(db_session, monkeypatch)
    _override_services(service)
    monkeypatch.setattr(
        PersistedCandidateContextLoader, "load_confirmed",
        lambda _self, _user_id: CandidateContext(profile_text="changed current profile"),
    )
    try:
        response = client.post("/api/v1/jobs/discovery-runs", headers=_headers("owner"), json=_request([jobs[0].id]).model_dump(mode="json"))
    finally:
        _clear_services()
    assert response.status_code == 201
    assert ranking.calls == 2


def test_current_opportunity_summary_http_contract_and_authority_filters(client, db_session, monkeypatch) -> None:
    jobs, run, service, _ranking = _prepared(db_session, monkeypatch, count=3, company=None)
    _override_services(service)
    try:
        headers = _headers("owner")
        body = client.get("/api/v1/jobs/opportunities?limit=2", headers=headers).json()
        assert body["limit"] == 2 and body["truncated"] is True and len(body["items"]) == 2
        assert body["items"][0]["company"] is None
        assert "opportunity" not in body["items"][0] and "description" not in body["items"][0]
        assert client.get("/api/v1/jobs/opportunities?limit=0", headers=headers).status_code == 422
        assert client.get("/api/v1/jobs/opportunities?limit=101", headers=headers).status_code == 422
        evaluation_id = run.jobs[0].evaluation_id
        evaluated_job = db_session.get(DiscoveredJob, run.jobs[0].discovered_job_id)
        assert client.get(f"/api/v1/jobs/opportunities/{evaluation_id}", headers=_headers("other")).status_code == 404
        assert evaluated_job is not None
        evaluated_job.state = DiscoveredJobState.INACTIVE.value; db_session.commit()
        assert client.get(f"/api/v1/jobs/opportunities/{evaluation_id}", headers=headers).status_code == 404
        evaluated_job.state = DiscoveredJobState.UNCHANGED.value; evaluated_job.verification_status = "unverified"; db_session.commit()
        assert client.get(f"/api/v1/jobs/opportunities/{evaluation_id}", headers=headers).status_code == 404
    finally:
        _clear_services()


def test_current_detail_rejects_stale_fingerprints_and_content_hash(db_session, monkeypatch) -> None:
    jobs, run, service, _ranking = _prepared(db_session, monkeypatch)
    evaluation_id = run.jobs[0].evaluation_id
    assert service.current_opportunity_detail("owner", evaluation_id).job.title == jobs[0].title
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed_read_only", lambda _self, _user_id: CandidateContext(profile_text="changed"))
    try:
        service.current_opportunity_detail("owner", evaluation_id)
    except LookupError:
        pass
    else:
        raise AssertionError("A stale candidate fingerprint must not be current.")
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed_read_only", lambda _self, _user_id: _context())
    jobs[0].content_hash = "f" * 64; db_session.commit()
    try:
        service.current_opportunity_detail("owner", evaluation_id)
    except LookupError:
        pass
    else:
        raise AssertionError("A changed public job must not remain current.")


def test_current_detail_rejects_stale_evaluation_contract(db_session, monkeypatch) -> None:
    monkeypatch.setenv("CAREER_TRANS_DEPLOYMENT_REVISION", "contract-before")
    jobs, run, service, _ranking = _prepared(db_session, monkeypatch)
    evaluation_id = run.jobs[0].evaluation_id
    monkeypatch.setenv("CAREER_TRANS_DEPLOYMENT_REVISION", "contract-after")
    try:
        service.current_opportunity_detail("owner", evaluation_id)
    except LookupError:
        pass
    else:
        raise AssertionError("An evaluation from an old contract must not be current.")
    assert jobs


def test_run_read_models_are_bounded_historical_and_user_scoped(client, db_session, monkeypatch) -> None:
    jobs, run, service, _ranking = _prepared(db_session, monkeypatch, count=2)
    older = DiscoveryRun(user_id="owner", search_input_json="{}", search_input_fingerprint="a" * 64, candidate_evaluation_fingerprint="b" * 64, evaluation_contract_fingerprint="c" * 64, status="running", funnel_json="{}", failure_summary_json="{}", started_at=datetime.now(timezone.utc) - timedelta(days=1))
    db_session.add(older); db_session.commit()
    _override_services(service)
    try:
        headers = _headers("owner")
        listing = client.get("/api/v1/jobs/discovery-runs?limit=1", headers=headers)
        assert listing.status_code == 200 and listing.json()["truncated"] is True
        assert "jobs" not in listing.json()["items"][0]
        assert "candidate_evaluation_fingerprint" not in listing.text
        detail = client.get(f"/api/v1/jobs/discovery-runs/{run.id}", headers=headers)
        assert detail.status_code == 200 and detail.json()["jobs"][0]["opportunity"] is None
        assert client.get(f"/api/v1/jobs/discovery-runs/{run.id}", headers=_headers("other")).status_code == 404
        historical = client.get(f"/api/v1/jobs/discovery-runs/{run.id}/jobs/{jobs[0].id}", headers=headers)
        assert historical.status_code == 200 and historical.json()["opportunity"] is not None
        assert client.get(
            f"/api/v1/jobs/discovery-runs/{run.id}/jobs/{jobs[0].id}",
            headers=_headers("other"),
        ).status_code == 404
        assert client.get(f"/api/v1/jobs/discovery-runs/{run.id}/jobs/{jobs[1].id}-missing", headers=headers).status_code == 404
        assert client.get("/api/v1/jobs/discovery-runs?limit=0", headers=headers).status_code == 422
    finally:
        _clear_services()


def test_historical_empty_run_row_remains_null_without_current_reclassification(db_session, monkeypatch) -> None:
    jobs, run, service, _ranking = _prepared(db_session, monkeypatch)
    # The uniqueness constraint prevents duplicate same job; make a second public job that is not in the current evaluation.
    extra = _job(99); db_session.add(extra); db_session.commit()
    row = DiscoveryRunJob(discovery_run_id=run.id, discovered_job_id=extra.id, outcome="not_actionable")
    db_session.add(row); db_session.commit()
    historical = service.get_historical_run_job_detail("owner", run.id, extra.id)
    assert historical.evaluation_id is None and historical.opportunity is None


def test_inbox_summary_caps_provenance_and_shares_actionability(client, db_session, monkeypatch) -> None:
    active, inactive, unverified = _job(21), _job(22), _job(23)
    inactive.state = DiscoveredJobState.INACTIVE.value
    inactive.last_seen_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    unverified.verification_status = "unverified"
    db_session.add_all([_user("owner"), active, inactive, unverified])
    db_session.commit()
    now = datetime.now(timezone.utc)
    for index in range(4):
        db_session.add(DiscoveredJobProvenance(job_id=inactive.id, runtime="codex", source_ref=f"safe-{index}", discovered_via="search", fingerprint=f"{index:064x}", imported_at=now + timedelta(seconds=index)))
    db_session.commit()
    body = client.get("/api/v1/jobs/inbox?limit=2", headers=_headers("owner")).json()
    by_id = {item["discovered_job_id"]: item for item in body["items"]}
    assert body["truncated"] is True
    assert by_id[inactive.id]["actionable"] is False
    assert len(by_id[inactive.id]["provenance"]) == 3 and by_id[inactive.id]["provenance_count"] == 4
    assert "description" not in by_id[inactive.id]
    inbox_item = next(item for item in OpportunityInboxService(db_session).list_recent_summary(limit=10).items if item.discovered_job_id == inactive.id)
    assert inbox_item.actionable == UserJobDiscoveryService(db_session).is_currently_actionable(inactive)

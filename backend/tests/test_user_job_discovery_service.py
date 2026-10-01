from datetime import datetime, timedelta, timezone
import json
import runpy
from uuid import uuid4
from pathlib import Path

from sqlalchemy import select

from app.models.discovered_job import DiscoveredJob
from candidate_read_support import patch_candidate_context
from app.models.user_job_discovery import DiscoveryRun, DiscoveryRunJob, UserJobEvaluation
from app.models.user import User
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import (
    CandidateContext,
    CandidateEvidenceMaterializationStatus,
    CareerEvidence,
    CareerEvidenceProvenance,
)
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.discovery import DiscoveredJobState, JobSearchQuery
from app.schemas.job_ranking import JobArchetype, JobArchetypeAssessment, JobRankingResponse, JobRelevanceAssessment, PostingLegitimacy, PostingLegitimacyAssessment, RankedJobOpportunity
from app.schemas.recommendation import Recommendation, RecommendationAssessment
from app.schemas.user_job_discovery import DiscoveryRunCreateRequest
from app.schemas.user_job_decision import UserJobDecisionMutation
from app.services.user_job_discovery_service import DiscoveryRunExecutionFailure, UserJobDiscoveryService
from app.services.user_job_discovery_service import UserJobDiscoveryHistoryReadService
from app.services.user_job_decision_service import UserJobDecisionService
from candidate_read_support import StaticCandidateReader, patch_candidate_context, snapshot_for_context
from app.services.canonical_candidate_read_service import (
    CandidateEvidenceMaterializationIncomplete,
    CanonicalCandidateReadService,
)
from app.schemas.ai_settings import SemanticOperation
from app.services.llm_runtime import JOB_EVALUATION_OPERATIONS
from app.api.deps import get_user_job_discovery_service, get_user_job_discovery_read_service
from app.core.security import create_access_token
from app.core.config import Settings
import app.services.user_job_discovery_service as discovery_module
from app.main import app as fastapi_app
from test_jobs_read_models import _prepared as prepared_read_model


class _Ranking:
    def __init__(self) -> None:
        self.calls = 0

    def rank(self, request):
        self.calls += 1
        job = request.jobs[0]
        opportunity = RankedJobOpportunity(
            job=job, relevance=JobRelevanceAssessment(relevant=True, score=.9, reasoning="safe"),
            archetype=JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="safe"),
            fit_assessment=FitAssessment(fit_score=70),
            career_assessment=CareerAssessment(career_alignment_score=80, confidence=AlignmentConfidence.HIGH, dimensions=[], reasoning="safe"),
            recommendation_assessment=RecommendationAssessment(recommendation=Recommendation.APPLY, fit_score=70, career_alignment_score=80, career_alignment_confidence=AlignmentConfidence.HIGH, rule_id="safe", reasoning="safe"),
            legitimacy=PostingLegitimacyAssessment(legitimacy=PostingLegitimacy.HIGH_CONFIDENCE, reasoning="safe"), rank=1,
        )
        return JobRankingResponse(discovered_count=len(request.jobs), gated_out_count=0, relevance_screened_count=len(request.jobs), finalist_count=1, analysed_count=1, results=[opportunity])


def _context() -> CandidateContext:
    return CandidateContext(profile_text="Profile", skills_text="Python", evidence=[CareerEvidence(evidence_id="e1", title="Project", text="Python delivery", skills=["Python"], provenance=[CareerEvidenceProvenance(document_sha256="a" * 64, segment_ids=["s1"])])])


def _job(suffix: str = "1") -> DiscoveredJob:
    now = datetime.now(timezone.utc)
    return DiscoveredJob(identity_key=f"provider:j{suffix}", source="greenhouse", source_token="board", external_id=f"j{suffix}", title=f"AI Engineer {suffix}", company="Example", location="London", url=f"https://jobs.example/j{suffix}", description="Full verified job description with requirements.", detail_authority="verified_employer_detail", verification_status="verified", content_hash=("b" * 63) + suffix[-1], state=DiscoveredJobState.UNCHANGED.value, first_seen_at=now, last_seen_at=now, last_changed_at=now)


def _request(job_id: str | list[str], *, location: str = "London", threshold: float = .5, max_semantic: int = 10, max_full: int = 5) -> DiscoveryRunCreateRequest:
    ids = [job_id] if isinstance(job_id, str) else job_id
    return DiscoveryRunCreateRequest(query=JobSearchQuery(keywords=["AI"], locations=[location]), discovered_job_ids=ids, max_semantic_candidates=max_semantic, max_full_analyses=max_full, min_relevance_score=threshold)


def _user(user_id: str) -> User:
    return User(id=user_id, email=f"{user_id}@example.test", password_hash="safe-password-hash")


def test_unchanged_job_is_new_to_user_then_reused_without_ranking(db_session, monkeypatch, runtime_snapshot_a) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking, runtime_snapshot=runtime_snapshot_a)
    first = service.start("user-a", _request(job.id))
    second = service.start("user-a", _request(job.id))
    assert ranking.calls == 1
    assert first.jobs[0].outcome == "newly_evaluated"
    assert second.jobs[0].outcome == "reused_evaluation"
    assert len(db_session.query(UserJobEvaluation).all()) == 1
    persisted = db_session.query(UserJobEvaluation).one()
    attribution = json.loads(persisted.runtime_attribution_json)
    assert set(attribution["operations"]) == {operation.value for operation in (
        SemanticOperation.JOB_RELEVANCE, SemanticOperation.JOB_ARCHETYPE,
        SemanticOperation.JOB_EXTRACTION, SemanticOperation.REQUIREMENT_MATCHING,
        SemanticOperation.CAREER_ALIGNMENT,
    )}
    assert attribution["provider"] == runtime_snapshot_a.provider
    for operation in JOB_EVALUATION_OPERATIONS:
        actual = attribution["operations"][operation.value]
        assert actual == {
            "model": runtime_snapshot_a.operation(operation).model,
            "reasoning_effort": runtime_snapshot_a.operation(operation).reasoning_effort.value if runtime_snapshot_a.operation(operation).reasoning_effort else None,
        }
    original_attribution = persisted.runtime_attribution_json
    assert UserJobDiscoveryHistoryReadService(db_session).get_historical_run_job_detail("user-a", first.id, job.id).runtime_attribution.status == "available"
    assert UserJobDiscoveryHistoryReadService(db_session).get_historical_run_job_detail("user-a", second.id, job.id).runtime_attribution.model_dump_json() == UserJobDiscoveryHistoryReadService(db_session).get_historical_run_job_detail("user-a", first.id, job.id).runtime_attribution.model_dump_json()
    assert db_session.query(UserJobEvaluation).one().runtime_attribution_json == original_attribution
    persisted.runtime_attribution_json = None
    db_session.commit()
    third = service.start("user-a", _request(job.id))
    assert third.jobs[0].outcome == "reused_evaluation"
    assert persisted.runtime_attribution_json is None
    assert UserJobDiscoveryHistoryReadService(db_session).get_historical_run_job_detail("user-a", third.id, job.id).runtime_attribution.status == "legacy_unavailable"


def test_candidate_change_and_inactive_job_do_not_reuse(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    context = _context(); patch_candidate_context(monkeypatch, context)
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    service.start("user-a", _request(job.id))
    context.evidence[0].text = "Changed current evidence"
    service.start("user-a", _request(job.id))
    assert ranking.calls == 2
    job.state = DiscoveredJobState.INACTIVE.value; db_session.commit()
    assert service.current_opportunities("user-a").opportunities == []


def test_start_preserves_missing_profile_and_incomplete_evidence_errors_without_run_rows(db_session) -> None:
    job = _job()
    db_session.add_all([_user("user-a"), job])
    db_session.commit()

    missing_reader = StaticCandidateReader(
        snapshot_for_context(_context(), structured_profile_available=False)
    )
    ranking = _Ranking()
    missing_service = UserJobDiscoveryService(
        db_session, ranking_service=ranking, candidate_reader=missing_reader
    )
    try:
        missing_service.start("user-a", _request(job.id))
    except ValueError as exc:
        assert str(exc) == "Candidate profile is not ready."
    else:
        raise AssertionError("Missing structured Profile must retain its readiness error.")
    assert db_session.scalars(select(DiscoveryRun)).all() == []
    assert ranking.calls == 0

    incomplete_reader = StaticCandidateReader(
        snapshot_for_context(
            _context(),
            evidence_status=CandidateEvidenceMaterializationStatus.INCOMPLETE,
        )
    )
    incomplete_service = UserJobDiscoveryService(
        db_session, ranking_service=ranking, candidate_reader=incomplete_reader
    )
    try:
        incomplete_service.start("user-a", _request(job.id))
    except ValueError as exc:
        assert str(exc) == "Current candidate evidence is not fully materialised."
        assert isinstance(exc.__cause__, CandidateEvidenceMaterializationIncomplete)
    else:
        raise AssertionError("Incomplete evidence must retain its distinct readiness error.")
    assert db_session.scalars(select(DiscoveryRun)).all() == []
    assert ranking.calls == 0


def test_current_opportunities_return_empty_for_missing_or_incomplete_candidate(db_session) -> None:
    db_session.add(_user("user-a")); db_session.commit()
    for snapshot in (
        snapshot_for_context(_context(), structured_profile_available=False),
        snapshot_for_context(
            _context(),
            evidence_status=CandidateEvidenceMaterializationStatus.INCOMPLETE,
        ),
    ):
        reader = StaticCandidateReader(snapshot)
        service = UserJobDiscoveryService(db_session, candidate_reader=reader)
        assert service.current_opportunities("user-a").opportunities == []
        assert reader.read_user_ids == ["user-a"]


def test_supplied_context_bypasses_canonical_reader_for_current_evaluation(db_session) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    context = _context()
    reader = StaticCandidateReader(snapshot_for_context(context))
    service = UserJobDiscoveryService(
        db_session, ranking_service=_Ranking(), candidate_reader=reader
    )
    service.start("user-a", _request(job.id))
    reader.read_user_ids.clear()
    fingerprint_before = service.candidate_evaluation_fingerprint(context)
    result = service.current_evaluation_for_job("user-a", job, candidate_context=context)
    # The fixture evaluation intentionally has no requirement-match detail, so
    # the method rejects it after checking currentness. The supplied context is
    # still used without consulting the reader.
    assert result is None
    assert reader.read_user_ids == []
    assert service.candidate_evaluation_fingerprint(context) == fingerprint_before


def test_current_evaluation_returns_none_for_missing_or_incomplete_candidate(db_session) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    snapshots = (
        snapshot_for_context(_context(), structured_profile_available=False),
        snapshot_for_context(
            _context(),
            evidence_status=CandidateEvidenceMaterializationStatus.INCOMPLETE,
        ),
    )
    for snapshot in snapshots:
        reader = StaticCandidateReader(snapshot)
        service = UserJobDiscoveryService(db_session, candidate_reader=reader)
        assert service.current_evaluation_for_job("user-a", job) is None
        assert reader.read_user_ids == ["user-a"]


def test_user_evaluation_and_run_are_isolated(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), _user("user-b"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    run_a = service.start("user-a", _request(job.id))
    run_b = service.start("user-b", _request(job.id))
    assert ranking.calls == 2
    assert service.current_opportunities("user-a").opportunities[0].evaluation_id != service.current_opportunities("user-b").opportunities[0].evaluation_id
    try:
        service.get_run("user-b", run_a.id)
    except LookupError:
        pass
    else:
        raise AssertionError("A user must not read another user's run.")
    assert run_b.jobs[0].outcome == "newly_evaluated"


def test_contract_change_creates_new_immutable_evaluation(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    first = service.start("user-a", _request(job.id))
    monkeypatch.setenv("CAREER_TRANS_DEPLOYMENT_REVISION", "changed-contract")
    second = service.start("user-a", _request(job.id))
    assert ranking.calls == 2
    assert first.jobs[0].evaluation_id != second.jobs[0].evaluation_id
    assert len(db_session.query(UserJobEvaluation).all()) == 2


def test_historical_run_keeps_original_immutable_opportunity_snapshot(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    context = _context(); patch_candidate_context(monkeypatch, context)
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    run_a = service.start("user-a", _request(job.id))
    original = run_a.jobs[0].opportunity.fit_assessment.fit_score
    context.evidence[0].text = "Material changed evidence"
    run_b = service.start("user-a", _request(job.id))
    assert service.get_run("user-a", run_a.id).jobs[0].opportunity.fit_assessment.fit_score == original
    assert run_a.jobs[0].evaluation_id != run_b.jobs[0].evaluation_id


def test_incompatible_current_geography_does_not_reuse_historical_evaluation(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    service.start("user-a", _request(job.id, location="London"))
    incompatible = service.start("user-a", _request(job.id, location="New York"))
    assert ranking.calls == 1
    assert incompatible.jobs[0].outcome == "presemantic_filtered"
    assert incompatible.jobs[0].evaluation_id is None


def test_reused_evaluation_respects_new_relevance_threshold_without_reranking(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    service.start("user-a", _request(job.id, threshold=.5))
    run = service.start("user-a", _request(job.id, threshold=.95))
    assert ranking.calls == 1
    assert run.jobs[0].outcome == "semantic_rejected"
    assert run.jobs[0].evaluation_id is not None


def test_unexpected_ranking_exception_terminalizes_run_safely(db_session, monkeypatch) -> None:
    class BrokenRanking:
        def rank(self, _request):
            raise RuntimeError("private provider detail")
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    service = UserJobDiscoveryService(db_session, ranking_service=BrokenRanking())
    failed_run_id = None
    try:
        service.start("user-a", _request(job.id))
    except DiscoveryRunExecutionFailure as exc:
        failed_run_id = exc.run_id
        assert exc.status.value == "failed"
    else:
        raise AssertionError("Unexpected ranking failure must be re-raised.")
    run = service.list_runs("user-a")[0]
    assert run.id == failed_run_id
    assert run.status == "failed" and run.completed_at is not None
    assert "private" not in str(run.failure_summary)


def test_repeated_client_request_id_returns_the_run_committed_before_provider_failure(db_session, monkeypatch) -> None:
    class BrokenRanking:
        def rank(self, _request):
            raise RuntimeError("synthetic provider failure")

    job, other = _job(), _job("other"); db_session.add_all([_user("user-a"), job, other]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    request_id = uuid4()
    request = _request(job.id).model_copy(update={"client_request_id": request_id})
    service = UserJobDiscoveryService(db_session, ranking_service=BrokenRanking())
    try:
        service.start("user-a", request)
    except DiscoveryRunExecutionFailure as exc:
        created_id = exc.run_id
    else:
        raise AssertionError("The synthetic provider error should happen after the run commits.")

    db_session.expire_all()
    persisted = db_session.get(DiscoveryRun, created_id)
    assert persisted is not None
    assert json.loads(persisted.search_input_json)["client_request_id"] == str(request_id)

    # A retry with the same key returns the durable outcome even if the ranking
    # service is currently unavailable; it cannot create a second logical run.
    retried = UserJobDiscoveryService(db_session).start("user-a", request)
    assert retried.id == created_id
    assert retried.status == "failed"
    try:
        UserJobDiscoveryService(db_session).start(
            "user-a", request.model_copy(update={"discovered_job_ids": [other.id]})
        )
    except ValueError as exc:
        assert "different evaluation input" in str(exc)
    else:
        raise AssertionError("The same request ID must not be reused with a different job selection.")
    assert len(db_session.scalars(select(DiscoveryRun).where(DiscoveryRun.user_id == "user-a")).all()) == 1


def test_reused_success_then_ranking_failure_retains_partial_status_and_early_link(db_session, monkeypatch) -> None:
    first, second = _job("1"), _job("2")
    db_session.add_all([_user("user-a"), first, second]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    UserJobDiscoveryService(db_session, ranking_service=_Ranking()).start("user-a", _request(first.id))
    linked_ids: list[str] = []

    class BrokenRanking:
        def rank(self, _request):
            assert len(linked_ids) == 1
            assert db_session.get(DiscoveryRun, linked_ids[0]).status == "running"
            raise RuntimeError("private provider detail")

    service = UserJobDiscoveryService(db_session, ranking_service=BrokenRanking())
    try:
        service.start("user-a", _request([first.id, second.id]), link_run=linked_ids.append)
    except DiscoveryRunExecutionFailure as exc:
        assert exc.run_id == linked_ids[0]
        assert exc.status.value == "partial_failed"
    else:
        raise AssertionError("The ranking failure must retain its terminal status.")
    run = service.get_run("user-a", linked_ids[0])
    assert run.status.value == "partial_failed"
    assert {row.outcome.value for row in run.jobs} == {"reused_evaluation", "analysis_failed"}


def test_authenticated_run_and_opportunity_routes_enforce_user_scope(client, db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), _user("user-b"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    service = UserJobDiscoveryService(db_session, ranking_service=_Ranking())
    fastapi_app.dependency_overrides[get_user_job_discovery_service] = lambda: service
    fastapi_app.dependency_overrides[get_user_job_discovery_read_service] = lambda: service
    headers_a = {"Authorization": f"Bearer {create_access_token('user-a')}"}
    headers_b = {"Authorization": f"Bearer {create_access_token('user-b')}"}
    payload = _request(job.id).model_dump(mode="json")
    try:
        created = client.post("/api/v1/jobs/discovery-runs", json=payload, headers=headers_a)
        assert created.status_code == 201
        run_id = created.json()["id"]
        assert client.get("/api/v1/jobs/discovery-runs", headers=headers_a).status_code == 200
        assert client.get(f"/api/v1/jobs/discovery-runs/{run_id}", headers=headers_b).status_code == 404
        historical_path = f"/api/v1/jobs/discovery-runs/{run_id}/jobs/{job.id}"
        owner_history = client.get(historical_path, headers=headers_a)
        assert owner_history.status_code == 200
        assert owner_history.json()["runtime_attribution"]["status"] == "available"
        assert client.get(historical_path, headers=headers_b).status_code == 404
        assert client.get("/api/v1/jobs/opportunities", headers=headers_a).json()["items"]
        assert client.get("/api/v1/jobs/opportunities", headers=headers_b).json()["items"] == []
    finally:
        fastapi_app.dependency_overrides.pop(get_user_job_discovery_service, None)
        fastapi_app.dependency_overrides.pop(get_user_job_discovery_read_service, None)


def test_search_history_and_inbox_reads_work_after_existing_sqlite_schema_upgrade(client, db_session) -> None:
    connection = db_session.connection()
    sqlite_connection = connection.connection.driver_connection
    connection.exec_driver_sql("DROP TABLE IF EXISTS discovery_run_jobs")
    connection.exec_driver_sql("DROP TABLE discovery_runs")
    connection.exec_driver_sql(
        "CREATE TABLE discovery_runs ("
        "id VARCHAR(36) NOT NULL, user_id VARCHAR(36) NOT NULL, "
        "search_input_json TEXT NOT NULL, search_input_fingerprint VARCHAR(64) NOT NULL, "
        "candidate_evaluation_fingerprint VARCHAR(64) NOT NULL, "
        "evaluation_contract_fingerprint VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL, "
        "funnel_json TEXT NOT NULL, failure_summary_json TEXT NOT NULL, started_at DATETIME NOT NULL, "
        "completed_at DATETIME, PRIMARY KEY (id), "
        "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE)"
    )
    db_session.commit()

    migration = Path(__file__).resolve().parents[1] / "migrations" / "20261002_discovery_run_request_id_sqlite.py"
    upgrade = runpy.run_path(str(migration))["upgrade"]
    upgrade(sqlite_connection)
    db_session.expire_all()

    job = _job()
    job.source = "agent_runtime"
    db_session.add_all([
        _user("user-a"),
        job,
        DiscoveryRun(
            id="legacy-run-after-upgrade",
            user_id="user-a",
            search_input_json='{"query":{"keywords":["ai"]}}',
            search_input_fingerprint="s" * 64,
            candidate_evaluation_fingerprint="c" * 64,
            evaluation_contract_fingerprint="e" * 64,
            status="completed",
            funnel_json="{}",
            failure_summary_json="{}",
        ),
    ])
    db_session.commit()

    headers = {"Authorization": f"Bearer {create_access_token('user-a')}"}
    history = client.get("/api/v1/jobs/search-history?limit=20", headers=headers)
    inbox = client.get("/api/v1/jobs/inbox?limit=20", headers=headers)
    assert history.status_code == 200
    assert any(item["id"] == "legacy-run-after-upgrade" for item in history.json()["items"])
    assert inbox.status_code == 200
    assert any(item["discovered_job_id"] == job.id for item in inbox.json()["items"])


def test_read_models_revalidate_current_recency_without_mutating_historical_snapshot(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    service = UserJobDiscoveryService(db_session, ranking_service=_Ranking())
    run = service.start("user-a", _request(job.id))
    evaluation = db_session.get(UserJobEvaluation, run.jobs[0].evaluation_id)
    stored = evaluation.evaluation_json
    job.posted_at = datetime.now(timezone.utc) - timedelta(days=365); db_session.commit()
    current = service.current_opportunity_detail("user-a", evaluation.id)
    historical = service.get_historical_run_job_detail("user-a", run.id, job.id)
    assert current.legitimacy.legitimacy == PostingLegitimacy.PROCEED_WITH_CAUTION
    assert historical.opportunity.legitimacy.legitimacy == PostingLegitimacy.HIGH_CONFIDENCE
    assert db_session.get(UserJobEvaluation, evaluation.id).evaluation_json == stored


def test_shared_actionability_rejects_inactive_and_unverified_jobs(db_session) -> None:
    from app.services.public_job_actionability import is_public_job_actionable
    active = _job("1")
    inactive = _job("2"); inactive.state = DiscoveredJobState.INACTIVE.value
    unverified = _job("3"); unverified.verification_status = "unverified"
    assert is_public_job_actionable(active) is True
    assert is_public_job_actionable(inactive) is False
    assert is_public_job_actionable(unverified) is False


def test_contract_fingerprint_uses_local_revision_and_relevant_configuration(db_session, monkeypatch) -> None:
    ranking = _Ranking()
    monkeypatch.delenv("CAREER_TRANS_DEPLOYMENT_REVISION", raising=False)
    monkeypatch.setattr(discovery_module.subprocess, "check_output", lambda *_a, **_k: "revision-a\n")
    discovery_module._CACHED_APPLICATION_REVISION = None
    first = UserJobDiscoveryService(db_session, ranking_service=ranking, settings=Settings()).evaluation_contract_fingerprint()
    same = UserJobDiscoveryService(db_session, ranking_service=ranking, settings=Settings()).evaluation_contract_fingerprint()
    discovery_module._CACHED_APPLICATION_REVISION = None
    monkeypatch.setattr(discovery_module.subprocess, "check_output", lambda *_a, **_k: "revision-b\n")
    changed_revision = UserJobDiscoveryService(db_session, ranking_service=ranking, settings=Settings()).evaluation_contract_fingerprint()
    changed_model = UserJobDiscoveryService(db_session, ranking_service=ranking, settings=Settings(job_relevance_model="gpt-5.6-sol")).evaluation_contract_fingerprint()
    assert first == same
    assert first != changed_revision
    assert changed_revision != changed_model


def test_search_fingerprint_normalises_harmless_order() -> None:
    first = JobSearchQuery(keywords=[" AI ", "Engineer"], locations=["London", "UK"], companies=[" Acme "], excluded_companies=[" Other "], excluded_title_terms=["Intern"], employment_types=["Full Time"])
    second = JobSearchQuery(keywords=["engineer", "ai", "AI"], locations=["uk", " london "], companies=["acme"], excluded_companies=["other"], excluded_title_terms=[" intern "], employment_types=["full time"])
    assert UserJobDiscoveryService.search_input_fingerprint(first) == UserJobDiscoveryService.search_input_fingerprint(second)


def test_historical_run_input_includes_execution_budgets(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    service = UserJobDiscoveryService(db_session, ranking_service=_Ranking())
    run_a = service.start("user-a", _request(job.id, max_semantic=3, max_full=2, threshold=.8))
    service.start("user-a", _request(job.id, max_semantic=9, max_full=4, threshold=.5))
    historical = service.get_run("user-a", run_a.id)
    assert historical.run_input["max_semantic_candidates"] == 3
    assert historical.run_input["max_full_analyses"] == 2
    assert historical.run_input["min_relevance_score"] == .8
    assert historical.run_input["query"]["keywords"] == ["ai"]


def test_job_content_change_creates_new_evaluation_and_preserves_old_run(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    old = service.start("user-a", _request(job.id))
    job.content_hash = "c" * 64; db_session.commit()
    new = service.start("user-a", _request(job.id))
    assert ranking.calls == 2
    assert old.jobs[0].evaluation_id != new.jobs[0].evaluation_id
    assert service.get_run("user-a", old.id).jobs[0].evaluation_id == old.jobs[0].evaluation_id


def test_candidate_fingerprint_ignores_source_name_but_tracks_stage_inputs() -> None:
    context = _context(); baseline = UserJobDiscoveryService.candidate_evaluation_fingerprint(context)
    context.source_name = "storage-only source"
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(context) == baseline
    context.career_strategy_text = "New direction"
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(context) != baseline
    context = _context(); context.eligibility.locations = ["London"]
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(context) != baseline
    context = _context(); context.evidence[0].text = "Changed evidence"
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(context) != baseline


def test_canonical_projection_preserves_known_candidate_fingerprint() -> None:
    snapshot = snapshot_for_context(_context())
    context = CanonicalCandidateReadService.candidate_context(
        snapshot,
        require_structured_profile=True,
        require_complete_evidence=True,
    )
    assert context is not None
    assert UserJobDiscoveryService.candidate_evaluation_fingerprint(context) == (
        "e7b847faec6feb43392d581e03eb3753fe8b8da3b0792f2ea7c4ff50689d463d"
    )


def test_mixed_reuse_and_unexpected_failure_is_partial_failed(db_session, monkeypatch) -> None:
    class BrokenRanking:
        def rank(self, _request):
            raise RuntimeError("private provider body")
    job_a, job_b = _job("1"), _job("2")
    db_session.add_all([_user("user-a"), job_a, job_b]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    service = UserJobDiscoveryService(db_session, ranking_service=_Ranking())
    first_run = service.start("user-a", _request(job_a.id))
    prior_run_ids = {first_run.id}
    service = UserJobDiscoveryService(db_session, ranking_service=BrokenRanking())
    try:
        service.start("user-a", _request([job_a.id, job_b.id]))
    except RuntimeError:
        pass
    run = next(item for item in service.list_runs("user-a") if item.id not in prior_run_ids)
    outcomes = {item.discovered_job_id: item.outcome for item in run.jobs}
    assert run.status == "partial_failed" and run.completed_at is not None, (run.status, outcomes)
    assert outcomes[job_a.id] == "reused_evaluation"
    assert outcomes[job_b.id] == "analysis_failed"
    assert "private" not in str(run.failure_summary)


def test_current_opportunities_ignore_historical_rank_for_deterministic_order(db_session, monkeypatch) -> None:
    jobs = [_job("1"), _job("2"), _job("3")]
    db_session.add_all([_user("user-a"), *jobs]); db_session.commit()
    context = _context(); patch_candidate_context(monkeypatch, context)
    service = UserJobDiscoveryService(db_session, ranking_service=_Ranking())
    candidate, contract = service.candidate_evaluation_fingerprint(context), service.evaluation_contract_fingerprint()
    specs = [(Recommendation.CONSIDER, 99, 99), (Recommendation.APPLY, 60, 70), (Recommendation.APPLY, 80, 60)]
    for job, (recommendation, fit, historical_rank) in zip(jobs, specs, strict=True):
        listing = service._listing(job)
        base = _Ranking().rank(type("Request", (), {"jobs": [listing]})()).results[0]
        assessment = base.recommendation_assessment.model_copy(update={"recommendation": recommendation, "fit_score": fit})
        opportunity = base.model_copy(update={"rank": historical_rank, "fit_assessment": FitAssessment(fit_score=fit), "recommendation_assessment": assessment})
        db_session.add(UserJobEvaluation(user_id="user-a", discovered_job_id=job.id, job_content_hash=job.content_hash, candidate_evaluation_fingerprint=candidate, evaluation_contract_fingerprint=contract, job_snapshot_json="{}", evaluation_json=opportunity.model_dump_json()))
    db_session.commit()
    ordered = service.current_opportunities("user-a").opportunities
    assert [item.discovered_job_id for item in ordered] == [jobs[2].id, jobs[1].id, jobs[0].id]


def test_duplicate_evaluation_materialisation_reconciles_to_existing_version(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    context = _context(); patch_candidate_context(monkeypatch, context)
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    first = service.start("user-a", _request(job.id))
    existing_id = first.jobs[0].evaluation_id
    existing_attribution = db_session.get(UserJobEvaluation, existing_id).runtime_attribution_json
    run = DiscoveryRun(user_id="user-a", search_input_json="{}", search_input_fingerprint="s" * 64, candidate_evaluation_fingerprint=service.candidate_evaluation_fingerprint(context), evaluation_contract_fingerprint=service.evaluation_contract_fingerprint())
    db_session.add(run); db_session.commit()
    row = DiscoveryRunJob(discovery_run_id=run.id, discovered_job_id=job.id, outcome="analysis_failed")
    db_session.add(row); db_session.commit()
    response = ranking.rank(type("Request", (), {"jobs": [service._listing(job)]})())
    service._persist_ranking(run, {job.id: row}, {service._listing_key(service._listing(job)): job.id}, response, "user-a", service.candidate_evaluation_fingerprint(context), service.evaluation_contract_fingerprint(), .5)
    db_session.commit()
    assert db_session.query(UserJobEvaluation).count() == 1
    assert row.evaluation_id == existing_id
    assert db_session.get(UserJobEvaluation, existing_id).runtime_attribution_json == existing_attribution


def test_historical_evaluation_attribution_is_immutable_across_runtime_change_and_legacy_null(db_session, monkeypatch, runtime_snapshot_a):
    from app.services.llm_runtime import resolve_runtime_snapshot
    from app.schemas.user_job_discovery import DiscoveryRunDetailRead, DiscoveryRunSummaryRead

    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    service = UserJobDiscoveryService(db_session, ranking_service=_Ranking(), runtime_snapshot=runtime_snapshot_a)
    run = service.start("user-a", _request(job.id))
    evaluation = db_session.get(UserJobEvaluation, run.jobs[0].evaluation_id)
    original = evaluation.runtime_attribution_json
    service._runtime_snapshot = resolve_runtime_snapshot(Settings(default_llm_provider="openai", job_relevance_model="gpt-5.6-sol"))
    historical = UserJobDiscoveryHistoryReadService(db_session).get_historical_run_job_detail("user-a", run.id, job.id).runtime_attribution
    assert historical.model_dump(mode="json") == json.loads(original)

    evaluation.runtime_attribution_json = None
    db_session.commit()
    assert UserJobDiscoveryHistoryReadService(db_session).get_historical_run_job_detail("user-a", run.id, job.id).runtime_attribution.status == "legacy_unavailable"

    row = db_session.query(DiscoveryRunJob).filter_by(discovery_run_id=run.id, discovered_job_id=job.id).one()
    row.evaluation_id = None
    db_session.commit()
    detail = UserJobDiscoveryHistoryReadService(db_session).get_historical_run_job_detail("user-a", run.id, job.id)
    assert detail.runtime_attribution is None
    assert "runtime_attribution" not in DiscoveryRunSummaryRead.model_fields
    assert "runtime_attribution" not in DiscoveryRunDetailRead.model_fields


def test_historical_discovery_http_gets_are_provider_and_runtime_free(client, db_session, monkeypatch):
    from app.api import deps

    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    patch_candidate_context(monkeypatch, _context())
    run = UserJobDiscoveryService(db_session, ranking_service=_Ranking()).start("user-a", _request(job.id))
    headers = {"Authorization": f"Bearer {create_access_token('user-a')}"}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("historical discovery GET resolved current runtime/provider")

    monkeypatch.setattr(deps, "get_user_runtime_snapshot", forbidden)
    monkeypatch.setattr(deps, "get_semantic_response_client", forbidden)
    assert client.get("/api/v1/jobs/discovery-runs", headers=headers).status_code == 200
    assert client.get(f"/api/v1/jobs/discovery-runs/{run.id}", headers=headers).status_code == 200
    detail = client.get(f"/api/v1/jobs/discovery-runs/{run.id}/jobs/{job.id}", headers=headers)
    assert detail.status_code == 200 and detail.json()["runtime_attribution"]["status"] == "available"


def test_current_opportunity_routes_remain_runtime_aware():
    from app.api.deps import get_user_runtime_snapshot, get_user_job_discovery_read_service
    from app.api.routes.jobs import router as jobs_router

    routes = [item for item in jobs_router.routes if getattr(item, "path", None) in {"/jobs/opportunities", "/jobs/opportunities/{evaluation_id}"}]
    assert len(routes) == 2
    for route in routes:
        dependency = next(item for item in route.dependant.dependencies if item.call is get_user_job_discovery_read_service)
        assert any(item.call is get_user_runtime_snapshot for item in dependency.dependencies)


def test_current_opportunities_filter_owner_dismissals_before_slice_and_detail_remains_readable(db_session, monkeypatch):
    jobs, run, service, _ranking = prepared_read_model(db_session, monkeypatch, count=4)
    decisions = UserJobDecisionService(db_session)
    decisions.mutate("owner", jobs[0].id, UserJobDecisionMutation(decision="dismissed"))
    decisions.mutate("other", jobs[1].id, UserJobDecisionMutation(decision="dismissed"))
    decisions.mutate("owner", jobs[2].id, UserJobDecisionMutation(decision="shortlisted"))

    summaries = service.current_opportunity_summaries("owner", limit=2)
    assert len(summaries.items) == 2
    assert jobs[0].id not in {item.discovered_job_id for item in summaries.items}
    assert jobs[2].id in {item.discovered_job_id for item in summaries.items}
    assert summaries.truncated is True
    assert [item.decision.decision for item in summaries.items] == ["undecided", "shortlisted"]
    dismissed_evaluation = next(item for item in run.jobs if item.discovered_job_id == jobs[0].id)
    assert service.current_opportunity_detail("owner", dismissed_evaluation.evaluation_id).job.title == jobs[0].title

from datetime import datetime, timezone

from app.models.discovered_job import DiscoveredJob
from app.models.user_job_discovery import DiscoveryRun, DiscoveryRunJob, UserJobEvaluation
from app.models.user import User
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext, CareerEvidence, CareerEvidenceProvenance
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.discovery import DiscoveredJobState, JobSearchQuery
from app.schemas.job_ranking import JobArchetype, JobArchetypeAssessment, JobRankingResponse, JobRelevanceAssessment, PostingLegitimacy, PostingLegitimacyAssessment, RankedJobOpportunity
from app.schemas.recommendation import Recommendation, RecommendationAssessment
from app.schemas.user_job_discovery import DiscoveryRunCreateRequest
from app.services.cv_ingestion_service import PersistedCandidateContextLoader
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.api.deps import get_user_job_discovery_service, get_user_job_discovery_read_service
from app.core.security import create_access_token
from app.core.config import Settings
import app.services.user_job_discovery_service as discovery_module
from app.main import app as fastapi_app


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


def test_unchanged_job_is_new_to_user_then_reused_without_ranking(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed_read_only", lambda _self, _user: _context())
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    first = service.start("user-a", _request(job.id))
    second = service.start("user-a", _request(job.id))
    assert ranking.calls == 1
    assert first.jobs[0].outcome == "newly_evaluated"
    assert second.jobs[0].outcome == "reused_evaluation"
    assert len(db_session.query(UserJobEvaluation).all()) == 1


def test_candidate_change_and_inactive_job_do_not_reuse(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    context = _context(); monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: context)
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    service.start("user-a", _request(job.id))
    context.evidence[0].text = "Changed current evidence"
    service.start("user-a", _request(job.id))
    assert ranking.calls == 2
    job.state = DiscoveredJobState.INACTIVE.value; db_session.commit()
    assert service.current_opportunities("user-a").opportunities == []


def test_user_evaluation_and_run_are_isolated(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), _user("user-b"), job]); db_session.commit()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
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
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    first = service.start("user-a", _request(job.id))
    monkeypatch.setenv("CAREER_TRANS_DEPLOYMENT_REVISION", "changed-contract")
    second = service.start("user-a", _request(job.id))
    assert ranking.calls == 2
    assert first.jobs[0].evaluation_id != second.jobs[0].evaluation_id
    assert len(db_session.query(UserJobEvaluation).all()) == 2


def test_historical_run_keeps_original_immutable_opportunity_snapshot(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    context = _context(); monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: context)
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    run_a = service.start("user-a", _request(job.id))
    original = run_a.jobs[0].opportunity.fit_assessment.fit_score
    context.evidence[0].text = "Material changed evidence"
    run_b = service.start("user-a", _request(job.id))
    assert service.get_run("user-a", run_a.id).jobs[0].opportunity.fit_assessment.fit_score == original
    assert run_a.jobs[0].evaluation_id != run_b.jobs[0].evaluation_id


def test_incompatible_current_geography_does_not_reuse_historical_evaluation(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    service.start("user-a", _request(job.id, location="London"))
    incompatible = service.start("user-a", _request(job.id, location="New York"))
    assert ranking.calls == 1
    assert incompatible.jobs[0].outcome == "presemantic_filtered"
    assert incompatible.jobs[0].evaluation_id is None


def test_reused_evaluation_respects_new_relevance_threshold_without_reranking(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
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
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
    service = UserJobDiscoveryService(db_session, ranking_service=BrokenRanking())
    try:
        service.start("user-a", _request(job.id))
    except RuntimeError:
        pass
    else:
        raise AssertionError("Unexpected ranking failure must be re-raised.")
    run = service.list_runs("user-a")[0]
    assert run.status == "failed" and run.completed_at is not None
    assert "private" not in str(run.failure_summary)


def test_authenticated_run_and_opportunity_routes_enforce_user_scope(client, db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), _user("user-b"), job]); db_session.commit()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed_read_only", lambda _self, _user: _context())
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
        assert client.get("/api/v1/jobs/opportunities", headers=headers_a).json()["items"]
        assert client.get("/api/v1/jobs/opportunities", headers=headers_b).json()["items"] == []
    finally:
        fastapi_app.dependency_overrides.pop(get_user_job_discovery_service, None)
        fastapi_app.dependency_overrides.pop(get_user_job_discovery_read_service, None)


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
    changed_model = UserJobDiscoveryService(db_session, ranking_service=ranking, settings=Settings(job_relevance_model="another-model")).evaluation_contract_fingerprint()
    assert first == same
    assert first != changed_revision
    assert changed_revision != changed_model


def test_search_fingerprint_normalises_harmless_order() -> None:
    first = JobSearchQuery(keywords=[" AI ", "Engineer"], locations=["London", "UK"], companies=[" Acme "], excluded_companies=[" Other "], excluded_title_terms=["Intern"], employment_types=["Full Time"])
    second = JobSearchQuery(keywords=["engineer", "ai", "AI"], locations=["uk", " london "], companies=["acme"], excluded_companies=["other"], excluded_title_terms=[" intern "], employment_types=["full time"])
    assert UserJobDiscoveryService.search_input_fingerprint(first) == UserJobDiscoveryService.search_input_fingerprint(second)


def test_historical_run_input_includes_execution_budgets(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
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
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
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


def test_mixed_reuse_and_unexpected_failure_is_partial_failed(db_session, monkeypatch) -> None:
    class BrokenRanking:
        def rank(self, _request):
            raise RuntimeError("private provider body")
    job_a, job_b = _job("1"), _job("2")
    db_session.add_all([_user("user-a"), job_a, job_b]); db_session.commit()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
    service = UserJobDiscoveryService(db_session, ranking_service=_Ranking())
    service.start("user-a", _request(job_a.id))
    service = UserJobDiscoveryService(db_session, ranking_service=BrokenRanking())
    try:
        service.start("user-a", _request([job_a.id, job_b.id]))
    except RuntimeError:
        pass
    run = service.list_runs("user-a")[0]
    outcomes = {item.discovered_job_id: item.outcome for item in run.jobs}
    assert run.status == "partial_failed" and run.completed_at is not None
    assert outcomes[job_a.id] == "reused_evaluation"
    assert outcomes[job_b.id] == "analysis_failed"
    assert "private" not in str(run.failure_summary)


def test_current_opportunities_ignore_historical_rank_for_deterministic_order(db_session, monkeypatch) -> None:
    jobs = [_job("1"), _job("2"), _job("3")]
    db_session.add_all([_user("user-a"), *jobs]); db_session.commit()
    context = _context(); monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: context)
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
    context = _context(); monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: context)
    ranking = _Ranking(); service = UserJobDiscoveryService(db_session, ranking_service=ranking)
    first = service.start("user-a", _request(job.id))
    existing_id = first.jobs[0].evaluation_id
    run = DiscoveryRun(user_id="user-a", search_input_json="{}", search_input_fingerprint="s" * 64, candidate_evaluation_fingerprint=service.candidate_evaluation_fingerprint(context), evaluation_contract_fingerprint=service.evaluation_contract_fingerprint())
    db_session.add(run); db_session.commit()
    row = DiscoveryRunJob(discovery_run_id=run.id, discovered_job_id=job.id, outcome="analysis_failed")
    db_session.add(row); db_session.commit()
    response = ranking.rank(type("Request", (), {"jobs": [service._listing(job)]})())
    service._persist_ranking(run, {job.id: row}, {service._listing_key(service._listing(job)): job.id}, response, "user-a", service.candidate_evaluation_fingerprint(context), service.evaluation_contract_fingerprint(), .5)
    db_session.commit()
    assert db_session.query(UserJobEvaluation).count() == 1
    assert row.evaluation_id == existing_id

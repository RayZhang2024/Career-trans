from datetime import datetime, timezone

from app.models.discovered_job import DiscoveredJob
from app.models.user_job_discovery import UserJobEvaluation
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


def _job() -> DiscoveredJob:
    now = datetime.now(timezone.utc)
    return DiscoveredJob(identity_key="provider:j1", source="greenhouse", source_token="board", external_id="j1", title="AI Engineer", company="Example", location="London", url="https://jobs.example/j1", description="Full verified job description with requirements.", detail_authority="verified_employer_detail", verification_status="verified", content_hash="b" * 64, state=DiscoveredJobState.UNCHANGED.value, first_seen_at=now, last_seen_at=now, last_changed_at=now)


def _request(job_id: str) -> DiscoveryRunCreateRequest:
    return DiscoveryRunCreateRequest(query=JobSearchQuery(keywords=["AI"], locations=["London"]), discovered_job_ids=[job_id], max_semantic_candidates=10, max_full_analyses=5)


def _user(user_id: str) -> User:
    return User(id=user_id, email=f"{user_id}@example.test", password_hash="safe-password-hash")


def test_unchanged_job_is_new_to_user_then_reused_without_ranking(db_session, monkeypatch) -> None:
    job = _job(); db_session.add_all([_user("user-a"), job]); db_session.commit()
    monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: _context())
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


def test_search_fingerprint_normalises_harmless_order() -> None:
    first = JobSearchQuery(keywords=[" AI ", "Engineer"], locations=["London", "UK"])
    second = JobSearchQuery(keywords=["engineer", "ai"], locations=["uk", " london "])
    assert UserJobDiscoveryService.search_input_fingerprint(first) == UserJobDiscoveryService.search_input_fingerprint(second)

"""Provider-free regressions for profile-first application CV tailoring."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.application_cv_tailoring import OpenAIApplicationCVTailoringAgent
from app.core.config import Settings
from app.models.application_cv_tailoring import ApplicationCVTailoringSession
from app.models.application_preparation import ApplicationPreparation
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateStructuredProfile
from app.models.cv_visibility_assessment import CVVisibilityAssessment
from app.models.discovered_job import DiscoveredJob
from app.models.user import User
from app.models.user_job_discovery import UserJobEvaluation
from app.providers.llm import SemanticOutputError
from app.schemas.ai_settings import SemanticOperation
from app.schemas.application_cv_tailoring import (
    TailoringAction, TailoringApproveWrite, TailoringDraftGenerateRequest,
    TailoringDraftReviewWrite, TailoringRecommendation, TailoringStartRequest,
    TailoringStrategy, TailoringStrategyReview, TailoringStrategyReviewWrite,
)
from app.schemas.application_preparation import (
    ApplicationIdentitySnapshot, ApplicationSourceRef, ApplicationTargetSnapshot,
    ApplicationTargetKind, CVWritingDraft, TailoredBullet, TailoredRoleDraft,
)
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.cv_ingestion import CandidateCVData, Employment, Skill
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.job import JobProfile, JobRequirement
from app.schemas.job_ranking import (
    JobArchetype, JobArchetypeAssessment, JobRelevanceAssessment,
    PostingLegitimacy, PostingLegitimacyAssessment, RankedJobOpportunity,
)
from app.schemas.matching import EvidenceRef, EvidenceSourceType, RequirementMatch
from app.schemas.recommendation import Recommendation, RecommendationAssessment
from app.services.application_cv_tailoring_service import (
    ApplicationCVTailoringError, ApplicationCVTailoringService, _Authority, _runtime_fingerprint,
)
from app.services.application_preparation_service import (
    ApplicationPreparationReadService, ApplicationPreparationService,
)
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.user_job_discovery_service import evaluation_contract_fingerprint_for_runtime


def _make_authority(db_session, runtime_snapshot_a, user_id: str, job_id: str) -> _Authority:
    job = DiscoveredJob(
        id=job_id, identity_key=f"provider:{job_id}", source="synthetic", title="AI systems engineer",
        location="London", url="https://jobs.example.test/role", description="Build reliable systems.",
        content_hash="a" * 64, state="unchanged", verification_status="verified",
        detail_authority="verified_employer_detail", first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc), last_changed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    evaluation = UserJobEvaluation(
        id="evaluation-current", user_id=user_id, discovered_job_id=job_id,
        job_content_hash=job.content_hash, candidate_evaluation_fingerprint="candidate-fingerprint",
        evaluation_contract_fingerprint="evaluation-contract", job_snapshot_json="{}", evaluation_json="{}",
    )
    db_session.add(evaluation)
    db_session.commit()
    skill_ref = ApplicationSourceRef(source_type="skills", source_ref="skill:0")
    requirement = JobRequirement(text="Build Python services", importance="essential", category="technical")
    match = RequirementMatch(
        requirement_index=0, requirement=requirement, match_type="demonstrated", score=0.9,
        evidence_ids=["confirmed-python"], evidence_refs=[EvidenceRef(source_type="skills", source_ref="skill:0", value="Python")], reasoning="Confirmed profile evidence.",
    )
    target = ApplicationTargetSnapshot(
        source_kind=ApplicationTargetKind.DISCOVERED_JOB, canonical_discovered_job_id=job_id,
        public_url=job.url, title=job.title, company="Synthetic Co", location="London",
        job_profile=JobProfile(title=job.title, requirements=[requirement]), requirement_matches=[match],
        job_content_hash=job.content_hash,
    )
    structured = CandidateCVData(
        skills=[Skill(name="Python")],
        employment=[Employment(employer="Example Co", title="Engineer", start_date="2020", end_date="2024", description="Built data services.")],
    )
    sources = {
        ("skills", "skill:0"): "Python",
        ("employment", "employment:0"): "Engineer at Example Co, 2020 to 2024. Built data services.",
    }
    return _Authority(
        job=job, evaluation=evaluation, opportunity=None, target=target,
        identity=ApplicationIdentitySnapshot(display_name="Synthetic Person", email="person@example.test"),
        context=CandidateContext(profile_text="Confirmed candidate profile", skills_text="Python"),
        structured=structured, sources=sources, candidate_fingerprint="full-profile-fingerprint",
        runtime_fingerprint=_runtime_fingerprint(runtime_snapshot_a),
    )


class _Agent:
    def __init__(self, strategy=None, draft=None, error=None):
        self.strategy = strategy
        self.draft = draft
        self.error = error
        self.strategy_calls = 0
        self.draft_calls = 0

    def recommend_strategy(self, _context):
        self.strategy_calls += 1
        if self.error:
            raise self.error
        return self.strategy

    def draft_tailored_cv(self, _context):
        self.draft_calls += 1
        if self.error:
            raise self.error
        return self.draft


class _RecordingClient:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.response)


def _strategy() -> TailoringStrategy:
    return TailoringStrategy(
        positioning="Evidence-led Python delivery.", summary_emphasis="Python services and delivery.",
        recommendations=[TailoringRecommendation(
            recommendation_id="requirement-0", requirement_index=0, action=TailoringAction.FEATURE,
            rationale="Confirmed Python skills and delivery experience support this requirement.",
            source_refs=[ApplicationSourceRef(source_type="skills", source_ref="skill:0"), ApplicationSourceRef(source_type="employment", source_ref="employment:0")],
        )],
        gaps_to_clarify=[], not_to_claim=["Do not claim unsupported cloud certifications."],
    )


def _draft() -> CVWritingDraft:
    skill = ApplicationSourceRef(source_type="skills", source_ref="skill:0")
    role = ApplicationSourceRef(source_type="employment", source_ref="employment:0")
    return CVWritingDraft(
        professional_summary="Engineer building Python services.", summary_source_refs=[skill], key_skills=["Python"],
        role_drafts=[TailoredRoleDraft(employment_index=0, bullets=[TailoredBullet(
            text="Built data services.", source_refs=[skill, role], priority=70,
        )])],
    )


def _service(db_session, runtime_snapshot_a, authority, agent):
    user_job_service = UserJobDiscoveryService(db_session, runtime_snapshot=runtime_snapshot_a)
    application_service = ApplicationPreparationService(
        db_session, user_discovery=user_job_service, graph_factory=lambda: None,
        drafting_agent_factory=lambda: None, settings=Settings(_env_file=None), runtime_snapshot=runtime_snapshot_a,
    )
    original_context = application_service._draft_context
    def draft_context(target, catalog, structured):
        return {
            "job": {"title": target.title, "requirements": [item.requirement.text for item in target.requirement_matches]},
            "sources": [{"source_type": kind, "source_ref": ref, "text": value} for (kind, ref), value in catalog.items()],
            "allowed_skills": [item.name for item in structured.skills],
            "employment": [{"employment_index": 0, "required_anchor_ref": {"source_type": "employment", "source_ref": "employment:0"}}],
            "projects": [],
        }
    application_service._draft_context = draft_context
    application_service._bounded_sources = lambda _context, _structured, _matches: authority.sources
    service = ApplicationCVTailoringService(
        db_session, runtime_snapshot=runtime_snapshot_a, application_service=application_service,
        agent_factory=lambda: agent, settings=Settings(_env_file=None),
    )
    service._authority = lambda _user_id, _job_id, runtime=None: authority
    return service


def test_generated_strategy_schema_is_strict_and_scoped_to_current_profile_and_requirements():
    schema = OpenAIApplicationCVTailoringAgent._strategy_schema({
        "sources": [{"source_type": "skills", "source_ref": "skill:0"}],
        "requirements": [{"requirement_index": 2}, {"requirement_index": 7}],
    })
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["TailoringRecommendation"]["properties"]["requirement_index"]["enum"] == [2, 7]
    assert schema["$defs"]["ApplicationSourceRef"]["anyOf"][0]["properties"]["source_ref"]["enum"] == ["skill:0"]

    client = _RecordingClient(json.dumps(_strategy().model_dump(mode="json")))
    agent = OpenAIApplicationCVTailoringAgent(strategy_client=client, draft_client=client, model="mock-model")
    result = agent.recommend_strategy({
        "sources": [{"source_type": "skills", "source_ref": "skill:0"}, {"source_type": "employment", "source_ref": "employment:0"}],
        "requirements": [{"requirement_index": 0}],
    })
    assert result.recommendations[0].recommendation_id == "requirement-0"
    assert len(client.calls) == 1
    assert client.calls[0]["text"]["format"]["strict"] is True

    draft_client = _RecordingClient(json.dumps(_draft().model_dump(mode="json")))
    draft_agent = OpenAIApplicationCVTailoringAgent(strategy_client=client, draft_client=draft_client, model="mock-model")
    draft_agent.draft_tailored_cv({
        "sources": [{"source_type": "skills", "source_ref": "skill:0"}, {"source_type": "employment", "source_ref": "employment:0"}],
        "allowed_skills": ["Python"], "employment": [{"employment_index": 0}], "projects": [],
    })
    draft_schema = draft_client.calls[0]["text"]["format"]["schema"]
    assert draft_schema["$defs"]["TailoredRoleDraft"]["properties"]["employment_index"]["enum"] == [0]
    assert draft_schema["properties"]["key_skills"]["items"]["enum"] == ["Python"]
    assert len(draft_client.calls) == 1


def test_sqlite_migration_is_additive_and_idempotent_for_existing_application_data():
    sql_path = Path(__file__).parents[1] / "migrations" / "20261012_application_cv_tailoring_sqlite.sql"
    connection = sqlite3.connect(":memory:")
    connection.executescript("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY); CREATE TABLE application_preparations (id VARCHAR(36) PRIMARY KEY);")
    connection.execute("INSERT INTO users (id) VALUES ('user-existing')")
    connection.execute("INSERT INTO application_preparations (id) VALUES ('preparation-existing')")
    sql = sql_path.read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.executescript(sql)
    assert connection.execute("SELECT id FROM users").fetchall() == [("user-existing",)]
    assert connection.execute("SELECT id FROM application_preparations").fetchall() == [("preparation-existing",)]
    columns = {row[1] for row in connection.execute("PRAGMA table_info(application_cv_tailoring_sessions)")}
    assert {"profile_outline_json", "strategy_history_json", "draft_history_json", "approved_preparation_id"} <= columns
    assert connection.execute("SELECT COUNT(*) FROM application_cv_tailoring_sessions").fetchone() == (0,)
    connection.close()


def test_profile_first_strategy_review_draft_revisions_and_approval_are_durable_and_user_scoped(db_session, runtime_snapshot_a):
    user = User(id="tailor-owner", email="owner@example.test", password_hash="safe")
    other = User(id="tailor-other", email="other@example.test", password_hash="safe")
    db_session.add_all([user, other]); db_session.commit()
    authority = _make_authority(db_session, runtime_snapshot_a, user.id, "tailor-job")
    agent = _Agent(strategy=_strategy(), draft=_draft())
    service = _service(db_session, runtime_snapshot_a, authority, agent)

    # No uploaded source CV or V2A row exists; canonical Profile evidence is sufficient.
    started = service.start(user.id, TailoringStartRequest(
        client_request_id="11111111-1111-4111-8111-111111111111", discovered_job_id=authority.job.id, target_pages=2,
    ))
    assert started.status == "strategy_review", started.failure_code
    assert agent.strategy_calls == 1
    assert service.get_by_request(user.id, started.client_request_id).id == started.id
    with pytest.raises(LookupError):
        service.get(other.id, started.id)

    reviewed = service.review_strategy(user.id, started.id, TailoringStrategyReviewWrite(
        expected_revision=1, accepted=True,
        choices=[TailoringStrategyReview(recommendation_id="requirement-0", decision="keep", order=0)],
    ))
    assert reviewed.status == "strategy_accepted"
    draft = service.generate_draft(user.id, started.id, TailoringDraftGenerateRequest(
        client_request_id="22222222-2222-4222-8222-222222222222", expected_strategy_revision=2,
    ))
    assert draft.status == "draft_review"
    assert agent.draft_calls == 1

    edited_draft = draft.draft.model_copy(update={"professional_summary": "Engineer delivering Python services."})
    saved = service.review_draft(user.id, started.id, TailoringDraftReviewWrite(expected_revision=1, draft=edited_draft))
    assert saved.draft_revision == 2
    assert len(json.loads(db_session.get(ApplicationCVTailoringSession, started.id).draft_history_json)) == 2

    approved = service.approve(user.id, started.id, TailoringApproveWrite(expected_strategy_revision=2, expected_draft_revision=2))
    assert approved.status == "approved"
    assert approved.approved_preparation_id
    assert db_session.query(ApplicationPreparation).filter_by(user_id=user.id).count() == 1
    final = ApplicationPreparationReadService(db_session).get(user.id, approved.approved_preparation_id)
    assert final.result.cv.professional_summary == "Engineer delivering Python services."
    assert final.target.title == authority.target.title
    assert db_session.query(ApplicationCVTailoringSession).filter_by(user_id=other.id).count() == 0


def test_real_canonical_profile_and_current_evaluation_are_sufficient_without_cv_upload_or_v2a(db_session, runtime_snapshot_a):
    user = User(id="profile-first-owner", email="profile-first@example.test", password_hash="safe")
    db_session.add(user)
    db_session.add(CandidateProfile(
        user_id=user.id, display_name="Synthetic Candidate", preferred_email=user.email,
        headline="Python engineer", summary="Builds reliable services.",
    ))
    structured = CandidateCVData(
        skills=[Skill(name="Python")],
        employment=[Employment(employer="Example Co", title="Engineer", start_date="2021", end_date="2025", description="Built Python data services.")],
    )
    db_session.add(CandidateStructuredProfile(user_id=user.id, structured_json=structured.model_dump_json()))
    job = DiscoveredJob(
        id="real-profile-job", identity_key="provider:real-profile-job", source="synthetic",
        title="Applied AI Engineer", company="Synthetic Co", location="London",
        url="https://jobs.example.test/applied-ai", description="Build reliable Python data services.",
        content_hash="b" * 64, state="unchanged", verification_status="verified",
        detail_authority="verified_employer_detail", first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc), last_changed_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()

    ActiveCandidateEvidenceResolver(db_session).resolve(user.id, structured)
    db_session.commit()
    reader = CanonicalCandidateReadService(db_session)
    snapshot = reader.read(user.id)
    context = reader.candidate_context(snapshot, require_structured_profile=True, require_complete_evidence=True)
    assert context is not None and context.evidence
    requirement = JobRequirement(text="Build Python data services", importance="essential", category="technical")
    match = RequirementMatch(
        requirement_index=0, requirement=requirement, match_type="demonstrated", score=0.9,
        evidence_ids=[context.evidence[0].evidence_id],
        evidence_refs=[EvidenceRef(source_type=EvidenceSourceType.SKILLS, source_ref="skills:0", value="Python")],
        reasoning="Current confirmed Profile evidence.",
    )
    opportunity = RankedJobOpportunity(
        job=UserJobDiscoveryService._listing(job),
        relevance=JobRelevanceAssessment(relevant=True, score=0.9, reasoning="Synthetic relevance."),
        archetype=JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="Synthetic archetype."),
        fit_assessment=FitAssessment(fit_score=78),
        career_assessment=CareerAssessment(career_alignment_score=80, confidence=AlignmentConfidence.HIGH, dimensions=[], reasoning="Synthetic alignment."),
        recommendation_assessment=RecommendationAssessment(
            recommendation=Recommendation.CONSIDER, fit_score=78, career_alignment_score=80,
            career_alignment_confidence=AlignmentConfidence.HIGH, rule_id="synthetic",
            reasoning="Synthetic recommendation.",
        ),
        legitimacy=PostingLegitimacyAssessment(legitimacy=PostingLegitimacy.HIGH_CONFIDENCE, reasoning="Synthetic listing."),
        rank=1, job_profile=JobProfile(title=job.title, company=job.company, requirements=[requirement]),
        requirement_matches=[match],
    )
    db_session.add(UserJobEvaluation(
        id="profile-first-evaluation", user_id=user.id, discovered_job_id=job.id,
        job_content_hash=job.content_hash,
        candidate_evaluation_fingerprint=UserJobDiscoveryService.candidate_evaluation_fingerprint(context),
        evaluation_contract_fingerprint=evaluation_contract_fingerprint_for_runtime(runtime_snapshot_a),
        job_snapshot_json=UserJobDiscoveryService._listing(job).model_dump_json(),
        evaluation_json=opportunity.model_dump_json(),
    ))
    db_session.commit()
    actual_strategy = _strategy()
    actual_recommendation = actual_strategy.recommendations[0].model_copy(update={
        "source_refs": [
            ApplicationSourceRef(source_type="skills", source_ref="skills:0"),
            ApplicationSourceRef(source_type="employment", source_ref="employment:0"),
        ],
    })
    actual_strategy = actual_strategy.model_copy(update={"recommendations": [actual_recommendation]})
    agent = _Agent(strategy=actual_strategy, draft=_draft())
    application_service = ApplicationPreparationService(
        db_session, user_discovery=UserJobDiscoveryService(db_session, runtime_snapshot=runtime_snapshot_a),
        graph_factory=lambda: None, drafting_agent_factory=lambda: None,
        settings=Settings(_env_file=None), runtime_snapshot=runtime_snapshot_a,
    )
    service = ApplicationCVTailoringService(
        db_session, runtime_snapshot=runtime_snapshot_a, application_service=application_service,
        agent_factory=lambda: agent, settings=Settings(_env_file=None),
    )

    preflight = service.preflight(user.id, job.id)
    assert preflight.state == "ready"
    assert preflight.max_requirements == 40 and preflight.max_input_characters == 36_000
    assert agent.strategy_calls == agent.draft_calls == 0
    actual_authority = service._authority(user.id, job.id)
    assert {("skills", "skills:0"), ("employment", "employment:0")} <= set(actual_authority.sources), actual_authority.sources
    service._validate_strategy(actual_strategy, actual_authority)
    started = service.start(user.id, TailoringStartRequest(
        client_request_id="66666666-6666-4666-8666-666666666666", discovered_job_id=job.id,
    ))
    assert started.status == "strategy_review", started.failure_code
    assert started.profile_outline.employment[0].title == "Engineer"
    assert agent.strategy_calls == 1
    assert db_session.query(CandidateStructuredProfile).filter_by(user_id=user.id).count() == 1
    assert db_session.query(CandidateCVIngestionDraft).filter_by(user_id=user.id).count() == 0
    assert db_session.query(CVVisibilityAssessment).filter_by(user_id=user.id).count() == 0
    assert db_session.query(ApplicationPreparation).filter_by(user_id=user.id).count() == 0


def test_repeated_start_is_idempotent_and_preserves_uncertain_provider_outcome(db_session, runtime_snapshot_a):
    user = User(id="tailor-uncertain", email="uncertain@example.test", password_hash="safe")
    db_session.add(user); db_session.commit()
    authority = _make_authority(db_session, runtime_snapshot_a, user.id, "uncertain-job")
    agent = _Agent(error=RuntimeError("transport interrupted"))
    service = _service(db_session, runtime_snapshot_a, authority, agent)
    request = TailoringStartRequest(
        client_request_id="33333333-3333-4333-8333-333333333333", discovered_job_id=authority.job.id,
    )
    first = service.start(user.id, request)
    second = service.start(user.id, request)
    assert first.status == "strategy_uncertain"
    assert second.id == first.id
    assert agent.strategy_calls == 1
    assert db_session.query(ApplicationCVTailoringSession).filter_by(user_id=user.id).count() == 1


def test_invalid_strategy_output_is_not_presented_as_completed(db_session, runtime_snapshot_a):
    user = User(id="tailor-invalid", email="invalid@example.test", password_hash="safe")
    db_session.add(user); db_session.commit()
    authority = _make_authority(db_session, runtime_snapshot_a, user.id, "invalid-job")
    agent = _Agent(error=SemanticOutputError("invalid strategy"))
    service = _service(db_session, runtime_snapshot_a, authority, agent)
    result = service.start(user.id, TailoringStartRequest(
        client_request_id="44444444-4444-4444-8444-444444444444", discovered_job_id=authority.job.id,
    ))
    assert result.status == "strategy_invalid"
    assert result.strategy is None
    assert result.strategy_status == "result_invalid"


def test_changed_profile_authority_stales_a_review_before_any_draft_call(db_session, runtime_snapshot_a):
    user = User(id="tailor-stale", email="stale@example.test", password_hash="safe")
    db_session.add(user); db_session.commit()
    authority = _make_authority(db_session, runtime_snapshot_a, user.id, "stale-job")
    agent = _Agent(strategy=_strategy(), draft=_draft())
    service = _service(db_session, runtime_snapshot_a, authority, agent)
    authority_holder = {"current": authority}
    service._authority = lambda _user_id, _job_id, runtime=None: authority_holder["current"]
    started = service.start(user.id, TailoringStartRequest(
        client_request_id="77777777-7777-4777-8777-777777777777", discovered_job_id=authority.job.id,
    ))
    authority_holder["current"] = replace(authority, candidate_fingerprint="changed-profile-fingerprint")
    with pytest.raises(ApplicationCVTailoringError, match="Profile, job evaluation, or AI settings changed"):
        service.review_strategy(user.id, started.id, TailoringStrategyReviewWrite(expected_revision=1, accepted=True))
    current = service.get(user.id, started.id)
    assert current.status == "stale"
    assert current.is_current is False
    assert agent.draft_calls == 0


def test_unsupported_user_draft_edit_returns_validation_error_without_advancing_revision(db_session, runtime_snapshot_a):
    user = User(id="tailor-edit-validation", email="edit-validation@example.test", password_hash="safe")
    db_session.add(user); db_session.commit()
    authority = _make_authority(db_session, runtime_snapshot_a, user.id, "edit-validation-job")
    agent = _Agent(strategy=_strategy(), draft=_draft())
    service = _service(db_session, runtime_snapshot_a, authority, agent)
    started = service.start(user.id, TailoringStartRequest(
        client_request_id="90909090-9090-4090-8090-909090909090", discovered_job_id=authority.job.id,
    ))
    accepted = service.review_strategy(user.id, started.id, TailoringStrategyReviewWrite(expected_revision=1, accepted=True))
    draft = service.generate_draft(user.id, started.id, TailoringDraftGenerateRequest(
        client_request_id="91919191-9191-4191-8191-919191919191", expected_strategy_revision=accepted.strategy_revision,
    ))
    invalid = draft.draft.model_copy(update={"key_skills": ["PyTorch"]})

    with pytest.raises(ApplicationCVTailoringError, match="skill outside confirmed candidate skills") as error:
        service.review_draft(user.id, started.id, TailoringDraftReviewWrite(expected_revision=1, draft=invalid))

    assert error.value.status_code == 422
    unchanged = service.get(user.id, started.id)
    assert unchanged.draft_revision == 1
    assert unchanged.draft.professional_summary == draft.draft.professional_summary

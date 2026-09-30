from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.one_off_discovery_execution import OneOffDiscoveryExecution
from app.models.discovery_schedule import DiscoverySchedule
from app.models.discovered_job import DiscoveredJob
from app.models.user import User
from app.models.user_job_discovery import DiscoveryRun
from app.schemas.agentic_discovery import AgenticDiscoveryDiagnostics, AgenticDiscoveryResponse
from app.schemas.candidate import CandidateContext
from app.schemas.discovery import JobSearchQuery
from app.schemas.discovery import JobListing
from app.schemas.job_discovery_settings import EffectiveJobDiscoveryProvider
from app.schemas.one_off_discovery import OneOffLaunchRequest
from app.services.job_discovery_settings_service import ResolvedWebSearchProvider
from app.services.agentic_web_execution_core import AgenticWebExecutionCore
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.one_off_discovery_service import (
    OneOffDiscoveryService, OneOffLaunchConflict, OneOffLaunchUnavailable,
)
from app.services.user_job_discovery_service import DiscoveryRunExecutionFailure
from app.services.search_history_service import SearchHistoryReadService


class _CandidateReader:
    def read(self, user_id):
        return object()

    def candidate_context(self, snapshot, **kwargs):
        return CandidateContext()


class _Settings:
    def __init__(self):
        self.revision = 2

    def read(self, _user_id):
        return SimpleNamespace(revision=self.revision, effective_provider=EffectiveJobDiscoveryProvider.TAVILY)

    def resolve_provider(self, _user_id, *, scheduled_due_runner):
        assert scheduled_due_runner is False
        return ResolvedWebSearchProvider(object(), {"provider": "tavily", "credential_source": "user", "search_depth": "basic"})


def _service(db_session, *, calls, candidate_reader=None, discover=None, user_runs_factory=None, agentic_core=None):
    settings = _Settings()

    def agentic_factory(user_id, runtime, provider):
        assert db_session.query(OneOffDiscoveryExecution).filter_by(user_id=user_id).count() == 1
        calls.append("provider")
        return SimpleNamespace(discover=discover or (lambda request: AgenticDiscoveryResponse(diagnostics=AgenticDiscoveryDiagnostics())))

    service = OneOffDiscoveryService(
        db_session, settings_service=settings,
        runtime_snapshot_resolver=lambda _user_id: object(),
        agentic_factory=agentic_factory,
        user_runs_factory=user_runs_factory or (lambda _runtime: None),
        candidate_reader=candidate_reader or _CandidateReader(),
        agentic_core=agentic_core,
    )
    return service, settings


def test_one_off_launch_is_durable_before_provider_and_clean_zero_is_completed(db_session):
    user = User(email="one-off@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    calls = []
    service, _ = _service(db_session, calls=calls)
    preflight = service.preflight(user.id)
    request = OneOffLaunchRequest(
        client_request_id=uuid4(), expected_launch_fingerprint=preflight.launch_fingerprint,
        query=JobSearchQuery(keywords=["AI engineer"]),
    )

    result = service.launch(user.id, request)

    assert calls == ["provider"]
    assert result.status.value == "completed"
    assert result.acquisition_summary["canonical_jobs"] == 0
    assert result.discovery_run_id is None
    assert db_session.query(OneOffDiscoveryExecution).count() == 1
    assert db_session.query(DiscoverySchedule).count() == 0


def test_one_off_idempotency_reconciles_and_rejects_changed_search_intent(db_session):
    user = User(email="one-off-idempotency@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    calls = []
    service, settings = _service(db_session, calls=calls)
    preflight = service.preflight(user.id)
    request_id = uuid4()
    request = OneOffLaunchRequest(client_request_id=request_id, expected_launch_fingerprint=preflight.launch_fingerprint, query=JobSearchQuery(keywords=["AI engineer"]))
    first = service.launch(user.id, request)

    settings.revision += 1
    assert service.launch(user.id, request).id == first.id
    assert calls == ["provider"]
    with pytest.raises(OneOffLaunchConflict):
        service.launch(user.id, OneOffLaunchRequest(client_request_id=request_id, expected_launch_fingerprint="0" * 64, query=JobSearchQuery(keywords=["Data engineer"])))
    assert db_session.query(OneOffDiscoveryExecution).count() == 1


def test_one_off_stale_preflight_does_not_claim(db_session):
    user = User(email="one-off-stale@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    calls = []
    service, _ = _service(db_session, calls=calls)
    preflight = service.preflight(user.id)
    with pytest.raises(OneOffLaunchConflict):
        service.launch(user.id, OneOffLaunchRequest(
            client_request_id=uuid4(), expected_launch_fingerprint="f" * 64,
            query=JobSearchQuery(keywords=["AI engineer"]),
        ))
    assert calls == []
    assert db_session.query(OneOffDiscoveryExecution).count() == 0


def test_one_off_rejects_noncanonical_editable_fields_before_claim(db_session):
    user = User(email="one-off-noncanonical@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    calls = []
    service, _ = _service(db_session, calls=calls)
    preflight = service.preflight(user.id)
    with pytest.raises(OneOffLaunchUnavailable, match="SearchIntent must be committed"):
        service.launch(user.id, OneOffLaunchRequest(
            client_request_id=uuid4(), expected_launch_fingerprint=preflight.launch_fingerprint,
            query=JobSearchQuery(keywords=[" AI "]),
        ))
    assert calls == []
    assert db_session.query(OneOffDiscoveryExecution).count() == 0


def test_one_off_candidate_readiness_is_authoritative_before_claim(db_session):
    user = User(email="one-off-candidate-not-ready@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    reader = _CandidateReader()
    reader.candidate_context = lambda _snapshot, **_kwargs: None
    calls = []
    service, _ = _service(db_session, calls=calls, candidate_reader=reader)
    preflight = service.preflight(user.id)
    assert preflight.available is False
    with pytest.raises(OneOffLaunchUnavailable, match="candidate profile"):
        service.launch(user.id, OneOffLaunchRequest(
            client_request_id=uuid4(), expected_launch_fingerprint=preflight.launch_fingerprint,
            query=JobSearchQuery(keywords=["AI"]),
        ))
    assert calls == []
    assert db_session.query(OneOffDiscoveryExecution).count() == 0


def test_one_off_provider_failure_without_results_is_terminal_failed(db_session):
    user = User(email="one-off-provider-failure@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    calls = []
    service, _ = _service(db_session, calls=calls, discover=lambda _request: AgenticDiscoveryResponse(
        diagnostics=AgenticDiscoveryDiagnostics(search_errors={"provider": "unavailable"}),
    ))
    preflight = service.preflight(user.id)
    result = service.launch(user.id, OneOffLaunchRequest(
        client_request_id=uuid4(), expected_launch_fingerprint=preflight.launch_fingerprint,
        query=JobSearchQuery(keywords=["AI"]),
    ))
    assert result.status.value == "failed"
    assert result.failure_summary == {"agentic_web": 1}


def test_one_off_partial_web_failure_preserves_successful_canonical_evaluation(db_session):
    user = User(email="one-off-partial@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    now = datetime.now(timezone.utc)
    listing = JobListing(source="tavily", source_token="query-1", external_id="partial", title="AI role", url="https://jobs.example.test/partial")
    db_session.add(DiscoveredJob(
        id="partial-job", identity_key=SqlAlchemyDiscoveredJobStateStore.identity_key(listing),
        source=listing.source, source_token=listing.source_token, external_id=listing.external_id,
        title=listing.title, url=listing.url, content_hash="p" * 64, state="new",
        last_seen_at=now, last_changed_at=now,
    ))
    db_session.commit()

    class Runs:
        def start(self, _user_id, _request):
            return SimpleNamespace(id="partial-run", status=SimpleNamespace(value="completed"), funnel={})

    service, _ = _service(
        db_session, calls=[],
        discover=lambda _request: AgenticDiscoveryResponse(
            listings=[listing], diagnostics=AgenticDiscoveryDiagnostics(extraction_failures=1),
        ),
        user_runs_factory=lambda _runtime: Runs(),
    )
    preflight = service.preflight(user.id)
    result = service.launch(user.id, OneOffLaunchRequest(
        client_request_id=uuid4(), expected_launch_fingerprint=preflight.launch_fingerprint,
        query=JobSearchQuery(keywords=["AI"]),
    ))
    assert result.status.value == "partial_failed"
    assert result.discovery_run_id == "partial-run"
    assert result.acquisition_summary["canonical_jobs"] == 1


def test_one_off_local_codex_uses_manual_authority_and_stops_on_live_search_failure(db_session):
    user = User(email="one-off-local-codex@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    calls = []

    class LocalCodexSettings(_Settings):
        def read(self, _user_id):
            return SimpleNamespace(revision=self.revision, effective_provider=EffectiveJobDiscoveryProvider.LOCAL_CODEX)

        def resolve_provider(self, _user_id, *, scheduled_due_runner):
            calls.append(("provider_authority", scheduled_due_runner))
            assert scheduled_due_runner is False
            return ResolvedWebSearchProvider(object(), {"provider": "local_codex", "credential_source": "none"})

    class NeverEvaluate:
        def start(self, *_args):
            raise AssertionError("Failed live Local Codex search must stop before evaluation.")

    service, _ = _service(
        db_session, calls=[],
        discover=lambda _request: AgenticDiscoveryResponse(
            diagnostics=AgenticDiscoveryDiagnostics(local_codex_search_failed=True, search_errors={"local_codex": "timeout"}),
        ),
        user_runs_factory=lambda _runtime: NeverEvaluate(),
    )
    service._settings = LocalCodexSettings()
    preflight = service.preflight(user.id)
    result = service.launch(user.id, OneOffLaunchRequest(
        client_request_id=uuid4(), expected_launch_fingerprint=preflight.launch_fingerprint,
        query=JobSearchQuery(keywords=["AI"]),
    ))
    assert result.status.value == "failed"
    assert result.provider["provider"] == "local_codex"
    assert all(authority is False for name, authority in calls if name == "provider_authority")


def test_one_off_uses_canonical_identity_when_same_url_has_multiple_jobs(db_session):
    user = User(email="one-off-evaluation@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    calls = []
    job_url = "https://jobs.example.test/role"
    now = datetime.now(timezone.utc)
    accepted = JobListing(source="tavily", source_token="query-1", external_id="accepted", title="AI role", url=job_url)
    unrelated = JobListing(source="greenhouse", source_token="board", external_id="other", title="Other role", url=job_url)
    accepted_id = "accepted-job"
    unrelated_id = "unrelated-job"

    def discover(_request):
        for job_id, listing in ((accepted_id, accepted), (unrelated_id, unrelated)):
            db_session.add(DiscoveredJob(
                id=job_id,
                identity_key=SqlAlchemyDiscoveredJobStateStore.identity_key(listing),
                source=listing.source, source_token=listing.source_token, external_id=listing.external_id,
                title=listing.title, url=job_url, content_hash=("a" if job_id == accepted_id else "b") * 64,
                state="new", last_seen_at=now, last_changed_at=now,
            ))
        db_session.commit()
        return AgenticDiscoveryResponse(
            listings=[accepted],
            diagnostics=AgenticDiscoveryDiagnostics(),
        )

    runtime = object()
    seen = []
    shared_calls = []
    core = AgenticWebExecutionCore(db_session)
    original_acquire = core.acquire
    original_evaluate = core.evaluate
    core.acquire = lambda **kwargs: (shared_calls.append("acquire"), original_acquire(**kwargs))[1]
    core.evaluate = lambda **kwargs: (shared_calls.append("evaluate"), original_evaluate(**kwargs))[1]

    class UserRuns:
        def start(self, user_id, request):
            assert user_id == user.id
            assert request.discovered_job_ids == [accepted_id]
            assert request.max_semantic_candidates == 10
            assert request.max_full_analyses == 5
            return SimpleNamespace(id="linked-run", status=SimpleNamespace(value="completed"), funnel={"relevance_screened": 1, "analysed": 1, "reused": 0})

    service, _ = _service(
        db_session, calls=calls, discover=discover,
        user_runs_factory=lambda snapshot: (seen.append(snapshot), UserRuns())[1],
        agentic_core=core,
    )
    service._runtime_snapshot_resolver = lambda _user_id: runtime
    preflight = service.preflight(user.id)
    result = service.launch(user.id, OneOffLaunchRequest(
        client_request_id=uuid4(), expected_launch_fingerprint=preflight.launch_fingerprint,
        query=JobSearchQuery(keywords=["AI"]),
    ))
    assert result.status.value == "completed"
    assert result.discovery_run_id == "linked-run"
    assert result.acquisition_summary["canonical_jobs"] == 1
    assert result.acquisition_summary["analysed"] == 1
    assert seen == [runtime]
    assert shared_calls == ["acquire", "evaluate"]


def test_evaluation_failure_links_terminal_discovery_run_into_one_logical_history_item(db_session):
    user = User(email="one-off-failed-evaluation@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    calls = []
    now = datetime.now(timezone.utc)
    listing = JobListing(source="tavily", source_token="query-1", external_id="accepted", title="AI role", url="https://jobs.example.test/failed-role")

    def discover(_request):
        calls.append("acquire")
        db_session.add(DiscoveredJob(
            id="failed-evaluation-job", identity_key=SqlAlchemyDiscoveredJobStateStore.identity_key(listing),
            source=listing.source, source_token=listing.source_token, external_id=listing.external_id,
            title=listing.title, url=listing.url, content_hash="f" * 64, state="new",
            last_seen_at=now, last_changed_at=now,
        ))
        db_session.commit()
        return AgenticDiscoveryResponse(listings=[listing], diagnostics=AgenticDiscoveryDiagnostics())

    class FailedEvaluation:
        def start(self, user_id, _request):
            calls.append("evaluate")
            run = DiscoveryRun(
                id="persisted-failed-run", user_id=user_id, search_input_json="{}",
                search_input_fingerprint="s" * 64, candidate_evaluation_fingerprint="c" * 64,
                evaluation_contract_fingerprint="e" * 64, status="failed", funnel_json="{}",
                failure_summary_json='{"ranking":1}', started_at=now, completed_at=now,
            )
            db_session.add(run)
            db_session.commit()
            raise DiscoveryRunExecutionFailure(run.id)

    service, _ = _service(
        db_session, calls=[], discover=discover,
        user_runs_factory=lambda _runtime: FailedEvaluation(),
    )
    preflight = service.preflight(user.id)
    result = service.launch(user.id, OneOffLaunchRequest(
        client_request_id=uuid4(), expected_launch_fingerprint=preflight.launch_fingerprint,
        query=JobSearchQuery(keywords=["AI"]),
    ))

    assert calls == ["acquire", "evaluate"]
    assert result.status.value == "failed"
    assert result.discovery_run_id == "persisted-failed-run"
    assert db_session.query(DiscoveryRun).filter_by(user_id=user.id).count() == 1
    assert db_session.query(OneOffDiscoveryExecution).filter_by(user_id=user.id).count() == 1
    projection = SearchHistoryReadService(db_session).list(user.id)
    assert len(projection.items) == 1
    assert projection.items[0].type == "one_off"
    assert projection.items[0].execution.discovery_run_id == "persisted-failed-run"


def test_one_off_reconciliation_recovers_stale_without_rerunning_provider(db_session):
    user = User(email="one-off-stale-running@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    row = OneOffDiscoveryExecution(
        user_id=user.id, client_request_id=str(uuid4()), request_fingerprint="a" * 64,
        query_snapshot_json='{"keywords":["AI"]}', policy_snapshot_json='{}',
        status="running", started_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    db_session.add(row)
    db_session.commit()
    calls = []
    service, _ = _service(db_session, calls=calls)

    result = service.reconcile(user.id, row.id)

    assert result.status.value == "failed"
    assert result.failure_summary == {"stale_execution": 1}
    assert calls == []


def test_one_off_preflight_launch_and_reconciliation_routes_require_authentication(client):
    assert client.get("/api/v1/jobs/one-off-discovery/preflight").status_code == 401
    assert client.get("/api/v1/jobs/one-off-discovery/executions").status_code == 401
    assert client.get("/api/v1/jobs/one-off-discovery/executions/private-id").status_code == 401
    assert client.post("/api/v1/jobs/one-off-discovery/executions", json={}).status_code == 401

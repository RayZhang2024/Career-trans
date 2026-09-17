from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.discovery_schedule import ScheduledDiscoveryExecution
from app.models.user import User
from app.schemas.candidate import CandidateContext
from app.schemas.discovery import JobListing, JobSearchQuery
from app.schemas.discovery_schedule import AcquisitionConfig, AgenticWebScheduleConfig, DiscoveryScheduleCreate, DiscoverySchedulePatch, ScheduleCadence, ScheduleSpec, StructuredAtsScheduleConfig, TriggerKind
from app.schemas.structured_ats_discovery import StructuredAtsDiscoveryResponse, StructuredAtsSourceDiagnostic
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts
from app.schemas.agentic_discovery import AgenticDiscoveryDiagnostics, AgenticDiscoveryResponse
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.discovery_schedule_service import DiscoveryScheduleService, most_recent_due, next_occurrence
from app.services.scheduled_discovery_execution_service import ScheduledDiscoveryExecutionService


UTC = timezone.utc


def _payload(**overrides):
    values = {
        "name": "London roles",
        "schedule": ScheduleSpec(cadence=ScheduleCadence.DAILY, timezone="Europe/London", local_time=time(9, 30)),
        "query": JobSearchQuery(keywords=["AI"], locations=["London"]),
        "acquisition": AcquisitionConfig(structured_ats=StructuredAtsScheduleConfig(enabled=True, providers=["greenhouse"])),
    }
    values.update(overrides)
    return DiscoveryScheduleCreate(**values)


def _user(session, email="schedule@example.com"):
    user = User(email=email, password_hash="test")
    session.add(user)
    session.commit()
    return user


def _listing() -> JobListing:
    return JobListing(source="greenhouse", source_token="acme", external_id="role-1", title="AI Engineer", company="Acme", location="London", url="https://jobs.example.test/role-1", description="Role detail")


def _ats_response(listings=(), diagnostics=()):
    return StructuredAtsDiscoveryResponse(listings=list(listings), source_diagnostics=list(diagnostics), raw_count=len(listings), deduplicated_count=0, rejected_count=0, lifecycle_counts=DiscoveryLifecycleCounts())


def _agentic_response(listings=(), *, errors=False):
    return AgenticDiscoveryResponse(listings=list(listings), diagnostics=AgenticDiscoveryDiagnostics(search_errors={"safe": "failure"} if errors else {}))


def _claimed_runner(db_session, monkeypatch, *, payload, ats, agentic, user_runs):
    user = _user(db_session, f"{id(ats)}@example.com")
    schedule = DiscoveryScheduleService(db_session).create(user.id, payload, datetime(2026, 9, 14, 8, tzinfo=UTC))
    runner = ScheduledDiscoveryExecutionService(db_session, structured_ats=ats, agentic_web_factory=lambda: agentic, user_runs=user_runs)
    monkeypatch.setattr("app.services.scheduled_discovery_execution_service.PersistedCandidateContextLoader.load_confirmed", lambda *_: CandidateContext())
    claimed = runner.claim(schedule.id, TriggerKind.MANUAL, datetime(2026, 9, 14, 8, tzinfo=UTC))
    assert claimed is not None
    return runner, claimed


def test_dst_daily_wall_time_and_nonexistent_and_ambiguous_slots():
    daily = ScheduleSpec(cadence=ScheduleCadence.DAILY, timezone="Europe/London", local_time=time(9, 30))
    before_spring = datetime(2026, 3, 28, 10, 0, tzinfo=UTC)
    assert next_occurrence(daily, before_spring).astimezone(__import__("zoneinfo").ZoneInfo("Europe/London")).time() == time(9, 30)

    missing = ScheduleSpec(cadence=ScheduleCadence.DAILY, timezone="Europe/London", local_time=time(1, 30))
    # 01:30 on 2026-03-29 does not exist in London; it is skipped.
    assert next_occurrence(missing, datetime(2026, 3, 28, 2, 0, tzinfo=UTC)).date().isoformat() == "2026-03-30"

    ambiguous = ScheduleSpec(cadence=ScheduleCadence.DAILY, timezone="Europe/London", local_time=time(1, 30))
    # fold=0 is 00:30 UTC on fall-back day, the first local occurrence.
    assert most_recent_due(ambiguous, datetime(2026, 10, 25, 1, 0, tzinfo=UTC)) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_weekly_and_invalid_timezone_fail_closed():
    weekly = ScheduleSpec(cadence=ScheduleCadence.WEEKLY, timezone="Europe/London", local_time=time(9), weekdays=[0, 2])
    assert next_occurrence(weekly, datetime(2026, 9, 14, 9, 0, tzinfo=UTC)).weekday() == 2
    with pytest.raises(ValueError, match="IANA"):
        ScheduleSpec(cadence=ScheduleCadence.DAILY, timezone="No/Such_Zone", local_time=time(9))


def test_edit_disable_reenable_and_user_isolation(db_session):
    owner, other = _user(db_session), _user(db_session, "other@example.com")
    service = DiscoveryScheduleService(db_session)
    now = datetime(2026, 9, 14, 8, tzinfo=UTC)
    created = service.create(owner.id, _payload(), now)
    planned = created.next_run_at
    edited = service.patch(owner.id, created.id, DiscoverySchedulePatch(name="Renamed"), now)
    assert edited.next_run_at == planned
    changed = service.patch(owner.id, created.id, DiscoverySchedulePatch(schedule=ScheduleSpec(cadence=ScheduleCadence.DAILY, timezone="Europe/London", local_time=time(10))), now)
    assert changed.next_run_at != planned
    disabled = service.patch(owner.id, created.id, DiscoverySchedulePatch(enabled=False), now)
    assert disabled.next_run_at is None
    enabled = service.patch(owner.id, created.id, DiscoverySchedulePatch(enabled=True), now)
    assert enabled.next_run_at and enabled.next_run_at > now
    with pytest.raises(LookupError):
        service.get(other.id, created.id)


def test_claim_is_durable_unique_and_manual_does_not_move_next_run(db_session, monkeypatch):
    user = _user(db_session)
    schedules = DiscoveryScheduleService(db_session)
    now = datetime(2026, 9, 14, 10, tzinfo=UTC)
    created = schedules.create(user.id, _payload(), datetime(2026, 9, 14, 7, tzinfo=UTC))
    record = schedules.get(user.id, created.id)
    record.next_run_at = datetime(2026, 9, 14, 9, 30, tzinfo=UTC)
    db_session.commit()

    class NeverAcquire:
        def discover(self, request):
            raise AssertionError("acquisition must not happen during claim")

    runner = ScheduledDiscoveryExecutionService(db_session, structured_ats=NeverAcquire(), agentic_web_factory=lambda: NeverAcquire(), user_runs=NeverAcquire())
    first = runner.claim(record.id, TriggerKind.SCHEDULED, now)
    assert first is not None
    after_claim = schedules.read(schedules.get(user.id, record.id)).next_run_at
    assert after_claim and after_claim > now
    assert runner.claim(record.id, TriggerKind.SCHEDULED, now) is None
    assert len(db_session.scalars(select(ScheduledDiscoveryExecution)).all()) == 1
    # A running scheduled execution blocks a manual execution too.
    assert runner.claim(record.id, TriggerKind.MANUAL, now) is None


def test_manual_disabled_schedule_claim_keeps_next_run_unchanged(db_session):
    user = _user(db_session)
    schedules = DiscoveryScheduleService(db_session)
    now = datetime(2026, 9, 14, 8, tzinfo=UTC)
    created = schedules.create(user.id, _payload(enabled=False), now)
    runner = ScheduledDiscoveryExecutionService(db_session, structured_ats=object(), agentic_web_factory=object, user_runs=object())
    claimed = runner.claim(created.id, TriggerKind.MANUAL, now)
    assert claimed is not None
    assert schedules.get(user.id, created.id).next_run_at is None


def test_candidate_not_ready_skips_before_acquisition(db_session):
    user = _user(db_session)
    schedules = DiscoveryScheduleService(db_session)
    created = schedules.create(user.id, _payload(enabled=False), datetime(2026, 9, 14, 8, tzinfo=UTC))

    class NeverAcquire:
        def discover(self, request):
            raise AssertionError("candidate-not-ready must not acquire")

    runner = ScheduledDiscoveryExecutionService(db_session, structured_ats=NeverAcquire(), agentic_web_factory=lambda: NeverAcquire(), user_runs=object())
    execution = runner.run_now(user.id, created.id, datetime(2026, 9, 14, 8, tzinfo=UTC))
    assert execution.status == "skipped"
    assert execution.failure_summary_json == '{"candidate_not_ready": 1}'


def test_snapshot_query_constraints_reach_existing_structured_ats(db_session, monkeypatch):
    received = []

    class Ats:
        def discover(self, request):
            received.append(request)
            return _ats_response()

    payload = _payload(query=JobSearchQuery(keywords=["AI"], locations=["London"], remote_ok=False, excluded_companies=["Avoid"], excluded_title_terms=["Sales"], employment_types=["FullTime"]))
    runner, claimed = _claimed_runner(db_session, monkeypatch, payload=payload, ats=Ats(), agentic=object(), user_runs=object())
    result = runner.execute_claimed(claimed.id, datetime(2026, 9, 14, 8, tzinfo=UTC))
    assert result.status == "completed"
    request = received[0]
    assert request.remote_ok is False
    assert request.excluded_companies == ["Avoid"]
    assert request.excluded_title_terms == ["Sales"]
    assert request.employment_types == ["FullTime"]


def test_channel_statuses_zero_handoff_and_linked_run(db_session, monkeypatch):
    failed = StructuredAtsSourceDiagnostic(company="Acme", provider="greenhouse", source_token="acme", succeeded=False, discovered_count=0, imported_count=0, unchanged_count=0, updated_count=0, deduplicated_count=0, bounded_out_count=0, rejected_count=0, failure_kind="provider_failure")
    payload = _payload()
    runner, claimed = _claimed_runner(db_session, monkeypatch, payload=payload, ats=SimpleNamespace(discover=lambda _: _ats_response(diagnostics=[failed])), agentic=object(), user_runs=object())
    assert runner.execute_claimed(claimed.id, datetime(2026, 9, 14, 8, tzinfo=UTC)).status == "failed"

    listing = _listing()
    SqlAlchemyDiscoveredJobStateStore(db_session).persist([listing])
    calls = []
    run = SimpleNamespace(id="run-1", status=SimpleNamespace(value="completed"), funnel={"reused": 1, "analysed": 0, "relevance_screened": 1})
    class Runs:
        def start(self, user_id, request):
            calls.append(request)
            return run
    runner, claimed = _claimed_runner(db_session, monkeypatch, payload=payload, ats=SimpleNamespace(discover=lambda _: _ats_response([listing])), agentic=object(), user_runs=Runs())
    result = runner.execute_claimed(claimed.id, datetime(2026, 9, 14, 8, tzinfo=UTC))
    assert result.status == "completed" and result.discovery_run_id == "run-1"
    assert calls[0].discovered_job_ids  # scheduler passes canonical IDs to #154 without actionability filtering.


def test_partial_channel_and_manual_exception_are_terminalized(db_session, monkeypatch):
    payload = _payload(acquisition=AcquisitionConfig(structured_ats=StructuredAtsScheduleConfig(enabled=True, providers=["greenhouse"]), agentic_web=AgenticWebScheduleConfig(enabled=True)))
    listing = _listing()
    SqlAlchemyDiscoveredJobStateStore(db_session).persist([listing])
    run = SimpleNamespace(id="run-2", status=SimpleNamespace(value="completed"), funnel={})
    runner, claimed = _claimed_runner(db_session, monkeypatch, payload=payload, ats=SimpleNamespace(discover=lambda _: _ats_response([listing])), agentic=SimpleNamespace(discover=lambda _: _agentic_response(errors=True)), user_runs=SimpleNamespace(start=lambda *_: run))
    assert runner.execute_claimed(claimed.id, datetime(2026, 9, 14, 8, tzinfo=UTC)).status == "partial_failed"

    runner, claimed = _claimed_runner(db_session, monkeypatch, payload=_payload(), ats=object(), agentic=object(), user_runs=object())
    monkeypatch.setattr(runner, "execute_claimed", lambda *_: (_ for _ in ()).throw(RuntimeError("private failure")))
    result = runner._execute_safely(claimed.id, datetime(2026, 9, 14, 8, tzinfo=UTC))
    assert result.status == "failed" and result.completed_at is not None
    assert "private failure" not in result.failure_summary_json


def test_invalid_timezone_is_rejected_at_authenticated_api_boundary(client):
    credentials = {"email": "timezone-api@example.com", "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    response = client.post(
        "/api/v1/jobs/discovery-schedules",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "invalid-zone",
            "schedule": {"cadence": "daily", "timezone": "No/Such_Zone", "local_time": "09:00:00", "weekdays": []},
            "query": {"keywords": ["AI"]},
            "acquisition": {"structured_ats": {"enabled": True, "providers": ["greenhouse"]}},
        },
    )
    assert response.status_code == 422

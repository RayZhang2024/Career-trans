from datetime import datetime, time, timezone

import pytest
from sqlalchemy import select

from app.models.discovery_schedule import ScheduledDiscoveryExecution
from app.models.user import User
from app.schemas.discovery import JobSearchQuery
from app.schemas.discovery_schedule import AcquisitionConfig, DiscoveryScheduleCreate, DiscoverySchedulePatch, ScheduleCadence, ScheduleSpec, StructuredAtsScheduleConfig, TriggerKind
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
    bad = ScheduleSpec(cadence=ScheduleCadence.DAILY, timezone="No/Such_Zone", local_time=time(9))
    with pytest.raises(ValueError, match="IANA"):
        next_occurrence(bad, datetime.now(UTC))


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

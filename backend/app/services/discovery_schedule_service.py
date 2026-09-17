"""Persistence and explicit local-wall-time recurrence for discovery schedules."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.discovery_schedule import DiscoverySchedule, ScheduledDiscoveryExecution
from app.schemas.discovery import JobSearchQuery
from app.schemas.discovery_schedule import AcquisitionConfig, DiscoveryScheduleCreate, DiscoverySchedulePatch, DiscoveryScheduleRead, EvaluationConfig, ExecutionStatus, ScheduleSpec, ScheduledExecutionRead, TriggerKind


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Invalid IANA timezone.") from exc


def _valid_local(naive: datetime, zone: ZoneInfo) -> datetime | None:
    """Use the first ambiguous occurrence and skip imaginary local times."""
    aware = naive.replace(tzinfo=zone, fold=0)
    return aware if _utc(aware).astimezone(zone).replace(tzinfo=None) == naive else None


def _scheduled_on(spec: ScheduleSpec, candidate: datetime) -> bool:
    return spec.cadence.value == "daily" or candidate.weekday() in spec.weekdays


def next_occurrence(spec: ScheduleSpec, now: datetime) -> datetime:
    zone = _zone(spec.timezone)
    local_date = _utc(now).astimezone(zone).date()
    for offset in range(371):
        candidate = datetime.combine(local_date + timedelta(days=offset), spec.local_time)
        if not _scheduled_on(spec, candidate):
            continue
        local = _valid_local(candidate, zone)
        if local is not None and _utc(local) > _utc(now):
            return _utc(local)
    raise ValueError("No future valid schedule occurrence found.")


def most_recent_due(spec: ScheduleSpec, now: datetime) -> datetime | None:
    zone = _zone(spec.timezone)
    local_date = _utc(now).astimezone(zone).date()
    for offset in range(371):
        candidate = datetime.combine(local_date - timedelta(days=offset), spec.local_time)
        if not _scheduled_on(spec, candidate):
            continue
        local = _valid_local(candidate, zone)
        if local is not None and _utc(local) <= _utc(now):
            return _utc(local)
    return None


class DiscoveryScheduleService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, user_id: str, payload: DiscoveryScheduleCreate, now: datetime) -> DiscoveryScheduleRead:
        record = DiscoverySchedule(
            user_id=user_id, name=payload.name, enabled=payload.enabled,
            schedule_spec_json=_json(payload.schedule.model_dump(mode="json")),
            query_json=_json(payload.query.model_dump(mode="json")),
            acquisition_config_json=_json(payload.acquisition.model_dump(mode="json")),
            evaluation_config_json=_json(payload.evaluation.model_dump(mode="json")),
            next_run_at=next_occurrence(payload.schedule, now) if payload.enabled else None,
        )
        self._session.add(record)
        self._session.commit()
        return self.read(record)

    def get(self, user_id: str, schedule_id: str) -> DiscoverySchedule:
        record = self._session.scalar(select(DiscoverySchedule).where(DiscoverySchedule.id == schedule_id, DiscoverySchedule.user_id == user_id))
        if record is None:
            raise LookupError("Discovery schedule not found.")
        return record

    def list(self, user_id: str) -> list[DiscoveryScheduleRead]:
        records = self._session.scalars(select(DiscoverySchedule).where(DiscoverySchedule.user_id == user_id).order_by(DiscoverySchedule.created_at, DiscoverySchedule.id)).all()
        return [self.read(record) for record in records]

    def patch(self, user_id: str, schedule_id: str, payload: DiscoverySchedulePatch, now: datetime) -> DiscoveryScheduleRead:
        record = self.get(user_id, schedule_id)
        timing_changed, was_enabled = payload.schedule is not None, record.enabled
        if payload.name is not None:
            record.name = payload.name
        if payload.query is not None:
            record.query_json = _json(payload.query.model_dump(mode="json"))
        if payload.acquisition is not None:
            record.acquisition_config_json = _json(payload.acquisition.model_dump(mode="json"))
        if payload.evaluation is not None:
            record.evaluation_config_json = _json(payload.evaluation.model_dump(mode="json"))
        if payload.schedule is not None:
            record.schedule_spec_json = _json(payload.schedule.model_dump(mode="json"))
        if payload.enabled is not None:
            record.enabled = payload.enabled
        if not record.enabled:
            record.next_run_at = None
        elif timing_changed or not was_enabled:
            record.next_run_at = next_occurrence(self.spec(record), now)
        self._session.commit()
        return self.read(record)

    def executions(self, user_id: str, schedule_id: str) -> list[ScheduledExecutionRead]:
        self.get(user_id, schedule_id)
        records = self._session.scalars(select(ScheduledDiscoveryExecution).where(ScheduledDiscoveryExecution.schedule_id == schedule_id, ScheduledDiscoveryExecution.user_id == user_id).order_by(ScheduledDiscoveryExecution.started_at.desc(), ScheduledDiscoveryExecution.id.desc())).all()
        return [self.execution_read(record) for record in records]

    @staticmethod
    def spec(record: DiscoverySchedule) -> ScheduleSpec:
        return ScheduleSpec.model_validate(json.loads(record.schedule_spec_json))

    @staticmethod
    def snapshot(record: DiscoverySchedule) -> dict[str, object]:
        return {"schedule": json.loads(record.schedule_spec_json), "query": json.loads(record.query_json), "acquisition": json.loads(record.acquisition_config_json), "evaluation": json.loads(record.evaluation_config_json)}

    @staticmethod
    def read(record: DiscoverySchedule) -> DiscoveryScheduleRead:
        return DiscoveryScheduleRead(id=record.id, name=record.name, enabled=record.enabled, schedule=DiscoveryScheduleService.spec(record), query=JobSearchQuery.model_validate(json.loads(record.query_json)), acquisition=AcquisitionConfig.model_validate(json.loads(record.acquisition_config_json)), evaluation=EvaluationConfig.model_validate(json.loads(record.evaluation_config_json)), next_run_at=_utc(record.next_run_at) if record.next_run_at else None, last_execution_at=_utc(record.last_execution_at) if record.last_execution_at else None)

    @staticmethod
    def execution_read(record: ScheduledDiscoveryExecution) -> ScheduledExecutionRead:
        return ScheduledExecutionRead(id=record.id, trigger_kind=TriggerKind(record.trigger_kind), scheduled_for=_utc(record.scheduled_for) if record.scheduled_for else None, status=ExecutionStatus(record.status), config_snapshot=json.loads(record.config_snapshot_json), discovery_run_id=record.discovery_run_id, acquisition_summary=json.loads(record.acquisition_summary_json), failure_summary=json.loads(record.failure_summary_json), started_at=_utc(record.started_at), completed_at=_utc(record.completed_at) if record.completed_at else None)

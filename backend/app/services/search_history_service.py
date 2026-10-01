"""Provider-free, user-scoped logical Search History projection."""

import json
from datetime import datetime, timezone

from sqlalchemy import literal, select, union_all
from sqlalchemy.orm import Session

from app.models.one_off_discovery_execution import OneOffDiscoveryExecution
from app.models.user_job_discovery import DiscoveryRun
from app.schemas.one_off_discovery import OneOffExecutionRead
from app.schemas.search_history import (
    DiscoveryRunSearchHistoryItem,
    OneOffSearchHistoryItem,
    SearchHistoryResponse,
)
from app.services.user_job_discovery_service import UserJobDiscoveryHistoryReadService
from app.services.one_off_discovery_service import OneOffDiscoveryService
from app.services.one_off_discovery_service import ONE_OFF_STALE_AGE


class SearchHistoryReadService:
    """Compose both durable execution types before applying one stable window."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, user_id: str, *, limit: int = 20) -> SearchHistoryResponse:
        one_off = select(
            literal("one_off").label("kind"),
            OneOffDiscoveryExecution.id.label("item_id"),
            OneOffDiscoveryExecution.started_at.label("started_at"),
        ).where(OneOffDiscoveryExecution.user_id == user_id)
        linked_run_exists = select(OneOffDiscoveryExecution.id).where(
            OneOffDiscoveryExecution.user_id == user_id,
            OneOffDiscoveryExecution.discovery_run_id == DiscoveryRun.id,
        ).exists()
        ordinary_runs = select(
            literal("discovery_run").label("kind"),
            DiscoveryRun.id.label("item_id"),
            DiscoveryRun.started_at.label("started_at"),
        ).where(DiscoveryRun.user_id == user_id, ~linked_run_exists)
        projection = union_all(one_off, ordinary_runs).subquery("logical_search_history")
        rows = self._session.execute(
            select(projection.c.kind, projection.c.item_id, projection.c.started_at)
            .order_by(
                projection.c.started_at.desc(),
                projection.c.kind.asc(),
                projection.c.item_id.asc(),
            )
            .limit(limit + 1)
        ).all()
        page = rows[:limit]
        execution_ids = [row.item_id for row in page if row.kind == "one_off"]
        run_ids = [row.item_id for row in page if row.kind == "discovery_run"]
        executions = {
            row.id: row for row in self._session.scalars(select(OneOffDiscoveryExecution).where(
                OneOffDiscoveryExecution.user_id == user_id,
                OneOffDiscoveryExecution.id.in_(execution_ids),
            )).all()
        } if execution_ids else {}
        stale_recovered = False
        recovery_time = datetime.now(timezone.utc)
        for execution in executions.values():
            started = execution.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if execution.status == "running" and started < recovery_time - ONE_OFF_STALE_AGE:
                execution.status = "failed"
                execution.completed_at = recovery_time
                execution.failure_summary_json = json.dumps({"stale_execution": 1}, sort_keys=True)
                stale_recovered = True
        if stale_recovered:
            # This terminalizes only a stale claim; stored query, policy,
            # provider, and acquisition snapshots remain immutable.
            self._session.commit()
        runs = {
            row.id: row for row in self._session.scalars(select(DiscoveryRun).where(
                DiscoveryRun.user_id == user_id,
                DiscoveryRun.id.in_(run_ids),
            )).all()
        } if run_ids else {}
        history = UserJobDiscoveryHistoryReadService(self._session)
        items = []
        for entry in page:
            if entry.kind == "one_off":
                execution = executions.get(entry.item_id)
                if execution is None:
                    continue
                items.append(OneOffSearchHistoryItem(
                    id=execution.id,
                    started_at=entry.started_at,
                    execution=OneOffDiscoveryService.read(execution),
                ))
            else:
                run = runs.get(entry.item_id)
                if run is None:
                    continue
                items.append(DiscoveryRunSearchHistoryItem(
                    id=run.id,
                    started_at=entry.started_at,
                    run=history._run_summary(run),
                ))
        return SearchHistoryResponse(items=items, limit=limit, truncated=len(rows) > limit)

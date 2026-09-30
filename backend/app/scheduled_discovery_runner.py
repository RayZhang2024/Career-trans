"""Bounded server-runtime operator for due discovery schedules.

Run with: ``python -m app.scheduled_discovery_runner --limit 10``.
This deliberately is not the HTTP-only user CLI.
"""

import argparse
import json
from datetime import datetime, timezone

from app.api import deps
from app.core.database import SessionLocal
from app.schemas.discovery_schedule import ExecutionStatus
from app.services.ai_settings_service import AiSettingsService
from app.services.scheduled_discovery_execution_service import ScheduledDiscoveryExecutionService
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService


def build_service(
    session,
    *,
    candidate_reader: CanonicalCandidateReadService | None = None,
) -> ScheduledDiscoveryExecutionService:
    def build_user_runs(runtime_snapshot):
        ranking = deps.get_user_job_ranking_service(runtime_snapshot)
        return deps.get_user_job_discovery_service(session, ranking, runtime_snapshot)

    return ScheduledDiscoveryExecutionService(
        session,
        structured_ats=deps.get_structured_ats_discovery_service(session),
        agentic_web_factory=lambda user_id, snapshot: deps.get_user_agentic_job_discovery_service_for_user(
            session, user_id, snapshot, scheduled_due_runner=True
        ),
        user_runs_factory=build_user_runs,
        runtime_snapshot_resolver=lambda user_id: AiSettingsService(session).snapshot_for_user(user_id),
        candidate_reader=candidate_reader,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Process due Career-trans discovery schedules.")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 101))
    args = parser.parse_args()
    with SessionLocal() as session:
        executions = build_service(session).process_due(datetime.now(timezone.utc), args.limit)
    counts: dict[str, int] = {}
    for execution in executions:
        counts[execution.status] = counts.get(execution.status, 0) + 1
    print(json.dumps({"claimed": len(executions), "terminal_statuses": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from app.services.scheduled_discovery_execution_service import ScheduledDiscoveryExecutionService


def build_service(session) -> ScheduledDiscoveryExecutionService:
    graph = deps.get_career_analysis_graph(
        deps.get_job_analysis_service(),
        deps.get_requirement_matching_service(),
        deps.get_career_assessment_service(),
    )
    ranking = deps.get_job_ranking_service(deps.get_job_relevance_agent(), deps.get_job_archetype_agent(), graph)
    return ScheduledDiscoveryExecutionService(
        session,
        structured_ats=deps.get_structured_ats_discovery_service(session),
        agentic_web_factory=lambda user_id, snapshot: deps.get_user_agentic_job_discovery_service_for_user(
            session, user_id, snapshot
        ),
        user_runs=deps.get_user_job_discovery_service(session, ranking),
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

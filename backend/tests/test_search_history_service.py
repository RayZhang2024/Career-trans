from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.one_off_discovery_execution import OneOffDiscoveryExecution
from app.models.user_job_discovery import DiscoveryRun
from app.models.user import User
from app.schemas.discovery import JobSearchQuery
from app.schemas.one_off_discovery import OneOffPolicy
from app.services.search_history_service import SearchHistoryReadService
from app.api.deps import get_search_history_read_service
from app.core.security import create_access_token
from app.main import app as fastapi_app


def _run(user_id: str, run_id: str, started_at: datetime, status: str = "completed") -> DiscoveryRun:
    return DiscoveryRun(
        id=run_id, user_id=user_id, search_input_json='{"query":{"keywords":["ordinary"]}}',
        search_input_fingerprint="s" * 64, candidate_evaluation_fingerprint="c" * 64,
        evaluation_contract_fingerprint="e" * 64, status=status, funnel_json="{}",
        failure_summary_json="{}", started_at=started_at,
    )


def test_unified_search_history_is_user_scoped_ordered_and_duplicate_free_across_windows(db_session):
    user = User(id="history-owner", email="history-owner@example.test", password_hash="unused")
    other = User(id="history-other", email="history-other@example.test", password_hash="unused")
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    linked_ids = {f"linked-run-{i}" for i in range(0, 23, 5)}
    runs = [
        _run(user.id, run_id, base + timedelta(minutes=int(run_id.rsplit("-", 1)[1])))
        for run_id in linked_ids
    ]
    ordinary = [_run(user.id, f"ordinary-{i}", base + timedelta(minutes=i * 2 + 1)) for i in range(5)]
    foreign = _run(other.id, "foreign-run", base + timedelta(days=1))
    db_session.add_all([user, other, *runs, *ordinary, foreign])
    db_session.flush()
    for index in range(23):
        run_id = f"linked-run-{index}" if index in range(0, 23, 5) else None
        db_session.add(OneOffDiscoveryExecution(
            id=f"one-off-{index:02}", user_id=user.id, client_request_id=str(uuid4()),
            request_fingerprint=f"{index:064x}",
            query_snapshot_json=JobSearchQuery(keywords=[f"theme-{index}"]).model_dump_json(),
            policy_snapshot_json=OneOffPolicy().model_dump_json(), provider_metadata_json="{}",
            status="completed", started_at=base + timedelta(minutes=index * 2), completed_at=base + timedelta(minutes=index * 2 + 1),
            acquisition_summary_json='{"canonical_jobs":0}', failure_summary_json="{}",
            discovery_run_id=run_id,
        ))
    db_session.commit()

    service = SearchHistoryReadService(db_session)
    first = service.list(user.id, limit=20)
    full = service.list(user.id, limit=100)

    assert len(first.items) == 20
    assert first.truncated is True
    assert len(full.items) == 28
    assert full.truncated is False
    keys = [(item.type, item.id) for item in full.items]
    assert len(keys) == len(set(keys))
    assert all(item.id != "foreign-run" for item in full.items)
    assert all(run_id not in {item.id for item in full.items} for run_id in linked_ids)
    assert {item.id for item in full.items if item.type == "discovery_run"} == {run.id for run in ordinary}
    assert any(item.type == "one_off" and item.execution.acquisition_summary["canonical_jobs"] == 0 for item in full.items)
    assert list(full.items) == sorted(full.items, key=lambda item: (-item.started_at.timestamp(), item.type, item.id))


def test_search_history_route_is_authenticated_and_uses_provider_free_projection(client, db_session):
    user = User(id="history-api-owner", email="history-api-owner@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    service = SearchHistoryReadService(db_session)
    fastapi_app.dependency_overrides[get_search_history_read_service] = lambda: service
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    try:
        assert client.get("/api/v1/jobs/search-history").status_code == 401
        response = client.get("/api/v1/jobs/search-history?limit=20", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"items": [], "limit": 20, "truncated": False}
    finally:
        fastapi_app.dependency_overrides.pop(get_search_history_read_service, None)


def test_history_terminalizes_stale_running_one_off_without_provider_resolution(db_session):
    user = User(id="history-stale-owner", email="history-stale-owner@example.test", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    stale = OneOffDiscoveryExecution(
        id="stale-one-off", user_id=user.id, client_request_id=str(uuid4()),
        request_fingerprint="a" * 64,
        query_snapshot_json=JobSearchQuery(keywords=["AI"]).model_dump_json(),
        policy_snapshot_json=OneOffPolicy().model_dump_json(), provider_metadata_json='{"provider":"tavily"}',
        status="running", started_at=datetime.now(timezone.utc) - timedelta(hours=3),
        acquisition_summary_json="{}", failure_summary_json="{}",
    )
    db_session.add(stale)
    db_session.commit()

    result = SearchHistoryReadService(db_session).list(user.id)

    assert len(result.items) == 1
    assert result.items[0].type == "one_off"
    assert result.items[0].execution.status.value == "failed"
    assert result.items[0].execution.failure_summary == {"stale_execution": 1}
    assert db_session.get(OneOffDiscoveryExecution, stale.id).provider_metadata_json == '{"provider":"tavily"}'

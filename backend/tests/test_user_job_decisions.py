from datetime import datetime, timedelta, timezone

import pytest

from app.models.user_job_decision import UserJobDecision
from app.services.user_job_decision_service import UserJobDecisionConflict, UserJobDecisionService
from app.schemas.user_job_decision import UserJobDecisionMutation, UserJobDecisionValue
from test_jobs_read_models import _headers, _job, _user


def test_decision_absence_creation_transition_and_undo_retain_revision(db_session):
    job = _job(701)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    service = UserJobDecisionService(db_session)

    absent = service.read("owner", job.id)
    assert absent.decision == UserJobDecisionValue.UNDECIDED
    assert absent.revision is None and absent.created_at is None and absent.updated_at is None
    assert service.mutate("owner", job.id, UserJobDecisionMutation(decision="undecided", expected_revision=None)).revision is None
    assert db_session.query(UserJobDecision).count() == 0

    shortlisted = service.mutate("owner", job.id, UserJobDecisionMutation(decision="shortlisted"))
    assert shortlisted.revision == 1 and shortlisted.decision == UserJobDecisionValue.SHORTLISTED
    created_at = shortlisted.created_at
    updated_at = shortlisted.updated_at
    same = service.mutate("owner", job.id, UserJobDecisionMutation(decision="shortlisted", expected_revision=1))
    assert same.revision == 1 and same.updated_at == updated_at
    undone = service.mutate("owner", job.id, UserJobDecisionMutation(decision="undecided", expected_revision=1))
    assert undone.revision == 2 and undone.created_at == created_at and undone.updated_at != updated_at


def test_decision_cas_conflicts_and_user_scoping(db_session):
    job = _job(702)
    db_session.add_all([_user("owner"), _user("other"), job])
    db_session.commit()
    service = UserJobDecisionService(db_session)
    service.mutate("owner", job.id, UserJobDecisionMutation(decision="dismissed"))
    service.mutate("owner", job.id, UserJobDecisionMutation(decision="shortlisted", expected_revision=1))
    with pytest.raises(UserJobDecisionConflict):
        service.mutate("owner", job.id, UserJobDecisionMutation(decision="dismissed", expected_revision=1))
    assert service.read("other", job.id).decision == UserJobDecisionValue.UNDECIDED
    assert service.list("other", decision=UserJobDecisionValue.DISMISSED, limit=20).items == []
    with pytest.raises(LookupError):
        service.read("owner", "missing-job")


def test_decision_api_is_authenticated_and_validates_list_filter(client, db_session):
    job = _job(703)
    db_session.add_all([_user("owner"), job])
    db_session.commit()
    headers = _headers("owner")
    exact = client.get(f"/api/v1/jobs/decisions/{job.id}", headers=headers)
    assert exact.status_code == 200 and exact.json()["revision"] is None
    assert client.get(f"/api/v1/jobs/decisions/{job.id}").status_code == 401
    assert client.get(f"/api/v1/jobs/decisions?decision=undecided", headers=headers).status_code == 422
    assert client.get(f"/api/v1/jobs/decisions?decision=shortlisted&limit=0", headers=headers).status_code == 422
    changed = client.put(f"/api/v1/jobs/decisions/{job.id}", json={"decision": "shortlisted", "expected_revision": None}, headers=headers)
    assert changed.status_code == 200 and changed.json()["revision"] == 1
    listed = client.get("/api/v1/jobs/decisions?decision=shortlisted&limit=20", headers=headers)
    assert listed.status_code == 200 and listed.json()["items"][0]["discovered_job_id"] == job.id


def test_migration_is_additive_and_does_not_backfill():
    from pathlib import Path

    migration = Path(__file__).parents[1] / "migrations" / "20260929_user_job_decisions.sql"
    text = migration.read_text(encoding="utf-8")
    assert "CREATE TABLE user_job_decisions" in text
    assert "UNIQUE (user_id, discovered_job_id)" in text
    assert "revision >= 1" in text
    assert "INSERT" not in text.upper()

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.core.security import create_access_token
from app.models.application_preparation import ApplicationPreparation
from app.models.application_tracking import ApplicationTrackingEvent, ApplicationTrackingRecord
from app.models.user import User
from app.schemas.application_tracking import ApplicationTrackingStatusEventCreate
from app.services.application_tracking_service import ApplicationTrackingService


STATUSES = ("prepared", "applied", "interview", "rejected", "offer", "withdrawn")


def _seed_preparation(db_session, preparation_id="prep-1", user_id="owner", title="Historical Engineer", created_at=None):
    if db_session.get(User, user_id) is None:
        db_session.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="safe"))
    now = created_at or datetime(2025, 1, 2, tzinfo=timezone.utc)
    preparation = ApplicationPreparation(
        id=preparation_id,
        user_id=user_id,
        target_snapshot_json=json.dumps({
            "source_kind": "discovered_job", "title": title, "company": "Snapshot Co",
            "location": "London", "public_url": "https://example.test/vacancy",
        }),
        identity_snapshot_json="not required for tracking",
        preparation_input_fingerprint="a" * 64,
        preparation_contract_fingerprint="b" * 64,
        preparation_result_json="malformed result deliberately ignored by tracking",
        created_at=now,
    )
    db_session.add(preparation)
    db_session.commit()
    return preparation


def _headers(user_id):
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _create(client, preparation_id="prep-1", status="prepared", headers=None, extra=None):
    payload = {"preparation_id": preparation_id, "status": status}
    payload.update(extra or {})
    return client.post("/api/v1/application-tracking", json=payload, headers=headers or _headers("owner"))


@pytest.mark.parametrize("initial", STATUSES)
def test_create_persists_explicit_initial_status_and_one_revision_one_event(client, db_session, initial):
    _seed_preparation(db_session)
    requested_at = datetime.now(timezone.utc)
    response = _create(client, status=initial)
    completed_at = datetime.now(timezone.utc)
    assert response.status_code == 201
    detail = response.json()
    assert detail["current_status"] == initial
    assert detail["revision"] == 1
    assert len(detail["events"]) == 1
    assert detail["events"][0]["revision"] == 1
    assert detail["events"][0]["from_status"] is None
    assert detail["events"][0]["to_status"] == initial
    assert detail["events"][0]["recorded_at"]
    recorded_at = datetime.fromisoformat(detail["events"][0]["recorded_at"].replace("Z", "+00:00"))
    assert requested_at <= recorded_at <= completed_at
    assert recorded_at.utcoffset().total_seconds() == 0
    if initial == "applied":
        assert [event["to_status"] for event in detail["events"]] == ["applied"]


def test_initial_status_required_and_unknown_extra_recorded_at_rejected(client, db_session):
    _seed_preparation(db_session)
    assert client.post("/api/v1/application-tracking", json={"preparation_id": "prep-1"}, headers=_headers("owner")).status_code == 422
    assert _create(client, status="unknown").status_code == 422
    assert _create(client, extra={"recorded_at": "1900-01-01T00:00:00Z"}).status_code == 422
    assert db_session.scalars(select(ApplicationTrackingRecord)).all() == []


def test_unowned_or_missing_preparation_create_is_neutral_404(client, db_session):
    _seed_preparation(db_session, user_id="other")
    db_session.add(User(id="owner", email="owner@example.test", password_hash="safe"))
    db_session.commit()
    assert _create(client, "prep-1").status_code == 404
    assert _create(client, "missing").status_code == 404
    assert db_session.query(ApplicationTrackingRecord).count() == 0


def test_duplicate_create_is_database_guarded_safe_and_atomic(client, db_session):
    _seed_preparation(db_session)
    first = _create(client, status="applied")
    second = _create(client, status="interview")
    assert first.status_code == 201
    assert second.status_code == 409
    assert "UNIQUE" not in second.text.upper()
    assert db_session.query(ApplicationTrackingRecord).count() == 1
    events = db_session.scalars(select(ApplicationTrackingEvent)).all()
    assert len(events) == 1 and events[0].revision == 1 and events[0].to_status == "applied"
    assert any(constraint.name == "uq_application_tracking_preparation" for constraint in ApplicationTrackingRecord.__table__.constraints)


def test_initial_record_and_event_roll_back_together_when_event_insert_fails(db_session, monkeypatch):
    _seed_preparation(db_session)
    real_add = db_session.add

    def fail_initial_event(value):
        if isinstance(value, ApplicationTrackingEvent):
            raise RuntimeError("injected event insert failure")
        return real_add(value)

    monkeypatch.setattr(db_session, "add", fail_initial_event)
    from app.schemas.application_tracking import ApplicationTrackingCreate
    with pytest.raises(RuntimeError, match="injected event insert failure"):
        ApplicationTrackingService(db_session).create("owner", ApplicationTrackingCreate(preparation_id="prep-1", status="applied"))
    monkeypatch.setattr(db_session, "add", real_add)
    assert db_session.query(ApplicationTrackingRecord).count() == 0
    assert db_session.query(ApplicationTrackingEvent).count() == 0


def test_distinct_preparations_for_same_job_can_be_tracked(client, db_session):
    _seed_preparation(db_session, "prep-1")
    _seed_preparation(db_session, "prep-2")
    assert _create(client, "prep-1").status_code == 201
    assert _create(client, "prep-2").status_code == 201


def test_tracking_reads_use_target_snapshot_and_ignore_malformed_result(client, db_session):
    prep = _seed_preparation(db_session)
    created = _create(client)
    assert created.status_code == 201
    db_session.query(ApplicationPreparation).filter_by(id=prep.id).one().preparation_result_json = "not-json"
    db_session.commit()
    detail = client.get(f"/api/v1/application-tracking/{created.json()['id']}", headers=_headers("owner"))
    assert detail.status_code == 200
    assert detail.json()["target"]["title"] == "Historical Engineer"
    assert detail.json()["target"]["company"] == "Snapshot Co"
    assert detail.json()["target"]["preparation_created_at"].startswith("2025-01-02")


def test_scoped_list_lookup_detail_and_deterministic_order(client, db_session):
    _seed_preparation(db_session, "prep-old", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    _seed_preparation(db_session, "prep-new", created_at=datetime(2025, 2, 1, tzinfo=timezone.utc))
    _seed_preparation(db_session, "prep-other", user_id="other")
    old = _create(client, "prep-old").json()
    new = _create(client, "prep-new").json()
    other = _create(client, "prep-other", headers=_headers("other")).json()
    listed = client.get("/api/v1/application-tracking", headers=_headers("owner"))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [new["id"], old["id"]]
    assert client.get("/api/v1/application-tracking/by-preparation/prep-old", headers=_headers("owner")).json()["id"] == old["id"]
    assert client.get(f"/api/v1/application-tracking/{other['id']}", headers=_headers("owner")).status_code == 404
    assert client.get("/api/v1/application-tracking/by-preparation/prep-other", headers=_headers("owner")).status_code == 404
    assert client.get("/api/v1/application-tracking/by-preparation/untracked", headers=_headers("owner")).status_code == 404


def test_status_events_are_atomic_revision_cas_and_any_different_status_is_allowed(client, db_session):
    _seed_preparation(db_session)
    detail = _create(client, status="rejected").json()
    tracking_id = detail["id"]
    response = client.post(
        f"/api/v1/application-tracking/{tracking_id}/status-events",
        json={"status": "interview", "expected_revision": 1}, headers=_headers("owner"),
    )
    assert response.status_code == 200
    changed = response.json()
    assert changed["current_status"] == "interview" and changed["revision"] == 2
    assert changed["events"][-1]["from_status"] == "rejected"
    assert changed["events"][-1]["to_status"] == "interview"
    assert [event["revision"] for event in changed["events"]] == [1, 2]
    assert changed["current_status"] == changed["events"][-1]["to_status"]
    assert changed["revision"] == changed["events"][-1]["revision"]

    no_op = client.post(f"/api/v1/application-tracking/{tracking_id}/status-events", json={"status": "interview", "expected_revision": 2}, headers=_headers("owner"))
    stale = client.post(f"/api/v1/application-tracking/{tracking_id}/status-events", json={"status": "offer", "expected_revision": 1}, headers=_headers("owner"))
    assert no_op.status_code == 409 and stale.status_code == 409
    latest = client.get(f"/api/v1/application-tracking/{tracking_id}", headers=_headers("owner")).json()
    assert latest["revision"] == 2 and latest["current_status"] == "interview" and len(latest["events"]) == 2

    # A later transition back to an earlier state remains valid; no rigid graph.
    back = client.post(f"/api/v1/application-tracking/{tracking_id}/status-events", json={"status": "prepared", "expected_revision": 2}, headers=_headers("owner"))
    assert back.status_code == 200 and back.json()["current_status"] == "prepared"


def test_event_insertion_failure_rolls_back_cas_record_change(client, db_session, monkeypatch):
    _seed_preparation(db_session)
    created = _create(client).json()
    real_add = db_session.add

    def fail_event(value):
        if isinstance(value, ApplicationTrackingEvent) and value.revision > 1:
            raise RuntimeError("injected persistence failure")
        return real_add(value)

    monkeypatch.setattr(db_session, "add", fail_event)
    with pytest.raises(RuntimeError, match="injected persistence failure"):
        ApplicationTrackingService(db_session).append_status_event(
            "owner", created["id"],
            ApplicationTrackingStatusEventCreate(status="applied", expected_revision=1),
        )
    monkeypatch.setattr(db_session, "add", real_add)
    db_session.rollback()
    stored = db_session.get(ApplicationTrackingRecord, created["id"])
    events = db_session.scalars(select(ApplicationTrackingEvent).where(ApplicationTrackingEvent.tracking_id == created["id"]).order_by(ApplicationTrackingEvent.revision)).all()
    assert stored.current_status == "prepared" and stored.revision == 1
    assert [(event.revision, event.from_status, event.to_status) for event in events] == [(1, None, "prepared")]


def test_cas_miss_classifies_inaccessible_as_404_and_owned_as_409(client, db_session):
    _seed_preparation(db_session)
    _seed_preparation(db_session, "prep-other", user_id="other")
    owner = _create(client).json()
    other = _create(client, "prep-other", headers=_headers("other")).json()
    stale = client.post(f"/api/v1/application-tracking/{owner['id']}/status-events", json={"status": "applied", "expected_revision": 99}, headers=_headers("owner"))
    inaccessible = client.post(f"/api/v1/application-tracking/{other['id']}/status-events", json={"status": "applied", "expected_revision": 1}, headers=_headers("owner"))
    assert stale.status_code == 409 and inaccessible.status_code == 404
    assert "other" not in inaccessible.text


def test_tracking_reads_and_writes_use_real_provider_free_dependency_path(client, db_session, monkeypatch):
    from app.providers import llm

    def forbidden(*_args, **_kwargs):
        raise AssertionError("tracking must not construct a semantic provider")

    monkeypatch.setattr(llm.SemanticResponseClient, "__init__", forbidden)
    _seed_preparation(db_session)
    created = _create(client)
    assert created.status_code == 201
    tracking_id = created.json()["id"]
    assert client.get("/api/v1/application-tracking", headers=_headers("owner")).status_code == 200
    assert client.get(f"/api/v1/application-tracking/by-preparation/prep-1", headers=_headers("owner")).status_code == 200
    assert client.get(f"/api/v1/application-tracking/{tracking_id}", headers=_headers("owner")).status_code == 200
    assert client.post(
        f"/api/v1/application-tracking/{tracking_id}/status-events",
        json={"status": "interview", "expected_revision": 1}, headers=_headers("owner"),
    ).status_code == 200


def test_metadata_declares_foreign_keys_uniqueness_and_event_shape_constraints(db_session):
    record_table = ApplicationTrackingRecord.__table__
    event_table = ApplicationTrackingEvent.__table__
    assert any(fk.target_fullname == "application_preparations.id" for fk in record_table.c.preparation_id.foreign_keys)
    assert any(fk.target_fullname == "application_tracking.id" for fk in event_table.c.tracking_id.foreign_keys)
    assert any(constraint.name == "uq_application_tracking_event_revision" for constraint in event_table.constraints)
    assert any(constraint.name == "ck_application_tracking_event_from_status" for constraint in event_table.constraints)
    assert "user_id" not in record_table.c
    assert inspect(db_session.bind).has_table("application_tracking")


def test_database_rejects_duplicate_event_revision(client, db_session):
    _seed_preparation(db_session)
    created = _create(client).json()
    db_session.add(ApplicationTrackingEvent(
        tracking_id=created["id"], revision=1, from_status=None,
        to_status="prepared", recorded_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    events = db_session.scalars(select(ApplicationTrackingEvent).where(ApplicationTrackingEvent.tracking_id == created["id"])).all()
    assert len(events) == 1 and events[0].revision == 1


@pytest.mark.parametrize("revision,from_status", [(1, "prepared"), (2, None)])
def test_database_rejects_invalid_event_from_status_shape(client, db_session, revision, from_status):
    _seed_preparation(db_session)
    created = _create(client).json()
    db_session.add(ApplicationTrackingEvent(
        tracking_id=created["id"], revision=revision, from_status=from_status,
        to_status="applied", recorded_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    assert db_session.query(ApplicationTrackingEvent).filter_by(tracking_id=created["id"]).count() == 1

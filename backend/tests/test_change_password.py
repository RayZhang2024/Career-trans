from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.models.candidate_profile import CandidateProfile
from app.models.user_ai_settings import UserAiSettings
from app.models.user import User
from app.services.auth_service import PasswordChangeConflict, change_password


def register_and_login(client: TestClient, email: str, password: str = "strong-password") -> str:
    registered = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert registered.status_code == 201
    logged_in = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert logged_in.status_code == 200
    return logged_in.json()["access_token"]


def test_change_password_updates_hash_generation_and_revokes_old_sessions(client: TestClient, db_session) -> None:
    old_password = "strong-password"
    token = register_and_login(client, "owner@example.com", old_password)
    account = db_session.query(User).filter_by(email="owner@example.com").one()
    db_session.add(CandidateProfile(user_id=account.id, headline="Confirmed profile survives password changes"))
    stored_preferences = '{"default_model":"offline-model","default_reasoning_effort":null,"operation_overrides":{}}'
    db_session.add(UserAiSettings(
        user_id=account.id,
        provider="openai",
        revision=4,
        preferences_json=stored_preferences,
    ))
    db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": old_password, "new_password": "Better-password-42"},
    )

    assert response.status_code == 200
    assert response.json() == {"changed": True}
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": old_password}).status_code == 401
    new_login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "Better-password-42"},
    )
    assert new_login.status_code == 200
    new_user = db_session.query(User).filter_by(email="owner@example.com").one()
    assert new_user.auth_version == 1
    assert new_user.password_hash != "Better-password-42"
    assert verify_password("Better-password-42", new_user.password_hash)
    fresh_headers = {"Authorization": f"Bearer {new_login.json()['access_token']}"}
    retained_profile = client.get("/api/v1/profile", headers=fresh_headers)
    assert retained_profile.status_code == 200
    assert retained_profile.json()["headline"] == "Confirmed profile survives password changes"
    retained_preferences = db_session.query(UserAiSettings).filter_by(user_id=new_user.id).one()
    assert retained_preferences.revision == 4
    assert retained_preferences.preferences_json == stored_preferences


@pytest.mark.parametrize(
    ("current_password", "new_password", "expected_status", "expected_detail"),
    [
        ("wrong-current", "Better-password-42", 400, "current_password_incorrect"),
        ("strong-password", "strong-password", 422, "new_password_must_differ"),
        ("strong-password", "short", 422, "password_too_short"),
        ("strong-password", "contains whitespace", 422, "password_contains_whitespace"),
        ("strong-password", "password1234567", 422, "password_too_common"),
    ],
)
def test_rejected_password_change_does_not_mutate_account(
    client: TestClient,
    db_session,
    current_password: str,
    new_password: str,
    expected_status: int,
    expected_detail: str,
) -> None:
    token = register_and_login(client, "owner@example.com")
    response = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": current_password, "new_password": new_password},
    )
    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    account = db_session.query(User).filter_by(email="owner@example.com").one()
    assert account.auth_version == 0
    assert verify_password("strong-password", account.password_hash)


def test_change_password_requires_auth_and_only_changes_authenticated_account(client: TestClient, db_session) -> None:
    assert client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "strong-password", "new_password": "Better-password-42"},
    ).status_code == 401
    owner_token = register_and_login(client, "owner@example.com")
    other_token = register_and_login(client, "other@example.com")

    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"current_password": "strong-password", "new_password": "Better-password-42"},
    )
    assert changed.status_code == 200
    assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {other_token}"}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "other@example.com", "password": "strong-password"}).status_code == 200


def test_old_generationless_tokens_work_only_for_generation_zero(client: TestClient, db_session) -> None:
    token = register_and_login(client, "legacy@example.com")
    account = db_session.query(User).filter_by(email="legacy@example.com").one()
    settings = get_settings()
    legacy_token = jwt.encode(
        {"sub": account.id, "iat": datetime.now(timezone.utc), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    mismatch_token = jwt.encode(
        {"sub": account.id, "auth_version": 3, "iat": datetime.now(timezone.utc), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {legacy_token}"}).status_code == 200
    assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {mismatch_token}"}).status_code == 401

    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "strong-password", "new_password": "Better-password-42"},
    )
    assert changed.status_code == 200
    assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {legacy_token}"}).status_code == 401


def test_password_update_and_generation_roll_back_together_if_commit_fails(client: TestClient, db_session) -> None:
    token = register_and_login(client, "rollback@example.com")
    with patch.object(db_session, "commit", side_effect=OperationalError("commit", {}, RuntimeError("offline"))):
        response = client.post(
            "/api/v1/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "strong-password", "new_password": "Better-password-42"},
        )
    assert response.status_code == 500
    assert response.json()["detail"] == "password_change_unavailable"
    assert "Better-password-42" not in response.text
    db_session.expire_all()
    account = db_session.query(User).filter_by(email="rollback@example.com").one()
    assert account.auth_version == 0
    assert verify_password("strong-password", account.password_hash)
    assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_stale_concurrent_password_change_cannot_overwrite_newer_generation(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'concurrent-password-change.sqlite3').as_posix()}")
    from app.core.database import Base

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as first, sessions() as second:
        account = User(email="race@example.com", password_hash=hash_password("strong-password"))
        first.add(account)
        first.commit()
        account_id = account.id
        first_copy = first.get(User, account_id)
        second_copy = second.get(User, account_id)
        assert first_copy is not None and second_copy is not None

        change_password(first, first_copy, "strong-password", "First-new-password-1")
        with pytest.raises(PasswordChangeConflict):
            change_password(second, second_copy, "strong-password", "Second-new-password-2")

        second.expire_all()
        current = second.get(User, account_id)
        assert current is not None
        assert current.auth_version == 1
        assert verify_password("First-new-password-1", current.password_hash)
        assert not verify_password("Second-new-password-2", current.password_hash)
    engine.dispose()


def test_change_request_rejects_confirmation_or_other_client_only_fields(client: TestClient) -> None:
    token = register_and_login(client, "strict-request@example.com")
    response = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "strong-password", "new_password": "Better-password-42", "confirmation": "Better-password-42"},
    )
    assert response.status_code == 422

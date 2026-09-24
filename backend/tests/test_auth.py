from fastapi.testclient import TestClient

import pytest

from app.core.password_policy import PasswordPolicyError, validate_new_password
from app.core.security import hash_password
from app.models.user import User


def test_register_login_and_me(client: TestClient) -> None:
    register_response = client.post(
        "/api/v1/auth/register",
        json={"email": "person@example.com", "password": "strong-password"},
    )
    assert register_response.status_code == 201
    assert register_response.json()["email"] == "person@example.com"
    assert "password" not in register_response.json()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "person@example.com", "password": "strong-password"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    me_response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "person@example.com"


def test_duplicate_registration_is_rejected(client: TestClient) -> None:
    payload = {"email": "person@example.com", "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_protected_endpoint_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


@pytest.mark.parametrize("password", ["a" * 14, "a" * 129])
def test_new_password_rejects_out_of_range_lengths(client: TestClient, password: str) -> None:
    response = client.post("/api/v1/auth/register", json={"email": "length@example.com", "password": password})
    assert response.status_code == 422
    assert response.json()["detail"] == ("password_too_short" if len(password) == 14 else "password_too_long")
    assert password not in response.text


@pytest.mark.parametrize("password", ["a" * 15, "a" * 128, "long passphrase with spaces", "Unicode密码和表情🙂组成的超长安全口令", "a" * 15])
def test_new_password_accepts_policy_compliant_values(client: TestClient, password: str) -> None:
    validate_new_password(password)
    response = client.post("/api/v1/auth/register", json={"email": f"{len(password)}-{abs(hash(password))}@example.com", "password": password})
    assert response.status_code == 201


def test_policy_has_no_composition_requirement_and_accepts_whitespace_without_trimming() -> None:
    password = "  abcdefghijk  "
    assert len(password) == 15
    validate_new_password(password)
    with pytest.raises(PasswordPolicyError) as error:
        validate_new_password(" " + "a" * 13)
    assert error.value.reason == "password_too_short"
    assert password not in str(error.value)


def test_registration_preserves_password_whitespace(client: TestClient) -> None:
    password = "  abcdefghijk  "
    registered = client.post("/api/v1/auth/register", json={"email": "spaces@example.com", "password": password})
    assert registered.status_code == 201
    assert client.post("/api/v1/auth/login", json={"email": "spaces@example.com", "password": password}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "spaces@example.com", "password": password.strip()}).status_code == 401


def test_common_password_is_rejected_by_shared_validator_and_registration(client: TestClient, caplog) -> None:
    password = "password1234567"
    with pytest.raises(PasswordPolicyError) as error:
        validate_new_password(password)
    assert error.value.reason == "password_too_common"

    response = client.post("/api/v1/auth/register", json={"email": "common@example.com", "password": password})
    assert response.status_code == 422
    assert response.json()["detail"] == "password_too_common"
    assert password not in response.text
    assert password not in caplog.text


def test_password_policy_endpoint_reflects_authoritative_contract(client: TestClient) -> None:
    response = client.get("/api/v1/auth/password-policy")
    assert response.status_code == 200
    assert response.json() == {
        "version": 1,
        "min_length": 15,
        "max_length": 128,
        "common_passwords_rejected": True,
        "composition_requirements": [],
    }
    assert "password123456" not in response.text


def test_legacy_eight_character_password_can_still_log_in(client: TestClient, db_session) -> None:
    password = "old-pass"
    db_session.add(User(email="legacy@example.com", password_hash=hash_password(password)))
    db_session.commit()

    response = client.post("/api/v1/auth/login", json={"email": "legacy@example.com", "password": password})
    assert response.status_code == 200
    assert response.json()["access_token"]

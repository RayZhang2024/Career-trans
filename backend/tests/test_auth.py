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


@pytest.mark.parametrize("password", ["a" * 7, "a" * 129])
def test_new_password_rejects_out_of_range_lengths(client: TestClient, password: str) -> None:
    response = client.post("/api/v1/auth/register", json={"email": "length@example.com", "password": password})
    assert response.status_code == 422
    assert response.json()["detail"] == ("password_too_short" if len(password) == 7 else "password_too_long")
    assert password not in response.text


@pytest.mark.parametrize("password", ["abcdefgh", "a" * 128, "Unicode密码和表情🙂组成的安全口令", "aB3!xY7z"])
def test_new_password_accepts_policy_compliant_values(client: TestClient, password: str) -> None:
    validate_new_password(password)
    response = client.post("/api/v1/auth/register", json={"email": f"{len(password)}-{abs(hash(password))}@example.com", "password": password})
    assert response.status_code == 201


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "\r", "\v", "\f", "\u0085", "\u00a0", "\u2003"])
def test_new_password_rejects_whitespace_anywhere(client: TestClient, whitespace: str) -> None:
    password = f"abcd{whitespace}efgh"
    with pytest.raises(PasswordPolicyError) as error:
        validate_new_password(password)
    assert error.value.reason == "password_contains_whitespace"
    response = client.post("/api/v1/auth/register", json={"email": f"space-{ord(whitespace):x}@example.com", "password": password})
    assert response.status_code == 422
    assert response.json()["detail"] == "password_contains_whitespace"


def test_policy_keeps_existing_common_password_requirement_and_has_no_composition_requirement() -> None:
    with pytest.raises(PasswordPolicyError) as error:
        validate_new_password("password1234567")
    assert error.value.reason == "password_too_common"
    validate_new_password("abcdefgh")


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
        "version": 2,
        "min_length": 8,
        "max_length": 128,
        "common_passwords_rejected": True,
        "composition_requirements": [],
        "whitespace_allowed": False,
    }
    assert "password123456" not in response.text


def test_legacy_eight_character_password_can_still_log_in(client: TestClient, db_session) -> None:
    password = "old-pass"
    db_session.add(User(email="legacy@example.com", password_hash=hash_password(password)))
    db_session.commit()

    response = client.post("/api/v1/auth/login", json={"email": "legacy@example.com", "password": password})
    assert response.status_code == 200
    assert response.json()["access_token"]

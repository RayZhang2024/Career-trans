from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.candidate_profile import CandidateProfile
from app.models.user import User


def register_and_login(client: TestClient, email: str) -> str:
    password = "strong-password"
    register_response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert register_response.status_code == 201

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_response.status_code == 200
    return login_response.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_public_profile_mutations_require_revision_confirmation(client: TestClient) -> None:
    token = register_and_login(client, "a@example.com")

    create_response = client.post(
        "/api/v1/profile",
        headers=auth_header(token),
        json={
            "headline": "Data Engineer",
            "current_role": "Engineer",
            "location": "London",
            "career_goal": "Move into applied AI.",
            "job_search_criteria": "Prefer permanent technical roles.",
        },
    )
    assert create_response.status_code == 409
    assert "revision workflow" in create_response.json()["detail"]
    assert client.get("/api/v1/profile", headers=auth_header(token)).status_code == 404

    update_response = client.patch(
        "/api/v1/profile",
        headers=auth_header(token),
        json={"location": "Oxford"},
    )
    assert update_response.status_code == 409
    assert "revision workflow" in update_response.json()["detail"]


def test_profiles_are_isolated_between_users(client: TestClient) -> None:
    token_a = register_and_login(client, "a@example.com")
    token_b = register_and_login(client, "b@example.com")

    response_a = client.post(
        "/api/v1/profile",
        headers=auth_header(token_a),
        json={"headline": "Profile A", "location": "London", "job_search_criteria": "Criteria A"},
    )
    response_b = client.post(
        "/api/v1/profile",
        headers=auth_header(token_b),
        json={"headline": "Profile B", "location": "Manchester", "job_search_criteria": "Criteria B"},
    )
    assert response_a.status_code == 409
    assert response_b.status_code == 409

    read_a = client.get("/api/v1/profile", headers=auth_header(token_a))
    read_b = client.get("/api/v1/profile", headers=auth_header(token_b))

    assert read_a.status_code == 404
    assert read_b.status_code == 404


def test_profile_read_returns_existing_user_scoped_profile(client: TestClient, db_session) -> None:
    token_a = register_and_login(client, "profile-owner@example.com")
    token_b = register_and_login(client, "profile-other@example.com")
    user_a = db_session.scalar(select(User).where(User.email == "profile-owner@example.com"))
    assert user_a is not None

    profile = CandidateProfile(
        user_id=user_a.id,
        headline="Data Engineer",
        summary="Builds reliable data platforms.",
        current_role="Senior Engineer",
        location="London",
        career_goal="Lead data platform work.",
        job_search_criteria="Permanent engineering roles.",
        display_name="Profile Owner",
        preferred_email="owner@example.com",
    )
    db_session.add(profile)
    db_session.commit()

    owner_response = client.get("/api/v1/profile", headers=auth_header(token_a))
    assert owner_response.status_code == 200
    owner_profile = owner_response.json()
    assert owner_profile["id"] == profile.id
    assert owner_profile["user_id"] == user_a.id
    assert owner_profile["headline"] == "Data Engineer"
    assert owner_profile["summary"] == "Builds reliable data platforms."
    assert owner_profile["current_role"] == "Senior Engineer"
    assert owner_profile["location"] == "London"
    assert owner_profile["career_goal"] == "Lead data platform work."
    assert owner_profile["job_search_criteria"] == "Permanent engineering roles."
    assert owner_profile["display_name"] == "Profile Owner"
    assert owner_profile["preferred_email"] == "owner@example.com"

    other_response = client.get("/api/v1/profile", headers=auth_header(token_b))
    assert other_response.status_code == 404
    assert other_response.json()["detail"] == "Profile not found."


def test_legacy_profile_post_tombstone_does_not_mutate_any_user_profiles(client) -> None:
    token_a = register_and_login(client, "bootstrap-a@example.com")
    token_b = register_and_login(client, "bootstrap-b@example.com")

    created = client.post(
        "/api/v1/profile",
        headers=auth_header(token_a),
        json={"career_goal": "Build durable products.", "job_search_criteria": "Prefer technical roles."},
    )
    assert created.status_code == 409
    assert client.get("/api/v1/profile", headers=auth_header(token_b)).status_code == 404
    assert client.get("/api/v1/profile/context-summary", headers=auth_header(token_a)).json()["ready"] is False

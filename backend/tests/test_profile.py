from fastapi.testclient import TestClient


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


def test_user_can_create_read_and_update_own_profile(client: TestClient) -> None:
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
    assert create_response.status_code == 201
    assert create_response.json()["headline"] == "Data Engineer"

    read_response = client.get("/api/v1/profile", headers=auth_header(token))
    assert read_response.status_code == 200
    assert read_response.json()["location"] == "London"
    assert read_response.json()["career_goal"] == "Move into applied AI."
    assert read_response.json()["job_search_criteria"] == "Prefer permanent technical roles."

    update_response = client.patch(
        "/api/v1/profile",
        headers=auth_header(token),
        json={"location": "Oxford"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["location"] == "Oxford"
    assert update_response.json()["career_goal"] == "Move into applied AI."
    assert update_response.json()["job_search_criteria"] == "Prefer permanent technical roles."

    strategy_response = client.patch(
        "/api/v1/profile",
        headers=auth_header(token),
        json={"job_search_criteria": "Prefer hybrid engineering roles."},
    )
    assert strategy_response.status_code == 200
    assert strategy_response.json()["career_goal"] == "Move into applied AI."
    assert strategy_response.json()["job_search_criteria"] == "Prefer hybrid engineering roles."

    goal_response = client.patch(
        "/api/v1/profile",
        headers=auth_header(token),
        json={"career_goal": "Build dependable applied systems."},
    )
    assert goal_response.status_code == 200
    assert goal_response.json()["career_goal"] == "Build dependable applied systems."
    assert goal_response.json()["job_search_criteria"] == "Prefer hybrid engineering roles."


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
    assert response_a.status_code == 201
    assert response_b.status_code == 201

    read_a = client.get("/api/v1/profile", headers=auth_header(token_a))
    read_b = client.get("/api/v1/profile", headers=auth_header(token_b))

    assert read_a.json()["headline"] == "Profile A"
    assert read_a.json()["location"] == "London"
    assert read_b.json()["headline"] == "Profile B"
    assert read_b.json()["location"] == "Manchester"
    assert read_a.json()["job_search_criteria"] == "Criteria A"
    assert read_b.json()["job_search_criteria"] == "Criteria B"
    assert read_a.json()["user_id"] != read_b.json()["user_id"]


def test_strategy_profile_bootstrap_is_scoped_to_authenticated_user_and_does_not_create_cv_context(client) -> None:
    token_a = register_and_login(client, "bootstrap-a@example.com")
    token_b = register_and_login(client, "bootstrap-b@example.com")

    created = client.post(
        "/api/v1/profile",
        headers=auth_header(token_a),
        json={"career_goal": "Build durable products.", "job_search_criteria": "Prefer technical roles."},
    )
    assert created.status_code == 201
    assert created.json()["user_id"] != "bootstrap-b@example.com"
    assert client.get("/api/v1/profile", headers=auth_header(token_b)).status_code == 404
    assert client.get("/api/v1/profile/context-summary", headers=auth_header(token_a)).json()["ready"] is False

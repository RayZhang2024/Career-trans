from sqlalchemy import select

from app.models.discovered_job import DiscoveredJob
from app.models.discovered_job_provenance import DiscoveredJobProvenance


def _auth_headers(client) -> dict[str, str]:
    credentials = {"email": "runtime-user@example.com", "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _payload(*jobs, **overrides):
    payload = {
        "runtime": "codex",
        "jobs": list(jobs),
        "query": {"keywords": ["Applied AI Engineer"], "locations": ["London"], "max_results": 10},
    }
    payload.update(overrides)
    return payload


def _job(**overrides):
    payload = {
        "title": "Forward Deployed Engineer",
        "company": "Example Systems",
        "location": "London, UK",
        "url": "https://careers.example.test/jobs/forward-deployed-engineer?tracking=ignored",
        "description": "Build customer-facing AI systems.",
        "employment_type": "Permanent",
        "provenance": {"source_ref": "public-search-result", "discovered_via": "web"},
    }
    payload.update(overrides)
    return payload


def test_authenticated_import_persists_runtime_provenance_and_is_non_authoritative(client, db_session) -> None:
    response = client.post(
        "/api/v1/jobs/import-discovered",
        json=_payload(_job()),
        headers=_auth_headers(client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["runtime"] == "codex"
    assert body["authoritative"] is False
    assert body["lifecycle_counts"] == {"new": 1, "updated": 0, "unchanged": 0, "inactive": 0}
    assert body["accepted_jobs"][0]["source"] == "agent_runtime"
    assert body["accepted_jobs"][0]["source_token"] == "codex"
    assert body["accepted_jobs"][0]["url"] == "https://careers.example.test/jobs/forward-deployed-engineer"
    assert body["accepted_jobs"][0]["provenance"] == {
        "runtime": "codex",
        "source_ref": "public-search-result",
        "discovered_via": "web",
    }

    record = db_session.scalar(select(DiscoveredJob))
    assert record is not None
    assert record.source == "agent_runtime"
    assert record.source_token == "codex"
    assert record.first_seen_at is not None
    provenance = db_session.scalar(select(DiscoveredJobProvenance))
    assert provenance is not None
    assert provenance.job_id == record.id
    assert provenance.runtime == "codex"
    assert provenance.source_ref == "public-search-result"
    assert provenance.discovered_via == "web"
    assert provenance.imported_at is not None


def test_import_requires_authentication(client) -> None:
    response = client.post("/api/v1/jobs/import-discovered", json=_payload(_job()))
    assert response.status_code == 401


def test_import_rejects_malformed_urls_and_bounded_untrusted_fields(client) -> None:
    headers = _auth_headers(client)
    malformed = client.post(
        "/api/v1/jobs/import-discovered",
        json=_payload(_job(url="javascript:alert(1)")),
        headers=headers,
    )
    assert malformed.status_code == 422

    too_many = client.post(
        "/api/v1/jobs/import-discovered",
        json=_payload(*[_job(url=f"https://careers.example.test/jobs/{index}") for index in range(26)]),
        headers=headers,
    )
    assert too_many.status_code == 422

    hidden_reasoning = client.post(
        "/api/v1/jobs/import-discovered",
        json=_payload(_job(provenance={"source_ref": "public"}, reasoning="private scratchpad")),
        headers=headers,
    )
    assert hidden_reasoning.status_code == 422


def test_import_deduplicates_before_persistence_and_uses_hard_constraints_only(client, db_session) -> None:
    headers = _auth_headers(client)
    first = _job()
    duplicate = _job(url="https://careers.example.test/jobs/forward-deployed-engineer/")
    response = client.post("/api/v1/jobs/import-discovered", json=_payload(first, duplicate), headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["accepted_jobs"]) == 1
    assert body["deduplicated_count"] == 1
    assert db_session.scalars(select(DiscoveredJob)).all()[0].title == "Forward Deployed Engineer"

    # The broad runtime has already established relevance: no exact lexical phrase gate applies.
    assert body["accepted_jobs"][0]["title"] == "Forward Deployed Engineer"


def test_rejected_jobs_are_not_persisted_and_explicit_policy_constraints_remain(client, db_session) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/api/v1/jobs/import-discovered",
        json=_payload(
            _job(title="Excluded Engineer"),
            query={
                "keywords": ["Applied AI Engineer"],
                "locations": ["London"],
                "employment_types": ["Permanent"],
                "excluded_title_terms": ["excluded"],
                "excluded_companies": ["Blocked"],
            },
        ),
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["accepted_jobs"] == []
    assert response.json()["rejected_count"] == 1
    assert db_session.scalars(select(DiscoveredJob)).all() == []

    company_response = client.post(
        "/api/v1/jobs/import-discovered",
        json=_payload(
            _job(company="Blocked Holdings"),
            query={"keywords": ["Applied AI Engineer"], "excluded_companies": ["Blocked"]},
        ),
        headers=headers,
    )
    assert company_response.json()["accepted_jobs"] == []

    location_response = client.post(
        "/api/v1/jobs/import-discovered",
        json=_payload(_job(location="Berlin, Germany")),
        headers=headers,
    )
    assert location_response.json()["accepted_jobs"] == []

    employment_response = client.post(
        "/api/v1/jobs/import-discovered",
        json=_payload(
            _job(employment_type="Contract"),
            query={"keywords": ["Applied AI Engineer"], "employment_types": ["Permanent"]},
        ),
        headers=headers,
    )
    assert employment_response.json()["accepted_jobs"] == []


def test_later_external_import_never_marks_omitted_job_inactive(client, db_session) -> None:
    headers = _auth_headers(client)
    assert client.post("/api/v1/jobs/import-discovered", json=_payload(_job()), headers=headers).status_code == 200
    response = client.post(
        "/api/v1/jobs/import-discovered",
        json=_payload(_job(url="https://careers.example.test/jobs/second-role", title="Solutions Engineer")),
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["lifecycle_counts"]["inactive"] == 0
    assert all(record.state != "inactive" for record in db_session.scalars(select(DiscoveredJob)).all())


def test_authenticated_search_context_is_compact_and_contains_no_credentials(client) -> None:
    headers = _auth_headers(client)
    assert client.post(
        "/api/v1/profile",
        json={
            "headline": "Technical engineer",
            "summary": "Builds production software.",
            "location": "London, UK",
            "career_goal": "Move into applied AI delivery.",
        },
        headers=headers,
    ).status_code == 201

    response = client.post(
        "/api/v1/jobs/external-discovery/search-context",
        json={"query": {"keywords": ["Applied AI Engineer"], "locations": ["London"], "remote_ok": True}},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["search_profile"]["profile_summary"] == "Technical engineer Builds production software."
    assert body["query"]["locations"] == ["London"]
    assert "API_KEY" not in str(body)
    assert "password" not in str(body).casefold()


def test_import_endpoint_has_no_openai_web_search_dependency(client) -> None:
    headers = _auth_headers(client)
    response = client.post("/api/v1/jobs/import-discovered", json=_payload(_job()), headers=headers)
    assert response.status_code == 200

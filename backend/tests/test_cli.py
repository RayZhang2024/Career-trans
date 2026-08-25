import io
import json
import socket
from urllib.error import HTTPError, URLError

import pytest

from app import cli
from app.cli_http import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    ENRICHMENT_HTTP_TIMEOUT_SECONDS,
    RANKING_HTTP_TIMEOUT_SECONDS,
    CareerTransApiClient,
    CareerTransApiError,
    CareerTransConfigurationError,
    CareerTransConnectionError,
    CareerTransTimeoutError,
)


def _draft() -> dict:
    return {
        "id": "draft-1",
        "state": "review_ready",
        "documents": [{"provenance": {"filename": "cv.md"}, "segments": [{"text": "CV"}]}],
        "merged": {
            "employment": [{}],
            "education": [],
            "skills": [{"name": "Python"}],
            "projects": [],
            "achievements": [],
            "evidence": [{}],
        },
    }


class FakeClient:
    def __init__(self, base_url: str, token: str | None) -> None:
        self.base_url = base_url
        self.token = token
        self.calls: list[tuple] = []

    def login(self, email: str, password: str) -> dict:
        self.calls.append(("login", email, password))
        return {"access_token": "test-token"}

    def upload_cv(self, files) -> dict:
        self.calls.append(("upload", files))
        return _draft() | {"state": "uploaded"}

    def interpret_cv(self, draft_id: str) -> dict:
        self.calls.append(("interpret", draft_id))
        return _draft()

    def get_cv(self, draft_id: str) -> dict:
        self.calls.append(("show", draft_id))
        return _draft()

    def edit_cv(self, draft_id: str, corrected: object) -> dict:
        self.calls.append(("edit", draft_id, corrected))
        return _draft()

    def confirm_cv(self, draft_id: str) -> dict:
        self.calls.append(("confirm", draft_id))
        return {"draft_id": draft_id, "confirmed_evidence_count": 1}

    def get_llm_configuration(self) -> dict:
        self.calls.append(("config_show",))
        return {"default_llm_provider": "ollama", "cv_semantic_extraction_model": "local-model", "openai_api_key_configured": False}

    def check_llm_configuration(self) -> dict:
        self.calls.append(("config_check",))
        return {"ready": True, "default_llm_provider": "ollama", "openai_api_key_configured": False}

    def get_candidate_context_summary(self) -> dict:
        self.calls.append(("context_summary",))
        return {
            "ready": True,
            "employment_count": 1,
            "education_count": 1,
            "skill_count": 2,
            "evidence_count": 2,
            "career_strategy_configured": False,
            "job_search_criteria_configured": False,
        }

    def get_profile(self) -> dict:
        self.calls.append(("profile_show",))
        return {
            "career_goal": "Develop technical product expertise.",
            "job_search_criteria": "Prefer permanent engineering roles.",
        }

    def update_profile(self, updates: dict) -> dict:
        self.calls.append(("profile_update", updates))
        return {
            "career_goal": updates.get("career_goal", "Develop technical product expertise."),
            "job_search_criteria": updates.get("job_search_criteria", "Prefer permanent engineering roles."),
        }

    def get_external_discovery_search_context(self, query: dict) -> dict:
        self.calls.append(("external_context", query))
        return {
            "search_profile": {"profile_summary": "Generic profile", "skills": ["Python"]},
            "query": query,
            "runtime_guidance": "factual jobs only",
        }

    def import_discovered_jobs(self, *, runtime: str, jobs: list[dict], query: dict) -> dict:
        self.calls.append(("import_discovered", runtime, jobs, query))
        return {"accepted_jobs": jobs, "rejected_count": 0, "deduplicated_count": 0}

    def rank_jobs_for_current_user(self, jobs: list[dict]) -> dict:
        self.calls.append(("rank_me", jobs))
        return {"discovered_count": len(jobs), "finalist_count": 0, "analysed_count": 0}

    def get_opportunity_inbox(self, limit: int) -> dict:
        self.calls.append(("inbox", limit))
        return {
            "limit": limit,
            "jobs": [
                {
                    "id": "job-1",
                    "job": {
                        "source": "agent_runtime",
                        "source_token": "codex",
                        "title": "Applied AI Engineer",
                        "company": "Example Systems",
                        "location": "London, UK",
                        "url": "https://jobs.example.test/1",
                        "description": "Build systems.",
                    },
                    "state": "new",
                    "first_seen_at": "2026-01-01T00:00:00Z",
                    "last_seen_at": "2026-01-01T00:00:00Z",
                    "provenance": [{"runtime": "codex", "source_ref": "public", "discovered_via": "web", "imported_at": "2026-01-01T00:00:00Z"}],
                }
            ],
        }

    def enrich_imported_jobs(self, limit: int) -> dict:
        self.calls.append(("enrich_imported", limit))
        return {"outcomes": [{"job_id": "job-1", "title": "Applied AI Engineer", "company": "Example Systems", "status": "enriched"}]}


def _fake_client(monkeypatch) -> list[FakeClient]:
    holder: list[FakeClient] = []

    def factory(base_url: str, token: str | None) -> FakeClient:
        client = FakeClient(base_url, token)
        holder.append(client)
        return client

    monkeypatch.setattr(cli, "CareerTransApiClient", factory)
    return holder


def test_help_and_environment_configuration(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CAREER_TRANS_BASE_URL", "http://example.test")
    monkeypatch.setenv("CAREER_TRANS_TOKEN", "environment-token")
    clients = _fake_client(monkeypatch)
    with pytest.raises(SystemExit, match="0"):
        cli.main(["--help"])
    assert "cv" in capsys.readouterr().out
    assert cli.main(["cv", "show", "draft-1"]) == 0
    assert clients[0].base_url == "http://example.test"
    assert clients[0].token == "environment-token"


def test_login_prompts_for_password_and_prints_setup(monkeypatch, capsys) -> None:
    clients = _fake_client(monkeypatch)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "secret-password")
    assert cli.main(["auth", "login", "--email", "user@example.com"]) == 0
    assert clients[0].calls == [("login", "user@example.com", "secret-password")]
    output = capsys.readouterr().out
    assert "test-token" in output
    assert "CAREER_TRANS_TOKEN" in output
    assert "password" not in cli.build_parser().format_help().casefold()


def test_upload_multiple_files_and_missing_file(monkeypatch, tmp_path, capsys) -> None:
    clients = _fake_client(monkeypatch)
    first, second = tmp_path / "first.md", tmp_path / "second.json"
    first.write_text("CV")
    second.write_text("{}")
    assert cli.main(["--token", "token", "cv", "upload", str(first), str(second)]) == 0
    assert [path.name for path in clients[0].calls[0][1]] == ["first.md", "second.json"]
    assert "Next: career-trans cv interpret draft-1" in capsys.readouterr().out
    assert cli.main(["--token", "token", "cv", "upload", str(tmp_path / "missing.md")]) == 2
    assert "Local CV file not found" in capsys.readouterr().err


def test_interpret_show_json_and_human_output(monkeypatch, capsys) -> None:
    clients = _fake_client(monkeypatch)
    assert cli.main(["--token", "token", "cv", "interpret", "draft-1"]) == 0
    assert "employment: 1" in capsys.readouterr().out
    assert cli.main(["--token", "token", "cv", "show", "draft-1"]) == 0
    assert "Files:" in capsys.readouterr().out
    assert cli.main(["--token", "token", "cv", "show", "draft-1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "draft-1"
    assert [[call[0] for call in client.calls] for client in clients] == [["interpret"], ["show"], ["show"]]


def test_edit_confirm_prompt_and_yes(monkeypatch, tmp_path, capsys) -> None:
    clients = _fake_client(monkeypatch)
    corrected = tmp_path / "corrected.json"
    corrected.write_text(json.dumps({"skills": []}))
    assert cli.main(["--token", "token", "cv", "edit", "draft-1", "--file", str(corrected)]) == 0
    assert clients[0].calls[0] == ("edit", "draft-1", {"skills": []})
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    assert cli.main(["--token", "token", "cv", "confirm", "draft-1"]) == 0
    assert "cancelled" in capsys.readouterr().out
    assert len(clients[0].calls) == 1
    assert cli.main(["--token", "token", "cv", "confirm", "draft-1", "--yes"]) == 0
    assert clients[-1].calls == [("confirm", "draft-1")]


def test_invalid_review_json_and_http_error_messages(monkeypatch, tmp_path, capsys) -> None:
    _fake_client(monkeypatch)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    assert cli.main(["--token", "token", "cv", "edit", "draft-1", "--file", str(invalid)]) == 2
    assert "Invalid review JSON" in capsys.readouterr().err

    class UnauthorizedClient(FakeClient):
        def get_cv(self, _draft_id: str) -> dict:
            raise CareerTransApiError(401, "Incorrect token")

    monkeypatch.setattr(cli, "CareerTransApiClient", UnauthorizedClient)
    assert cli.main(["--token", "bad", "cv", "show", "draft-1"]) == 2
    assert "Authentication failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        (404, "CV ingestion draft not found.", "Check the draft ID"),
        (409, "Only a review-ready CV ingestion draft can be edited.", "Request rejected"),
        (422, "Unsupported CV file type.", "Request rejected"),
        (502, "CV semantic extraction failed.", "Semantic provider error"),
    ],
)
def test_cli_maps_draft_state_validation_and_semantic_errors(monkeypatch, capsys, status, detail, expected) -> None:
    class FailingClient(FakeClient):
        def get_cv(self, _draft_id: str) -> dict:
            raise CareerTransApiError(status, detail)

    monkeypatch.setattr(cli, "CareerTransApiClient", FailingClient)
    assert cli.main(["--token", "token", "cv", "show", "draft-1"]) == 2
    assert expected in capsys.readouterr().err


def test_cli_config_show_and_check_are_safe_and_actionable(monkeypatch, capsys) -> None:
    clients = _fake_client(monkeypatch)
    assert cli.main(["--token", "token", "config", "show"]) == 0
    show = capsys.readouterr().out
    assert "default_llm_provider=ollama" in show
    assert "openai_api_key_configured=false" in show
    assert "test-token" not in show
    assert cli.main(["--token", "token", "config", "check"]) == 0
    assert "ready=true" in capsys.readouterr().out
    assert clients[-1].calls == [("config_check",)]


def test_cli_profile_context_summary(monkeypatch, capsys) -> None:
    clients = _fake_client(monkeypatch)
    assert cli.main(["--token", "token", "profile", "context-summary"]) == 0
    output = capsys.readouterr().out
    assert "ready=true" in output
    assert "evidence_count=2" in output
    assert clients[0].calls == [("context_summary",)]


def test_cli_profile_show_and_strategy_updates_are_explicit(monkeypatch, capsys) -> None:
    clients = _fake_client(monkeypatch)
    assert cli.main(["--token", "token", "profile", "show"]) == 0
    shown = capsys.readouterr().out
    assert "career_goal=Develop technical product expertise." in shown
    assert "job_search_criteria=Prefer permanent engineering roles." in shown
    assert clients[0].calls == [("profile_show",)]

    assert cli.main(
        ["--token", "token", "profile", "set-strategy", "--career-goal", "Move toward applied systems."]
    ) == 0
    assert clients[1].calls == [("profile_update", {"career_goal": "Move toward applied systems."})]

    assert cli.main(
        ["--token", "token", "profile", "set-strategy", "--job-search-criteria", "Prefer hybrid roles."]
    ) == 0
    assert clients[2].calls == [("profile_update", {"job_search_criteria": "Prefer hybrid roles."})]

    assert cli.main(
        [
            "--token",
            "token",
            "profile",
            "set-strategy",
            "--career-goal",
            "Build products.",
            "--job-search-criteria",
            "Avoid short contracts.",
        ]
    ) == 0
    assert clients[3].calls == [
        (
            "profile_update",
            {"career_goal": "Build products.", "job_search_criteria": "Avoid short contracts."},
        )
    ]


def test_cli_profile_strategy_requires_an_explicit_field(monkeypatch, capsys) -> None:
    _fake_client(monkeypatch)
    assert cli.main(["--token", "token", "profile", "set-strategy"]) == 2
    assert "Provide --career-goal and/or --job-search-criteria." in capsys.readouterr().err


def test_cli_codex_external_discovery_imports_and_optionally_ranks(monkeypatch, capsys) -> None:
    clients = _fake_client(monkeypatch)

    class FakeRunner:
        def discover(self, context):
            assert context.search_profile.skills == ["Python"]
            from app.schemas.external_discovery import ExternalDiscoveredJob

            return [
                ExternalDiscoveredJob(
                    title="Engineer",
                    company="Example",
                    url="https://jobs.example.test/1",
                    provenance={"source_ref": "public", "discovered_via": "web"},
                )
            ]

    monkeypatch.setattr(cli, "CodexExternalDiscoveryRunner", FakeRunner)
    assert cli.main(
        [
            "--token", "token", "jobs", "discover-external", "--keyword", "Engineer",
            "--location", "London", "--rank",
        ]
    ) == 0
    calls = clients[0].calls
    assert calls[0] == (
        "external_context",
        {"keywords": ["Engineer"], "locations": ["London"], "companies": [], "max_results": 20},
    )
    assert calls[1][0:2] == ("import_discovered", "codex")
    assert calls[2][0] == "rank_me"
    output = capsys.readouterr().out
    assert "Imported 1 jobs" in output
    assert "Ranked 1 jobs" in output


def test_cli_lists_and_reranks_persisted_inbox_without_discovery(monkeypatch, capsys) -> None:
    clients = _fake_client(monkeypatch)
    assert cli.main(["--token", "token", "jobs", "list", "--limit", "5"]) == 0
    listing = capsys.readouterr().out
    assert "Applied AI Engineer | Example Systems | London, UK" in listing
    assert "https://jobs.example.test/1" in listing
    assert clients[0].calls == [("inbox", 5)]

    class RankedInboxClient(FakeClient):
        def rank_jobs_for_current_user(self, jobs: list[dict]) -> dict:
            self.calls.append(("rank_me", jobs))
            return {
                "results": [
                    {
                        "rank": 1,
                        "job": jobs[0],
                        "relevance": {"score": 0.91},
                        "archetype": {"archetype": "ai_solutions_architect"},
                        "recommendation_assessment": {"recommendation": "apply", "fit_score": 82, "career_alignment_score": 79, "reasoning": "Strong supported fit."},
                    }
                ]
            }
    monkeypatch.setattr(cli, "CareerTransApiClient", RankedInboxClient)
    assert cli.main(["--token", "token", "jobs", "rank-imported", "--limit", "5"]) == 0
    output = capsys.readouterr().out
    assert "#1 APPLY" in output
    assert "Relevance: 0.91 | Fit: 82 | Career alignment: 79" in output
    assert "Rationale: Strong supported fit." in output


def test_cli_rank_imported_details_renders_existing_diagnostics_without_extra_calls(monkeypatch, capsys) -> None:
    created: list[FakeClient] = []

    class DetailedInboxClient(FakeClient):
        def __init__(self, base_url: str, token: str | None) -> None:
            super().__init__(base_url, token)
            created.append(self)

        def rank_jobs_for_current_user(self, jobs: list[dict]) -> dict:
            self.calls.append(("rank_me", jobs))
            return {
                "results": [
                    {
                        "rank": 1,
                        "job": jobs[0],
                        "relevance": {"score": 0.96},
                        "archetype": {"archetype": "agentic_automation"},
                        "requirement_matches": [
                            {
                                "requirement_index": 0,
                                "requirement": {"text": "Python", "importance": "essential"},
                                "match_type": "missing",
                                "score": 0.0,
                                "evidence_ids": ["ev-2"],
                                "evidence_refs": [{"source_ref": "employment:1"}],
                                "reasoning": "No supported evidence.",
                            }
                        ],
                        "fit_assessment": {
                            "fit_score": 0.0,
                            "essential_score": 0.0,
                            "desirable_score": None,
                            "strengths": [],
                            "hard_blockers": [0],
                            "gaps": [{"gap_type": "hard_blocker", "severity": "high", "requirement": {"text": "Python"}, "reason": "No supporting evidence."}],
                        },
                        "career_assessment": {"career_alignment_score": 50.0, "confidence": "medium", "strategic_strengths": ["Relevant direction"], "strategic_tradeoffs": ["Limited evidence"], "reasoning": "Neutral alignment."},
                        "recommendation_assessment": {"recommendation": "skip", "fit_score": 0.0, "career_alignment_score": 50.0, "rule_id": "hard_blocker", "key_strengths": ["Relevant direction"], "key_tradeoffs": ["Limited evidence"], "hard_blockers": [0], "reasoning": "Blocked by missing essential evidence."},
                    }
                ]
            }

    monkeypatch.setattr(cli, "CareerTransApiClient", DetailedInboxClient)
    assert cli.main(["--token", "token", "jobs", "rank-imported", "--limit", "5", "--details"]) == 0
    output = capsys.readouterr().out
    assert "Requirements" in output
    assert "[MISSING] Python (essential, score=0.0) — evidence: ev-2; refs: employment:1" in output
    assert "Fit drivers" in output
    assert "total=0.0; essential=0.0" in output
    assert "hard blockers: [0]" in output
    assert "Career drivers" in output
    assert "Recommendation rule" in output
    assert "rule_id: hard_blocker" in output
    assert [call[0] for call in created[0].calls] == ["inbox", "rank_me"]


def test_cli_rank_imported_surfaces_unassessed_incomplete_role(monkeypatch, capsys) -> None:
    created: list[FakeClient] = []

    class IncompleteInboxClient(FakeClient):
        def __init__(self, base_url: str, token: str | None) -> None:
            super().__init__(base_url, token)
            created.append(self)

        def rank_jobs_for_current_user(self, jobs: list[dict]) -> dict:
            self.calls.append(("rank_me", jobs))
            return {
                "results": [],
                "semantic_screening": [
                    {"job": jobs[0], "relevance": {"score": 0.97}, "archetype": {"archetype": "agentic_automation"}}
                ],
                "failures": [
                    {"job": jobs[0], "stage": "insufficient_job_detail", "error": "Job detail is insufficient for deep fit assessment: missing or blank job description."}
                ],
            }

    monkeypatch.setattr(cli, "CareerTransApiClient", IncompleteInboxClient)
    assert cli.main(["--token", "token", "jobs", "rank-imported", "--details"]) == 0
    output = capsys.readouterr().out
    assert "UNASSESSED | Applied AI Engineer | Example Systems | London, UK" in output
    assert "Relevance: 0.97 | Archetype: agentic_automation" in output
    assert "Reason: Job detail is insufficient" in output
    assert "Fit:" not in output
    assert [call[0] for call in created[0].calls] == ["inbox", "rank_me"]


def test_cli_enrich_imported_reports_explicit_outcomes_without_ranking(monkeypatch, capsys) -> None:
    clients = _fake_client(monkeypatch)
    assert cli.main(["--token", "token", "jobs", "enrich-imported", "--limit", "3"]) == 0
    assert "ENRICHED | Applied AI Engineer | Example Systems" in capsys.readouterr().out
    assert clients[0].calls == [("enrich_imported", 3)]


def test_cli_interpret_reports_provider_configuration_without_generic_500(monkeypatch, capsys) -> None:
    class FailingClient(FakeClient):
        def interpret_cv(self, _draft_id: str) -> dict:
            raise CareerTransApiError(503, "OpenAI semantic LLM requires OPENAI_API_KEY.")

    monkeypatch.setattr(cli, "CareerTransApiClient", FailingClient)
    assert cli.main(["--token", "token", "cv", "interpret", "draft-1"]) == 2
    error = capsys.readouterr().err
    assert "LLM provider configuration or availability error" in error
    assert "OPENAI_API_KEY" in error
    assert "HTTP 500" not in error


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def geturl(self) -> str:
        return "http://example.test/api"


def test_http_client_multipart_auth_and_error_mapping(tmp_path) -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return _Response(_draft())

    file = tmp_path / "cv.md"
    file.write_text("CV text")
    client = CareerTransApiClient("http://example.test/", "token", opener=opener)
    client.upload_cv([file])
    request, timeout = requests[0]
    assert timeout == DEFAULT_HTTP_TIMEOUT_SECONDS
    assert request.get_header("Authorization") == "Bearer token"
    assert b'filename="cv.md"' in request.data
    assert b"CV text" in request.data

    def unavailable(_request, *, timeout):
        assert timeout == DEFAULT_HTTP_TIMEOUT_SECONDS
        raise URLError("offline")

    with pytest.raises(CareerTransConnectionError):
        CareerTransApiClient("http://example.test", "token", opener=unavailable).get_cv("draft-1")

    with pytest.raises(CareerTransConfigurationError, match="No API token"):
        CareerTransApiClient("http://example.test").get_cv("draft-1")

    def invalid_token(request, *, timeout):
        assert timeout == DEFAULT_HTTP_TIMEOUT_SECONDS
        raise HTTPError(request.full_url, 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b'{"detail":"Invalid token"}'))

    with pytest.raises(CareerTransApiError, match="Invalid token"):
        CareerTransApiClient("http://example.test", "token", opener=invalid_token).get_cv("draft-1")


def test_http_client_uses_long_finite_timeouts_only_for_ranking_and_enrichment() -> None:
    observed_timeouts = []

    def opener(_request, *, timeout):
        observed_timeouts.append(timeout)
        return _Response({"discovered_count": 0})

    client = CareerTransApiClient("http://example.test", "token", opener=opener)
    client.get_opportunity_inbox(5)
    client.rank_jobs_for_current_user([{"source": "test", "title": "Engineer", "url": "https://jobs.example.test/1"}])
    client.enrich_imported_jobs(10)

    assert observed_timeouts == [
        DEFAULT_HTTP_TIMEOUT_SECONDS,
        RANKING_HTTP_TIMEOUT_SECONDS,
        ENRICHMENT_HTTP_TIMEOUT_SECONDS,
    ]
    assert RANKING_HTTP_TIMEOUT_SECONDS > DEFAULT_HTTP_TIMEOUT_SECONDS
    assert ENRICHMENT_HTTP_TIMEOUT_SECONDS > DEFAULT_HTTP_TIMEOUT_SECONDS


@pytest.mark.parametrize("timeout_error", [TimeoutError("timed out"), socket.timeout("timed out")])
def test_http_client_normalizes_timeout_without_retry(timeout_error) -> None:
    calls = []

    def opener(_request, *, timeout):
        calls.append(timeout)
        raise timeout_error

    client = CareerTransApiClient("http://example.test", "token", opener=opener)
    with pytest.raises(CareerTransTimeoutError, match="timed out after 180 seconds"):
        client.rank_jobs_for_current_user([{"source": "test", "title": "Engineer", "url": "https://jobs.example.test/1"}])
    assert calls == [RANKING_HTTP_TIMEOUT_SECONDS]


def test_http_client_normalizes_url_timeout_without_retry() -> None:
    calls = []

    def opener(_request, *, timeout):
        calls.append(timeout)
        raise URLError(socket.timeout("timed out"))

    client = CareerTransApiClient("http://example.test", "token", opener=opener)
    with pytest.raises(CareerTransTimeoutError, match="timed out after 180 seconds"):
        client.rank_jobs_for_current_user([{"source": "test", "title": "Engineer", "url": "https://jobs.example.test/1"}])
    assert calls == [RANKING_HTTP_TIMEOUT_SECONDS]


@pytest.mark.parametrize("timeout_error", [TimeoutError("timed out"), socket.timeout("timed out")])
def test_enrichment_timeout_is_normalized_without_retry(timeout_error) -> None:
    calls = []

    def opener(_request, *, timeout):
        calls.append(timeout)
        raise timeout_error

    client = CareerTransApiClient("http://example.test", "token", opener=opener)
    with pytest.raises(CareerTransTimeoutError, match="timed out after 180 seconds"):
        client.enrich_imported_jobs(10)
    assert calls == [ENRICHMENT_HTTP_TIMEOUT_SECONDS]


def test_cli_prints_actionable_timeout_message(monkeypatch, capsys) -> None:
    class TimingOutClient(FakeClient):
        def get_opportunity_inbox(self, _limit: int) -> dict:
            raise CareerTransTimeoutError("Career-trans request timed out after 20 seconds. The server may still be processing it; do not retry automatically.")

    monkeypatch.setattr(cli, "CareerTransApiClient", TimingOutClient)
    assert cli.main(["--token", "token", "jobs", "list"]) == 2
    error = capsys.readouterr().err
    assert "timed out after 20 seconds" in error
    assert "do not retry automatically" in error

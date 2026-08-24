import io
import json
from urllib.error import HTTPError, URLError

import pytest

from app import cli
from app.cli_http import CareerTransApiClient, CareerTransApiError, CareerTransConfigurationError, CareerTransConnectionError


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
        return {"ready": True, "employment_count": 1, "education_count": 1, "skill_count": 2, "evidence_count": 2}

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
    assert timeout == 20
    assert request.get_header("Authorization") == "Bearer token"
    assert b'filename="cv.md"' in request.data
    assert b"CV text" in request.data

    def unavailable(_request, *, timeout):
        assert timeout == 20
        raise URLError("offline")

    with pytest.raises(CareerTransConnectionError):
        CareerTransApiClient("http://example.test", "token", opener=unavailable).get_cv("draft-1")

    with pytest.raises(CareerTransConfigurationError, match="No API token"):
        CareerTransApiClient("http://example.test").get_cv("draft-1")

    def invalid_token(request, *, timeout):
        assert timeout == 20
        raise HTTPError(request.full_url, 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b'{"detail":"Invalid token"}'))

    with pytest.raises(CareerTransApiError, match="Invalid token"):
        CareerTransApiClient("http://example.test", "token", opener=invalid_token).get_cv("draft-1")

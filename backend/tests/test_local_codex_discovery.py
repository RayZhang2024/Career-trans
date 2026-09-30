import importlib.util
import json
import sqlite3
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from app.core.config import Settings
from app.api.routes import job_discovery_settings as settings_route
from app.main import app
from app.models.user import User
from app.models.user_job_discovery_settings import UserJobDiscoverySettings
from app.models.discovered_job import DiscoveredJob
from app.models.discovery_schedule import DiscoverySchedule, ScheduledDiscoveryExecution
from app.providers.local_codex import LocalCodexSearchError, LocalCodexWebSearchProvider
from app.schemas.job_discovery_settings import JobDiscoverySettingsReplace
from app.schemas.job_discovery_settings import (
    LocalCodexAuthenticationStatus,
    LocalCodexCapabilityStatus,
    LocalCodexDiscoveryStatus,
    LocalCodexScheduledStatus,
    LocalCodexStatusRead,
    LocalCodexTestRead,
)
from app.services.codex_runtime import CodexInvocation, CodexRuntimeAdapter, codex_subprocess_environment
from app.services.job_discovery_settings_service import (
    JobDiscoveryProviderNotReady,
    JobDiscoverySettingsService,
)


def test_local_codex_config_defaults_and_validates_model() -> None:
    defaults = Settings(_env_file=None)
    assert defaults.local_codex_discovery_enabled is False
    assert defaults.local_codex_search_model == "gpt-5.6-luna"
    assert defaults.local_codex_scheduled_discovery_capability == "unverified"
    assert Settings(_env_file=None, local_codex_discovery_enabled=True, local_codex_search_model="gpt-5.6-sol").local_codex_search_model == "gpt-5.6-sol"
    with pytest.raises(ValueError, match="LOCAL_CODEX_SEARCH_MODEL"):
        Settings(_env_file=None, local_codex_search_model="bad model")


@pytest.mark.parametrize(
    ("auth_output", "expected"),
    [("Not logged in", "signed_out"), ("logged in", "signed_in"), ("", "unknown")],
)
def test_runtime_probe_authentication_is_reported_without_raw_output(auth_output, expected) -> None:
    def runner(command, **kwargs):
        output = auth_output if command[1:] == ["login", "status"] else ""
        return CompletedProcess(command, 0, output, "")

    status = CodexRuntimeAdapter(
        settings=Settings(_env_file=None, local_codex_discovery_enabled=True),
        runner=runner,
        executable_lookup=lambda _: "codex",
    ).probe()
    assert status.authentication_status == expected
    assert auth_output not in status.model_dump_json() if auth_output else True


def test_subprocess_environment_keeps_codex_home_and_strips_application_secrets(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", "C:/synthetic/codex-home")
    monkeypatch.setenv("CODEX_AUTH_TOKEN", "synthetic-codex-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///private.db")
    monkeypatch.setenv("CAREER_TRANS_PRIVATE_SETTING", "synthetic-app-secret")
    environment = codex_subprocess_environment()
    assert environment["CODEX_HOME"] == "C:/synthetic/codex-home"
    for secret_name in ("CODEX_AUTH_TOKEN", "OPENAI_API_KEY", "DATABASE_URL", "CAREER_TRANS_PRIVATE_SETTING"):
        assert secret_name not in environment


def test_local_codex_provider_validates_deduplicates_and_limits_results() -> None:
    class Runtime:
        def invoke_structured(self, **kwargs):
            self.kwargs = kwargs
            return CodexInvocation(0, b"", b"", '{"results":['
                '{"title":"First","url":"https://jobs.example/a","snippet":"Role"},'
                '{"title":"Duplicate","url":"https://jobs.example/a","snippet":null},'
                '{"title":"Second","url":"http://other.example/job","snippet":""}]}')

    runtime = Runtime()
    provider = LocalCodexWebSearchProvider(runtime=runtime, model="gpt-5.6-luna")
    found = provider.search("  engineer roles  ", 2)
    assert len(found) == 2
    assert found[0].rank == 1 and found[0].domain == "jobs.example"
    assert found[1].rank == 2 and found[1].domain == "other.example"
    assert runtime.kwargs["model"] == "gpt-5.6-luna"
    assert "engineer roles" in runtime.kwargs["prompt"]
    assert "candidate" not in runtime.kwargs["prompt"].casefold()


@pytest.mark.parametrize("url", ["file:///private", "https://user:pass@example.com/job", "https://", "https://exa mple.com/job"])
def test_local_codex_provider_rejects_unsafe_or_invalid_urls(url: str) -> None:
    class Runtime:
        def invoke_structured(self, **kwargs):
            return CodexInvocation(0, b"", b"", json.dumps({"results": [{"title": "Role", "url": url, "snippet": None}]}))

    with pytest.raises(LocalCodexSearchError, match="invalid structured"):
        LocalCodexWebSearchProvider(runtime=Runtime(), model="gpt-5.6-luna").search("engineering", 1)


def test_local_codex_provider_hides_raw_command_diagnostics() -> None:
    class Runtime:
        def invoke_structured(self, **kwargs):
            return CodexInvocation(9, b"private stdout", b"private stderr", None)

    with pytest.raises(LocalCodexSearchError) as error:
        LocalCodexWebSearchProvider(runtime=Runtime(), model="gpt-5.6-luna").search("engineering", 1)
    assert "private" not in str(error.value)


def test_runtime_probe_uses_supported_surfaces_and_reports_auth_and_capabilities() -> None:
    calls = []

    def runner(command, **kwargs):
        calls.append(command[1:])
        output = {
            ("--version",): "codex-cli 0.155.0-alpha.9.2",
            ("--help",): "--search Enable live web search via web_search",
            ("exec", "--help"): "--output-schema --output-last-message prompt from stdin",
            ("login", "status"): "You are logged in",
        }.get(tuple(command[1:]), "")
        return CompletedProcess(command, 0, output, "")

    status = CodexRuntimeAdapter(
        settings=Settings(_env_file=None, local_codex_discovery_enabled=True),
        runner=runner,
        executable_lookup=lambda _: "codex",
    ).probe()
    assert status.version == "0.155.0-alpha.9.2"
    assert status.authentication_status == "signed_in"
    assert status.manual_discovery_status == "ready"
    assert status.scheduled_discovery_status == "unverified"
    assert ["login", "status"] in calls


def test_structured_invocation_keeps_prompt_off_argv_and_bounds_process_context(monkeypatch) -> None:
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[command.index("--output-last-message") + 1]).write_text('{"ok":true}', encoding="utf-8")
        return CompletedProcess(command, 0, b"", b"")

    result = CodexRuntimeAdapter(runner=runner, executable_lookup=lambda _: "codex").invoke_structured(
        prompt="Synthetic prompt only", schema={"type": "object"}, model="gpt-5.6-luna",
        schema_filename="schema.json", output_filename="output.json", timeout_seconds=20,
    )
    command, kwargs = calls[0]
    assert result.output_text == '{"ok":true}'
    assert "Synthetic prompt only" not in command
    assert kwargs["input"] == b"Synthetic prompt only"
    assert kwargs["shell"] is False and kwargs["timeout"] == 20
    assert kwargs["cwd"] != str(Path.cwd())
    assert "OPENAI_API_KEY" not in kwargs["env"]


def test_deployment_disabled_local_codex_does_not_run_subprocess(db_session, monkeypatch) -> None:
    db_session.add(User(id="disabled", email="disabled@example.com", password_hash="test"))
    db_session.add(UserJobDiscoverySettings(user_id="disabled", provider_override="local_codex", revision=1))
    db_session.commit()
    monkeypatch.setattr(CodexRuntimeAdapter, "probe", lambda self: pytest.fail("probe must not run when deployment opts out"))
    service = JobDiscoverySettingsService(db_session, settings=Settings(_env_file=None))
    with pytest.raises(JobDiscoveryProviderNotReady, match="disabled by this deployment"):
        service.resolve_provider("disabled")


def test_unverified_due_runner_fails_before_runtime_probe(db_session, monkeypatch) -> None:
    db_session.add(User(id="scheduled", email="scheduled@example.com", password_hash="test"))
    db_session.add(UserJobDiscoverySettings(user_id="scheduled", provider_override="local_codex", revision=1))
    db_session.commit()
    settings = Settings(_env_file=None, local_codex_discovery_enabled=True)
    monkeypatch.setattr(CodexRuntimeAdapter, "probe", lambda self: pytest.fail("unverified due runner must fail before probing"))
    with pytest.raises(JobDiscoveryProviderNotReady, match="not verified"):
        JobDiscoverySettingsService(db_session, settings=settings).resolve_provider("scheduled", scheduled_due_runner=True)


def test_local_codex_provider_override_persists_in_fresh_schema(db_session) -> None:
    user = User(id="provider", email="provider@example.com", password_hash="test")
    db_session.add(user)
    db_session.commit()
    service = JobDiscoverySettingsService(db_session, settings=Settings(_env_file=None))
    result = service.replace("provider", JobDiscoverySettingsReplace(expected_revision=0, provider_override="local_codex"))
    assert result.provider_override == "local_codex"
    assert result.effective_provider == "local_codex"


def test_existing_sqlite_settings_migration_preserves_rows_and_foreign_keys() -> None:
    migration_path = Path(__file__).parents[1] / "migrations" / "20261001_local_codex_provider_sqlite.py"
    spec = importlib.util.spec_from_file_location("local_codex_sqlite_migration", migration_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE users (id VARCHAR(36) PRIMARY KEY);
        CREATE TABLE user_job_discovery_settings (
          user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          provider_override VARCHAR(24) NULL CHECK (provider_override IS NULL OR provider_override IN ('tavily','openai','disabled')),
          revision INTEGER NOT NULL CHECK (revision >= 1),
          created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
        );
        INSERT INTO users VALUES ('u1'), ('u2'), ('u3');
        INSERT INTO user_job_discovery_settings VALUES ('u1','tavily',7,'created','updated');
        INSERT INTO user_job_discovery_settings VALUES ('u2','openai',8,'created-2','updated-2');
        INSERT INTO user_job_discovery_settings VALUES ('u3','disabled',9,'created-3','updated-3');
    """)
    module.upgrade(connection)
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("SELECT user_id, provider_override, revision, created_at, updated_at FROM user_job_discovery_settings").fetchone() == ("u1", "tavily", 7, "created", "updated")
    assert connection.execute("SELECT provider_override, revision FROM user_job_discovery_settings WHERE user_id='u2'").fetchone() == ("openai", 8)
    assert connection.execute("SELECT provider_override, revision FROM user_job_discovery_settings WHERE user_id='u3'").fetchone() == ("disabled", 9)
    connection.execute("UPDATE user_job_discovery_settings SET provider_override='local_codex' WHERE user_id='u1'")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO user_job_discovery_settings VALUES ('missing','openai',1,'a','b')")
    connection.execute("DELETE FROM users WHERE id='u1'")
    assert connection.execute("SELECT COUNT(*) FROM user_job_discovery_settings").fetchone()[0] == 2
    assert connection.execute("SELECT 1 FROM user_job_discovery_settings WHERE user_id='u1'").fetchone() is None
    connection.close()


def test_local_codex_status_and_test_routes_are_authenticated_and_non_mutating(client, db_session, monkeypatch) -> None:
    credentials = {"email": "local-codex-api@example.com", "password": "Strong-password-123"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    ready = LocalCodexStatusRead(
        enabled_by_deployment=True, cli_installed=True, version="codex-cli 1.2",
        authentication_status=LocalCodexAuthenticationStatus.SIGNED_IN,
        structured_invocation_status=LocalCodexCapabilityStatus.AVAILABLE,
        search_capability_status=LocalCodexCapabilityStatus.AVAILABLE,
        manual_discovery_status=LocalCodexDiscoveryStatus.READY,
        scheduled_discovery_status=LocalCodexScheduledStatus.UNVERIFIED,
        message="Ready", setup_guidance="Test live search.",
    )
    service = JobDiscoverySettingsService(db_session, settings=Settings(_env_file=None))
    monkeypatch.setattr(service, "local_codex_status", lambda: ready)
    monkeypatch.setattr(service, "test_local_codex", lambda: LocalCodexTestRead(success=True, result_count=1, message="Live search succeeded."))
    app.dependency_overrides[settings_route._service] = lambda: service
    try:
        assert client.get("/api/v1/job-discovery/local-codex/status").status_code == 401
        status = client.get("/api/v1/job-discovery/local-codex/status", headers=headers)
        assert status.status_code == 200 and status.json()["scheduled_discovery_status"] == "unverified"
        tested = client.post("/api/v1/job-discovery/local-codex/test", headers=headers, json={})
        assert tested.status_code == 200 and tested.json()["result_count"] == 1
        assert db_session.query(UserJobDiscoverySettings).count() == 0
        assert db_session.query(DiscoveredJob).count() == 0
        assert db_session.query(DiscoverySchedule).count() == 0
        assert db_session.query(ScheduledDiscoveryExecution).count() == 0
        assert "codex" not in status.json().get("account", "").casefold()
    finally:
        app.dependency_overrides.pop(settings_route._service, None)

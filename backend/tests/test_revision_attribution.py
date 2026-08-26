import os
import subprocess

import pytest
from langsmith.env import get_langchain_env_var_metadata

from app.core.config import Settings, configure_langsmith_environment
from app.core.revision import resolve_application_revision


@pytest.fixture(autouse=True)
def clear_revision_caches() -> None:
    resolve_application_revision.cache_clear()
    get_langchain_env_var_metadata.cache_clear()
    yield
    resolve_application_revision.cache_clear()
    get_langchain_env_var_metadata.cache_clear()


def test_explicit_deployment_revision_takes_precedence(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.revision.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("Git must not run for a deployment revision."),
    )

    assert resolve_application_revision("deployment-sha") == "deployment-sha"


def test_git_head_is_used_when_no_deployment_revision(monkeypatch) -> None:
    calls = 0

    def git_head(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess([], 0, stdout="git-sha\n", stderr="")

    monkeypatch.setattr("app.core.revision.subprocess.run", git_head)

    assert resolve_application_revision() == "git-sha"
    assert resolve_application_revision() == "git-sha"
    assert calls == 1


def test_missing_git_fails_safely(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("app.core.revision.subprocess.run", unavailable)

    assert resolve_application_revision() is None


def test_resolved_revision_reaches_langsmith_runtime_metadata(monkeypatch) -> None:
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    settings = Settings(application_revision="deployed-revision")
    revision = resolve_application_revision(settings.application_revision)

    configure_langsmith_environment(settings, revision=revision)

    # This is the cached LangSmith runtime-metadata function used by its
    # provider-run creation path; it contains no prompts or user data.
    get_langchain_env_var_metadata.cache_clear()
    assert os.environ["LANGCHAIN_REVISION_ID"] == "deployed-revision"
    assert get_langchain_env_var_metadata()["revision_id"] == "deployed-revision"

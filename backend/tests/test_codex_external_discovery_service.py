import json
import subprocess
from pathlib import Path

import pytest

from app.schemas.external_discovery import ExternalDiscoverySearchContextResponse
from app.services.codex_external_discovery_service import (
    CodexExternalDiscoveryError,
    CodexExternalDiscoveryRunner,
)


def _context() -> ExternalDiscoverySearchContextResponse:
    return ExternalDiscoverySearchContextResponse.model_validate(
        {
            "search_profile": {
                "profile_summary": 'Software & AI | Muon (D) "C:\\work\\100%^"',
                "skills": ["Python"],
            },
            "query": {"keywords": ["Engineer"], "locations": ["London"], "max_results": 2},
            "runtime_guidance": "factual jobs only",
        }
    )


def test_codex_runner_tolerates_non_utf8_console_diagnostics_and_validates_output_file() -> None:
    commands = []

    def runner(command, **kwargs):
        commands.append((command, kwargs))
        Path(command[4]).write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "title": "Engineer",
                            "company": "Example",
                            "url": "https://jobs.example.test/1",
                            "provenance": {"source_ref": "public", "discovered_via": "web"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, b"\xb2", b"\xb2")

    jobs = CodexExternalDiscoveryRunner(
        runner=runner,
        executable_lookup=lambda _: "codex",
    ).discover(_context())

    assert jobs[0].title == "Engineer"
    command, kwargs = commands[0]
    assert command[0:4] == ["codex", "--search", "exec", "--output-last-message"]
    assert command[-1] == "-"
    assert not any("Software & AI" in argument or "Muon" in argument for argument in command)
    assert b"Software & AI | Muon (D)" in kwargs["input"]
    assert b"up to 2" in kwargs["input"]
    assert b"search seeds, not literal title requirements" in kwargs["input"]
    assert kwargs["timeout"] == 240
    assert kwargs["check"] is False
    assert kwargs["text"] is False
    assert kwargs["shell"] is False


def test_codex_runner_fails_safely_for_unavailable_failed_timed_out_or_invalid_output() -> None:
    with pytest.raises(CodexExternalDiscoveryError, match="unavailable"):
        CodexExternalDiscoveryRunner(executable_lookup=lambda _: None).discover(_context())

    def failure(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, b"", b"private stderr")

    with pytest.raises(CodexExternalDiscoveryError, match="exit code 1.*private stderr"):
        CodexExternalDiscoveryRunner(runner=failure, executable_lookup=lambda _: "codex").discover(_context())

    def timeout(_command, **_kwargs):
        raise subprocess.TimeoutExpired("codex", 1)

    with pytest.raises(CodexExternalDiscoveryError, match="timed out"):
        CodexExternalDiscoveryRunner(runner=timeout, executable_lookup=lambda _: "codex").discover(_context())

    def invalid(command, **_kwargs):
        Path(command[4]).write_text("not json", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(CodexExternalDiscoveryError, match="invalid discovery JSON"):
        CodexExternalDiscoveryRunner(runner=invalid, executable_lookup=lambda _: "codex").discover(_context())


def test_codex_runner_surfaces_bounded_sanitized_non_utf8_failure_diagnostics() -> None:
    secret = "super-secret-value"
    openai_secret = "openai-secret-value"
    github_secret = "github-secret-value"

    def failure(command, **_kwargs):
        stderr = (b"discarded-" * 500) + b"failure \xb2 " + (
            f"API_KEY={secret} OPENAI_API_KEY={openai_secret} "
            f"GITHUB_TOKEN={github_secret} Context: private candidate data"
        ).encode()
        return subprocess.CompletedProcess(command, 17, b"", stderr)

    with pytest.raises(CodexExternalDiscoveryError) as error:
        CodexExternalDiscoveryRunner(runner=failure, executable_lookup=lambda _: "codex").discover(_context())

    message = str(error.value)
    assert "exit code 17" in message
    assert "\ufffd" in message
    assert secret not in message
    assert openai_secret not in message
    assert github_secret not in message
    assert "private candidate data" not in message
    assert "[REDACTED]" in message
    assert len(message) <= 1_100


def test_codex_runner_uses_stdout_only_when_stderr_is_empty_and_handles_empty_diagnostics() -> None:
    def stdout_failure(command, **_kwargs):
        return subprocess.CompletedProcess(command, 2, b"short fallback diagnostic", b"")

    with pytest.raises(CodexExternalDiscoveryError, match="exit code 2.*short fallback diagnostic"):
        CodexExternalDiscoveryRunner(runner=stdout_failure, executable_lookup=lambda _: "codex").discover(_context())

    def empty_failure(command, **_kwargs):
        return subprocess.CompletedProcess(command, 3, b"", b"")

    with pytest.raises(CodexExternalDiscoveryError, match=r"exit code 3\)\. Retry or run Codex directly"):
        CodexExternalDiscoveryRunner(runner=empty_failure, executable_lookup=lambda _: "codex").discover(_context())

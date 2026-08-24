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
            "search_profile": {"profile_summary": "Generic engineer", "skills": ["Python"]},
            "query": {"keywords": ["Engineer"], "locations": ["London"], "max_results": 2},
            "runtime_guidance": "factual jobs only",
        }
    )


def test_codex_runner_uses_bounded_context_and_validates_json_output() -> None:
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
        return subprocess.CompletedProcess(command, 0, "", "")

    jobs = CodexExternalDiscoveryRunner(
        runner=runner,
        executable_lookup=lambda _: "codex",
    ).discover(_context())

    assert jobs[0].title == "Engineer"
    command, kwargs = commands[0]
    assert command[0:4] == ["codex", "--search", "exec", "--output-last-message"]
    assert "Generic engineer" in command[-1]
    assert "up to 2" in command[-1]
    assert kwargs["timeout"] == 240
    assert kwargs["check"] is False


def test_codex_runner_fails_safely_for_unavailable_failed_timed_out_or_invalid_output() -> None:
    with pytest.raises(CodexExternalDiscoveryError, match="unavailable"):
        CodexExternalDiscoveryRunner(executable_lookup=lambda _: None).discover(_context())

    def failure(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "private stderr")

    with pytest.raises(CodexExternalDiscoveryError, match="failed"):
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

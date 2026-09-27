import json
import subprocess
from pathlib import Path

import pytest

from app.schemas.external_discovery import CodexExternalDiscoveryOutput, ExternalDiscoverySearchContextResponse
from app.services.codex_external_discovery_service import (
    CodexExternalDiscoveryError,
    CodexExternalDiscoveryRunner,
)


def _context() -> ExternalDiscoverySearchContextResponse:
    return ExternalDiscoverySearchContextResponse.model_validate(
        {
            "search_profile": {
                "profile_summary": 'Software & AI | Muon (D) "C:\\work\\100%^" PROFILE_SUMMARY_SENTINEL_AX91',
                "skills": ["Python", "SKILL_SENTINEL_BK72"],
            },
            "query": {
                "keywords": ["Engineer", "ARBITRARY_CANDIDATE_TEXT_QP64"],
                "locations": ["London", "LOCATION_SENTINEL_CM53"],
                "max_results": 2,
            },
            "runtime_guidance": "factual jobs only",
        }
    )


def _argument_path(command: list[str], flag: str) -> Path:
    return Path(command[command.index(flag) + 1])


def _valid_output() -> dict[str, object]:
    return {
        "jobs": [
            {
                "title": "Engineer",
                "company": "Example",
                "url": "https://jobs.example.test/1",
                "provenance": {"source_ref": "public", "discovered_via": "web"},
            }
        ]
    }


def test_codex_runner_tolerates_non_utf8_console_diagnostics_and_validates_output_file() -> None:
    commands = []

    def runner(command, **kwargs):
        commands.append((command, kwargs))
        schema_path = _argument_path(command, "--output-schema")
        assert json.loads(schema_path.read_text(encoding="utf-8")) == CodexExternalDiscoveryRunner._output_schema()
        _argument_path(command, "--output-last-message").write_text(
            json.dumps(_valid_output()), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, b"\xb2", b"\xb2")

    jobs = CodexExternalDiscoveryRunner(
        runner=runner,
        executable_lookup=lambda _: "codex",
    ).discover(_context())

    assert jobs[0].title == "Engineer"
    command, kwargs = commands[0]
    assert command[0:4] == ["codex", "--search", "exec", "--output-schema"]
    assert "--output-last-message" in command
    schema_path = _argument_path(command, "--output-schema")
    output_path = _argument_path(command, "--output-last-message")
    assert schema_path.name == "external-discovery-output-schema.json"
    assert output_path.name == "discovered-jobs.json"
    assert not schema_path.exists()
    assert not output_path.exists()
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

    with pytest.raises(CodexExternalDiscoveryError, match=r"exit code 1\)\. Retry or run Codex directly"):
        CodexExternalDiscoveryRunner(runner=failure, executable_lookup=lambda _: "codex").discover(_context())

    def timeout(_command, **_kwargs):
        raise subprocess.TimeoutExpired("codex", 1)

    with pytest.raises(CodexExternalDiscoveryError, match="timed out"):
        CodexExternalDiscoveryRunner(runner=timeout, executable_lookup=lambda _: "codex").discover(_context())

    def invalid(command, **_kwargs):
        _argument_path(command, "--output-last-message").write_text("not json", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(CodexExternalDiscoveryError, match="invalid discovery JSON"):
        CodexExternalDiscoveryRunner(runner=invalid, executable_lookup=lambda _: "codex").discover(_context())


@pytest.mark.parametrize(
    "output",
    [
        {"jobs": [{**_valid_output()["jobs"][0], "url": "not-a-url"}]},
        {"jobs": [{**_valid_output()["jobs"][0], "unexpected": "field"}]},
        {"jobs": [{"company": "Example", "url": "https://jobs.example.test/1"}]},
        {"jobs": [{"title": "Engineer", "company": "Example"}]},
        {"jobs": [{**_valid_output()["jobs"][0], "title": "x" * 501}]},
        {"jobs": []},
        {"jobs": [_valid_output()["jobs"][0]] * 26},
    ],
    ids=[
        "invalid_url",
        "extra_field",
        "missing_required_title",
        "missing_required_url",
        "overlong_title",
        "zero_jobs",
        "over_limit",
    ],
)
def test_codex_runner_fails_closed_for_contract_invalid_output(output: dict[str, object]) -> None:
    def runner(command, **_kwargs):
        _argument_path(command, "--output-last-message").write_text(json.dumps(output), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    with pytest.raises(CodexExternalDiscoveryError, match="invalid discovery JSON"):
        CodexExternalDiscoveryRunner(runner=runner, executable_lookup=lambda _: "codex").discover(_context())


def test_codex_runner_emits_sdk_derived_strict_schema_for_the_canonical_contract() -> None:
    schema = CodexExternalDiscoveryRunner._output_schema()
    serialized = json.dumps(schema)

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
    assert schema["properties"]["jobs"]["minItems"] == 1
    assert schema["properties"]["jobs"]["maxItems"] == 25
    assert '"default"' not in serialized
    assert '"minLength"' not in serialized
    assert '"maxLength"' not in serialized

    job = schema["$defs"]["ExternalDiscoveredJob"]
    provenance = schema["$defs"]["ExternalDiscoveryProvenance"]
    for object_schema in (job, provenance):
        assert object_schema["additionalProperties"] is False
        assert object_schema["required"] == list(object_schema["properties"])

    company_types = job["properties"]["company"]["anyOf"]
    source_ref_types = provenance["properties"]["source_ref"]["anyOf"]
    assert {item["type"] for item in company_types} == {"string", "null"}
    assert {item["type"] for item in source_ref_types} == {"string", "null"}
    assert job["properties"]["posted_at"]["anyOf"][0]["format"] == "date-time"
    assert schema != CodexExternalDiscoveryOutput.model_json_schema()


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
    assert secret not in message
    assert openai_secret not in message
    assert github_secret not in message
    assert "private candidate data" not in message
    assert "[REDACTED]" in message
    assert len(message) <= 1_100


def test_codex_runner_uses_stdout_only_when_stderr_is_empty_and_handles_empty_diagnostics() -> None:
    def stdout_failure(command, **_kwargs):
        return subprocess.CompletedProcess(command, 2, b"short fallback diagnostic", b"")

    with pytest.raises(CodexExternalDiscoveryError, match=r"exit code 2\)\. Retry or run Codex directly"):
        CodexExternalDiscoveryRunner(runner=stdout_failure, executable_lookup=lambda _: "codex").discover(_context())

    def empty_failure(command, **_kwargs):
        return subprocess.CompletedProcess(command, 3, b"", b"")

    with pytest.raises(CodexExternalDiscoveryError, match=r"exit code 3\)\. Retry or run Codex directly"):
        CodexExternalDiscoveryRunner(runner=empty_failure, executable_lookup=lambda _: "codex").discover(_context())


def _candidate_values() -> tuple[str, ...]:
    return (
        'Software & AI | Muon (D) "C:\\work\\100%^"',
        "PROFILE_SUMMARY_SENTINEL_AX91",
        "SKILL_SENTINEL_BK72",
        "LOCATION_SENTINEL_CM53",
        "ARBITRARY_CANDIDATE_TEXT_QP64",
    )


def _failed_error(stderr: bytes | str, stdout: bytes | str = b"", exit_code: int = 19) -> str:
    def failure(command, **_kwargs):
        return subprocess.CompletedProcess(command, exit_code, stdout, stderr)

    with pytest.raises(CodexExternalDiscoveryError) as error:
        CodexExternalDiscoveryRunner(runner=failure, executable_lookup=lambda _: "codex").discover(_context())
    return str(error.value)


def _serialized_candidate_context() -> str:
    context = _context()
    return json.dumps({
        "search_profile": context.search_profile.model_dump(mode="json"),
        "query": context.query.model_dump(mode="json"),
    })


@pytest.mark.parametrize(
    "diagnostic",
    [
        'Error: failed. profile_summary="Software & AI | Muon (D) \\"C:\\\\work\\\\100%^\\""',
        'skills=["Python","SKILL_SENTINEL_BK72"] locations=["London","LOCATION_SENTINEL_CM53"] '
        "ARBITRARY_CANDIDATE_TEXT_QP64",
        # After tail truncation, this starts inside the serialized profile_summary value.
        ("x" * 2_048) + _serialized_candidate_context()[
            _serialized_candidate_context().index("PROFILE_SUMMARY_SENTINEL_AX91") + 8:
        ],
        # Partial JSON and escaped candidate strings are still untrusted text.
        '...profile_summary":"prefix SKILL_SENTINEL_BK72 and \\"ARBITRARY_CANDIDATE_TEXT_QP64',
        'Context: {"profile_summary":"Software & AI | Muon (D) \\"C:\\\\work\\\\100%^\\"",'
        '"skills":["SKILL_SENTINEL_BK72"],"locations":["LOCATION_SENTINEL_CM53"]}',
        'Error: model gpt-local unavailable\\nContext: ' + _serialized_candidate_context(),
    ],
    ids=["profile-summary", "skills-locations-arbitrary", "tail-mid-context", "partial-json", "escaped-values", "mixed-model-error"],
)
def test_failed_diagnostics_never_surface_candidate_context(diagnostic: str) -> None:
    message = _failed_error(diagnostic)
    assert "exit code 19" in message
    for candidate_value in _candidate_values():
        assert candidate_value not in message


def test_unknown_or_secret_bearing_candidate_diagnostics_fail_closed():
    secret = "sk_test_super_secret_9281"
    diagnostic = (
        f"Authorization: Bearer {secret} profile_summary={_context().search_profile.profile_summary}; "
        "skill=SKILL_SENTINEL_BK72 location=LOCATION_SENTINEL_CM53"
    )
    message = _failed_error(diagnostic)
    assert "[REDACTED]" in message
    assert secret not in message
    for candidate_value in _candidate_values():
        assert candidate_value not in message


def test_stdout_fallback_candidate_context_is_fail_closed():
    message = _failed_error(b"", stdout="stdout tail with ARBITRARY_CANDIDATE_TEXT_QP64 SKILL_SENTINEL_BK72")
    assert "Retry or run Codex directly for diagnostics" in message
    assert "ARBITRARY_CANDIDATE_TEXT_QP64" not in message
    assert "SKILL_SENTINEL_BK72" not in message


def test_non_utf8_partial_context_error_is_robust_and_never_exposed():
    diagnostic = b"\xff\xfe" + b"x" * 2_100 + b"LOCATION_SENTINEL_CM53\xff"
    message = _failed_error(diagnostic)
    assert "Codex discovery failed" in message
    assert "LOCATION_SENTINEL_CM53" not in message


def test_clearly_classified_safe_model_diagnostic_remains_actionable():
    message = _failed_error(
        "Error: model gpt-local unavailable; context=SKILL_SENTINEL_BK72 LOCATION_SENTINEL_CM53"
    )
    assert "configured model" in message
    assert "Check the local Codex model setting" in message
    assert "SKILL_SENTINEL_BK72" not in message
    assert "LOCATION_SENTINEL_CM53" not in message

"""Bounded local Codex CLI adapter for external, non-authoritative job discovery."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from app.schemas.external_discovery import (
    CodexExternalDiscoveryOutput,
    ExternalDiscoveredJob,
    ExternalDiscoverySearchContextResponse,
)
from app.core.config import Settings, get_settings
from app.providers.openai_structured_output import (
    StrictStructuredOutputSchemaError,
    strict_schema_from_pydantic_model,
)


_DIAGNOSTIC_TAIL_BYTES = 2_048
_CAREER_TRANS_ENV_NAMES = frozenset({"database_url"})
_CREDENTIAL_ENV_MARKERS = (
    "api_key",
    "api-key",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
    "auth",
)
_SECRET_PATTERNS = (
    # Covers standalone keys and common prefixed environment names such as
    # OPENAI_API_KEY and GITHUB_TOKEN.
    re.compile(r"(?i)\b(?:[a-z][a-z0-9_-]*[_-])?(?:api[_-]?key|token|authorization|password|secret)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~-]+"),
    re.compile(r"\b(?:sk|lsv2|gho)_[A-Za-z0-9_-]+"),
)
_SAFE_DIAGNOSTIC_CLASSIFIERS = (
    (
        re.compile(
            r"(?im)^\s*(?:codex(?:\s+exec)?\s*:\s*)?(?:error:\s*)?"
            r"(?:model|deployment)\b.{0,100}\b(?:not found|unavailable|not available|not supported)\b[^\r\n]*$"
        ),
        "Codex could not use the configured model. Check the local Codex model setting and retry.",
    ),
    (
        re.compile(r"(?im)^\s*(?:codex(?:\s+exec)?\s*:\s*)?(?:error:\s*)?(?:rate limit|too many requests|http 429|status 429)\b[^\r\n]*$"),
        "Codex reported a rate limit. Wait briefly, then retry.",
    ),
    (
        re.compile(r"(?im)^\s*(?:codex(?:\s+exec)?\s*:\s*)?(?:error:\s*)?(?:connection refused|could not connect|connection timed out|network unavailable)\b[^\r\n]*$"),
        "Codex could not connect to its service. Check connectivity and retry.",
    ),
    (
        re.compile(r"(?im)^\s*(?:codex(?:\s+exec)?\s*:\s*)?(?:error:\s*)?(?:unauthorized|authentication failed|not authenticated|http 401|status 401)\b[^\r\n]*$"),
        "Codex reported an authentication issue. Check local Codex authentication and retry.",
    ),
)


class CodexExternalDiscoveryError(RuntimeError):
    """Safe, actionable failure from the local external discovery runtime."""


class CodexExternalDiscoveryRunner:
    """Run a user-authenticated local Codex CLI without sharing Career-trans credentials."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        executable_lookup: Callable[[str], str | None] = shutil.which,
        timeout_seconds: int = 240,
        model: str | None = None,
    ) -> None:
        self._runner = runner
        self._executable_lookup = executable_lookup
        self._timeout_seconds = timeout_seconds
        self._model = (
            get_settings().codex_external_discovery_model
            if model is None
            else Settings(_env_file=None, codex_external_discovery_model=model).codex_external_discovery_model
        )

    def discover(self, context: ExternalDiscoverySearchContextResponse) -> list[ExternalDiscoveredJob]:
        executable = self._executable_lookup("codex")
        if executable is None:
            raise CodexExternalDiscoveryError(
                "Codex CLI is unavailable. Install Codex, authenticate with ChatGPT, then retry."
            )
        prompt = self._prompt(context)
        with tempfile.TemporaryDirectory(prefix="career-trans-codex-") as directory:
            schema_path = Path(directory) / "external-discovery-output-schema.json"
            output_path = Path(directory) / "discovered-jobs.json"
            # The Codex CLI consumes a standard JSON Schema file.  Generate it from the
            # same canonical Pydantic contract that remains authoritative after the
            # subprocess completes; do not maintain a second, hand-written contract.
            try:
                schema_path.write_text(
                    json.dumps(self._output_schema(), separators=(",", ":")),
                    encoding="utf-8",
                )
            except StrictStructuredOutputSchemaError as exc:
                raise CodexExternalDiscoveryError(
                    "Codex discovery cannot generate a strict structured-output contract. "
                    "Check the installed OpenAI SDK."
                ) from exc
            # --search is a global Codex flag and must precede `exec`; current-vacancy
            # discovery requires live rather than cached web search.
            # Codex documents `-` as stdin prompt input. Keeping the full task off the
            # command line avoids cmd.exe reparsing prompt metacharacters via its .cmd shim.
            command = [
                executable,
                "-m",
                self._model,
                "--search",
                "exec",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
            try:
                result = self._runner(
                    command,
                    capture_output=True,
                    # The JSON output file is authoritative. Keep console diagnostics as
                    # bytes so Windows code-page output cannot crash Python decoding.
                    text=False,
                    input=prompt.encode("utf-8"),
                    timeout=self._timeout_seconds,
                    check=False,
                    shell=False,
                    env=self._codex_subprocess_environment(),
                )
            except subprocess.TimeoutExpired as exc:
                raise CodexExternalDiscoveryError(
                    "Codex discovery timed out. Narrow the search or retry."
                ) from exc
            except OSError as exc:
                raise CodexExternalDiscoveryError(
                    "Codex CLI could not be started. Check the local Codex installation and authentication."
                ) from exc
            if result.returncode != 0:
                diagnostic = self._safe_diagnostic(result.stderr) or self._safe_diagnostic(result.stdout)
                if diagnostic:
                    raise CodexExternalDiscoveryError(
                        f"Codex discovery failed (exit code {result.returncode}): {diagnostic}"
                    )
                raise CodexExternalDiscoveryError(
                    f"Codex discovery failed (exit code {result.returncode}). "
                    "Retry or run Codex directly for diagnostics."
                )
            try:
                raw_output = output_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise CodexExternalDiscoveryError(
                    "Codex discovery did not return a structured result. Retry the command."
                ) from exc
        try:
            return CodexExternalDiscoveryOutput.model_validate(json.loads(raw_output)).jobs
        except (json.JSONDecodeError, ValueError) as exc:
            raise CodexExternalDiscoveryError(
                "Codex returned invalid discovery JSON; no jobs were imported."
            ) from exc

    @staticmethod
    def _output_schema() -> dict[str, object]:
        """Return the SDK-derived strict schema for the canonical output model."""
        return strict_schema_from_pydantic_model(CodexExternalDiscoveryOutput)

    @staticmethod
    def _codex_subprocess_environment() -> dict[str, str]:
        """Keep OS/Codex runtime configuration while excluding Career-trans secrets."""
        environment = os.environ.copy()
        for name in list(environment):
            normalized = name.casefold()
            if normalized.startswith("codex_"):
                continue
            if (
                normalized.startswith("career_trans_")
                or normalized in _CAREER_TRANS_ENV_NAMES
                or any(marker in normalized for marker in _CREDENTIAL_ENV_MARKERS)
            ):
                environment.pop(name, None)
        return environment

    @staticmethod
    def _safe_diagnostic(value: bytes | str | None) -> str:
        """Map recognized safe errors to fixed text; never return raw subprocess text."""
        if not value:
            return ""
        if isinstance(value, bytes):
            text = value[-_DIAGNOSTIC_TAIL_BYTES:].decode("utf-8", errors="replace")
        else:
            text = value[-_DIAGNOSTIC_TAIL_BYTES:]
        had_secret = False
        for pattern in _SECRET_PATTERNS:
            text, count = pattern.subn("[REDACTED]", text)
            had_secret = had_secret or count > 0
        for pattern, safe_message in _SAFE_DIAGNOSTIC_CLASSIFIERS:
            if pattern.search(text):
                return f"{safe_message} [REDACTED]" if had_secret else safe_message
        # Even a completely unknown suffix may be a fragment of serialized task
        # context. Return only a fixed redaction marker if secrets were detected;
        # otherwise let discover() use its generic actionable error.
        return "[REDACTED]" if had_secret else ""

    @staticmethod
    def _prompt(context: ExternalDiscoverySearchContextResponse) -> str:
        max_jobs = min(context.query.max_results, 25)
        compact_context = json.dumps(
            {
                "search_profile": context.search_profile.model_dump(mode="json"),
                "query": context.query.model_dump(mode="json"),
            },
            separators=(",", ":"),
        )
        return (
            "Find up to "
            f"{max_jobs} current public job vacancies matching this bounded Career-trans context. "
            "Treat query keywords as search seeds, not literal title requirements: include "
            "factually supported, semantically adjacent job titles when the vacancy content is relevant. "
            "Use public web search and open only relevant public vacancy or careers pages. "
            "Do not log in, bypass access controls, submit applications, or invent missing facts. "
            "Return JSON only—no Markdown, prose, or code fences—with exactly this shape: "
            '{"jobs":[{"title":"...","company":"...","location":"...","url":"https://...",'
            '"description":"supported factual job text or null","posted_at":null,'
            '"employment_type":null,"work_arrangement":null,'
            '"provenance":{"source_ref":"search query or result URL","discovered_via":"web"}}]}. '
            "Omit unsupported optional facts. Do not include credentials, prompts, reasoning, or scratchpad data. "
            f"Context: {compact_context}"
        )

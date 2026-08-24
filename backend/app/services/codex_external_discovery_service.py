"""Bounded local Codex CLI adapter for external, non-authoritative job discovery."""

import json
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


_DIAGNOSTIC_TAIL_BYTES = 2_048
_MAX_DIAGNOSTIC_CHARS = 1_024
_SECRET_PATTERNS = (
    # Covers standalone keys and common prefixed environment names such as
    # OPENAI_API_KEY and GITHUB_TOKEN.
    re.compile(r"(?i)\b(?:[a-z][a-z0-9_-]*[_-])?(?:api[_-]?key|token|authorization|password|secret)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~-]+"),
    re.compile(r"\b(?:sk|lsv2|gho)_[A-Za-z0-9_-]+"),
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
    ) -> None:
        self._runner = runner
        self._executable_lookup = executable_lookup
        self._timeout_seconds = timeout_seconds

    def discover(self, context: ExternalDiscoverySearchContextResponse) -> list[ExternalDiscoveredJob]:
        executable = self._executable_lookup("codex")
        if executable is None:
            raise CodexExternalDiscoveryError(
                "Codex CLI is unavailable. Install Codex, authenticate with ChatGPT, then retry."
            )
        prompt = self._prompt(context)
        with tempfile.TemporaryDirectory(prefix="career-trans-codex-") as directory:
            output_path = Path(directory) / "discovered-jobs.json"
            # --search is a global Codex flag and must precede `exec`; current-vacancy
            # discovery requires live rather than cached web search.
            # Codex documents `-` as stdin prompt input. Keeping the full task off the
            # command line avoids cmd.exe reparsing prompt metacharacters via its .cmd shim.
            command = [executable, "--search", "exec", "--output-last-message", str(output_path), "-"]
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
    def _safe_diagnostic(value: bytes | str | None) -> str:
        """Expose a small, sanitized terminal tail without making it part of the result contract."""
        if not value:
            return ""
        if isinstance(value, bytes):
            text = value[-_DIAGNOSTIC_TAIL_BYTES:].decode("utf-8", errors="replace")
        else:
            text = value[-_DIAGNOSTIC_TAIL_BYTES:]
        # Codex should not echo the supplied task, but never surface one if it does.
        text = re.sub(r"(?is)\b(?:career-trans\s+)?(?:context|prompt|input)\s*:\s*.*", "[redacted task]", text)
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return " ".join(text.split())[-_MAX_DIAGNOSTIC_CHARS:]

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

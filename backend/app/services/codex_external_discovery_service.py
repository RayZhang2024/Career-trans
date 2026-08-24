"""Bounded local Codex CLI adapter for external, non-authoritative job discovery."""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.schemas.external_discovery import (
    CodexExternalDiscoveryOutput,
    ExternalDiscoveredJob,
    ExternalDiscoverySearchContextResponse,
)


class CodexExternalDiscoveryError(RuntimeError):
    """Safe, actionable failure from the local external discovery runtime."""


class CodexExternalDiscoveryRunner:
    """Run a user-authenticated local Codex CLI without sharing Career-trans credentials."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
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
            command = [executable, "exec", "--output-last-message", str(output_path), prompt]
            try:
                result = self._runner(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    check=False,
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
                raise CodexExternalDiscoveryError(
                    "Codex discovery failed. Check that Codex is authenticated, then retry."
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

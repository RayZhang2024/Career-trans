"""Bounded Codex CLI probing and structured invocation shared by discovery modes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.schemas.job_discovery_settings import (
    LocalCodexAuthenticationStatus,
    LocalCodexCapabilityStatus,
    LocalCodexDiscoveryStatus,
    LocalCodexScheduledStatus,
    LocalCodexStatusRead,
)


ProbeRunner = Callable[..., subprocess.CompletedProcess[bytes | str]]
ExecutableLookup = Callable[[str], str | None]
_VERSION_PATTERN = re.compile(r"\bcodex-cli\s+([^\s]+)", re.IGNORECASE)
_OUTPUT_LIMIT_BYTES = 64_000
_PROBE_TIMEOUT_SECONDS = 3
_PROBE_TAIL_BYTES = 64_000
_CAREER_TRANS_ENV_NAMES = frozenset({"database_url"})
_CREDENTIAL_ENV_MARKERS = (
    "api_key", "api-key", "token", "secret", "password", "credential",
    "authorization", "bearer", "auth",
)


def codex_subprocess_environment() -> dict[str, str]:
    """Retain OS/Codex runtime configuration while removing application secrets."""
    environment = os.environ.copy()
    for name in list(environment):
        normalized = name.casefold()
        if normalized == "codex_home":
            continue
        if (
            normalized.startswith("career_trans_")
            or normalized in _CAREER_TRANS_ENV_NAMES
            or any(marker in normalized for marker in _CREDENTIAL_ENV_MARKERS)
        ):
            environment.pop(name, None)
    return environment


@dataclass(frozen=True)
class CodexInvocation:
    returncode: int
    stdout: bytes
    stderr: bytes
    output_text: str | None


class CodexRuntimeAdapter:
    """Probe supported local Codex capabilities and run bounded schema output."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        runner: ProbeRunner = subprocess.run,
        executable_lookup: ExecutableLookup = shutil.which,
        timeout_seconds: int = 45,
    ) -> None:
        self._settings = settings or get_settings()
        self._runner = runner
        self._executable_lookup = executable_lookup
        self._timeout_seconds = max(1, min(int(timeout_seconds), 90))

    def probe(self) -> LocalCodexStatusRead:
        settings = self._settings
        scheduled = LocalCodexScheduledStatus(settings.local_codex_scheduled_discovery_capability)
        executable = self._executable_lookup("codex")
        cli_installed = executable is not None
        if not settings.local_codex_discovery_enabled:
            return LocalCodexStatusRead(
                enabled_by_deployment=False,
                cli_installed=cli_installed,
                version=None,
                authentication_status=LocalCodexAuthenticationStatus.UNKNOWN,
                structured_invocation_status=LocalCodexCapabilityStatus.UNKNOWN,
                search_capability_status=LocalCodexCapabilityStatus.UNKNOWN,
                manual_discovery_status=LocalCodexDiscoveryStatus.NOT_READY,
                scheduled_discovery_status=scheduled,
                message="Local Codex is disabled by this deployment.",
                setup_guidance="Ask the deployment administrator to enable Local Codex if this trusted backend should use its host Codex session.",
            )
        if executable is None:
            return self._not_ready(
                scheduled,
                message="Codex CLI is not installed on the Career-trans backend host.",
                guidance="Install Codex CLI for the backend OS account, then refresh status.",
            )

        version_output = self._probe_command(executable, ["--version"])
        version = self._parse_version(version_output)
        root_help = self._probe_command(executable, ["--help"])
        exec_help = self._probe_command(executable, ["exec", "--help"])
        login_output = self._probe_command(executable, ["login", "status"], allow_nonzero=True)

        if root_help is None or exec_help is None:
            invocation = LocalCodexCapabilityStatus.UNKNOWN
            search = LocalCodexCapabilityStatus.UNKNOWN
        else:
            root_text = root_help.casefold()
            exec_text = exec_help.casefold()
            invocation = (
                LocalCodexCapabilityStatus.AVAILABLE
                if "--output-schema" in exec_text
                and "--output-last-message" in exec_text
                and "stdin" in exec_text
                else LocalCodexCapabilityStatus.UNAVAILABLE
            )
            search = (
                LocalCodexCapabilityStatus.AVAILABLE
                if "--search" in root_text and "live web search" in root_text and "web_search" in root_text
                else LocalCodexCapabilityStatus.UNAVAILABLE
            )

        authentication = self._authentication_status(login_output)
        can_run = (
            invocation is LocalCodexCapabilityStatus.AVAILABLE
            and search is LocalCodexCapabilityStatus.AVAILABLE
            and authentication is not LocalCodexAuthenticationStatus.SIGNED_OUT
        )
        manual = LocalCodexDiscoveryStatus.READY if can_run else LocalCodexDiscoveryStatus.NOT_READY
        if authentication is LocalCodexAuthenticationStatus.SIGNED_OUT:
            message = "Codex authentication is required on the backend host."
            guidance = "On the backend host, complete the supported `codex login` flow, then refresh status. Do not paste Codex credentials into Career-trans."
        elif invocation is LocalCodexCapabilityStatus.UNAVAILABLE:
            message = "The installed Codex version does not expose the required structured invocation capability."
            guidance = "Install a Codex CLI version whose exec help includes structured output and stdin prompt support, then refresh status."
        elif search is LocalCodexCapabilityStatus.UNAVAILABLE:
            message = "The installed Codex version does not expose live web search."
            guidance = "Install a Codex CLI version whose help exposes live web search, then refresh status."
        elif can_run:
            message = "Local Codex passes the backend-host preflight. Test Local Codex to confirm live search access."
            guidance = (
                "Authentication could not be confirmed by the CLI; use Test Local Codex to check the live search path."
                if authentication is LocalCodexAuthenticationStatus.UNKNOWN
                else "No setup action is currently required. Test Local Codex to confirm live search access."
            )
        else:
            message = "Local Codex readiness could not be confirmed."
            guidance = "Refresh status or complete Codex setup on the backend host. Do not paste Codex credentials into Career-trans."

        return LocalCodexStatusRead(
            enabled_by_deployment=True,
            cli_installed=True,
            version=version,
            authentication_status=authentication,
            structured_invocation_status=invocation,
            search_capability_status=search,
            manual_discovery_status=manual,
            scheduled_discovery_status=scheduled,
            message=message,
            setup_guidance=guidance,
        )

    def invoke_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, object],
        model: str,
        schema_filename: str,
        output_filename: str,
        timeout_seconds: int | None = None,
        max_output_bytes: int = 64_000,
        sandbox: str | None = None,
        isolated_working_directory: bool = True,
    ) -> CodexInvocation:
        executable = self._executable_lookup("codex")
        if executable is None:
            raise FileNotFoundError("Codex CLI is not installed on the backend host.")
        with tempfile.TemporaryDirectory(prefix="career-trans-codex-") as directory:
            schema_path = Path(directory) / schema_filename
            output_path = Path(directory) / output_filename
            schema_path.write_text(json.dumps(schema, separators=(",", ":")), encoding="utf-8")
            command = [
                executable,
                "-m", model,
                "--search", "exec",
            ]
            if sandbox is not None:
                command.extend(["--sandbox", sandbox])
            command.extend([
                "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
                "-",
            ])
            try:
                with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                    result = self._runner(
                        command,
                        stdout=stdout_file,
                        stderr=stderr_file,
                        text=False,
                        input=prompt.encode("utf-8"),
                        timeout=timeout_seconds or self._timeout_seconds,
                        check=False,
                        shell=False,
                        **({"cwd": directory} if isolated_working_directory else {}),
                        env=codex_subprocess_environment(),
                    )
                    stdout = self._tail(result.stdout, stdout_file)
                    stderr = self._tail(result.stderr, stderr_file)
            except subprocess.TimeoutExpired:
                raise
            except OSError:
                raise
            output_text: str | None = None
            if result.returncode == 0:
                try:
                    if output_path.stat().st_size > max_output_bytes:
                        return CodexInvocation(result.returncode, stdout, stderr, None)
                    output_text = output_path.read_text(encoding="utf-8")
                except OSError:
                    output_text = None
            return CodexInvocation(int(result.returncode), stdout, stderr, output_text)

    def _probe_command(
        self,
        executable: str,
        arguments: list[str],
        *,
        allow_nonzero: bool = False,
    ) -> str | None:
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                result = self._runner(
                    [executable, *arguments],
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=False,
                    timeout=_PROBE_TIMEOUT_SECONDS,
                    check=False,
                    shell=False,
                    cwd=tempfile.gettempdir(),
                    env=codex_subprocess_environment(),
                )
                stdout = self._tail(result.stdout, stdout_file)
                stderr = self._tail(result.stderr, stderr_file)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 and not allow_nonzero:
            return None
        combined = f"{stdout.decode('utf-8', errors='replace')}\n{stderr.decode('utf-8', errors='replace')}"
        return combined[:_OUTPUT_LIMIT_BYTES]

    @staticmethod
    def _parse_version(output: str | None) -> str | None:
        if not output:
            return None
        match = _VERSION_PATTERN.search(output)
        return match.group(1)[:80] if match else None

    @staticmethod
    def _authentication_status(output: str | None) -> LocalCodexAuthenticationStatus:
        if output is None:
            return LocalCodexAuthenticationStatus.UNKNOWN
        lowered = output.casefold()
        if any(phrase in lowered for phrase in ("not logged in", "not authenticated", "not signed in", "signed out")):
            return LocalCodexAuthenticationStatus.SIGNED_OUT
        if any(phrase in lowered for phrase in ("logged in", "authenticated", "signed in")):
            return LocalCodexAuthenticationStatus.SIGNED_IN
        return LocalCodexAuthenticationStatus.UNKNOWN

    @staticmethod
    def _as_bytes(value: bytes | str | None) -> bytes:
        if value is None:
            return b""
        return value.encode("utf-8", errors="replace") if isinstance(value, str) else value

    @classmethod
    def _tail(cls, returned: bytes | str | None, stream) -> bytes:
        """Read only a bounded tail; subprocess output is spooled, not held in RAM."""
        if returned is not None:
            return cls._as_bytes(returned)[-_PROBE_TAIL_BYTES:]
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - _PROBE_TAIL_BYTES))
        return stream.read(_PROBE_TAIL_BYTES)

    @staticmethod
    def _not_ready(
        scheduled: LocalCodexScheduledStatus,
        *,
        message: str,
        guidance: str,
    ) -> LocalCodexStatusRead:
        return LocalCodexStatusRead(
            enabled_by_deployment=True,
            cli_installed=False,
            version=None,
            authentication_status=LocalCodexAuthenticationStatus.UNKNOWN,
            structured_invocation_status=LocalCodexCapabilityStatus.UNAVAILABLE,
            search_capability_status=LocalCodexCapabilityStatus.UNAVAILABLE,
            manual_discovery_status=LocalCodexDiscoveryStatus.NOT_READY,
            scheduled_discovery_status=scheduled,
            message=message,
            setup_guidance=guidance,
        )

"""Safe, process-cached application revision attribution."""

from __future__ import annotations

from functools import lru_cache
import subprocess
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def resolve_application_revision(deployment_revision: str | None = None) -> str | None:
    """Return an explicit deployment revision or the local Git HEAD once.

    A missing checkout, unavailable Git executable, or failed Git command is
    deliberately non-fatal: tracing remains available without revision data.
    """
    if deployment_revision and deployment_revision.strip():
        return deployment_revision.strip()

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    return result.stdout.strip() or None

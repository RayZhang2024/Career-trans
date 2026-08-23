import re
import unicodedata
from typing import Protocol
from urllib.parse import urlparse

from app.schemas.job_sources import CompanyTarget, ResolvedJobSource

SAFE_SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")


class JobSourceProbe(Protocol):
    name: str

    def probe(self, company: CompanyTarget, slug: str) -> ResolvedJobSource | None: ...


class JobSourceProbeError(Exception):
    """A provider request failed rather than simply missing a board."""


def derive_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    if not slug:
        raise ValueError("Company name does not produce a usable ATS slug.")
    return slug


def is_safe_slug(slug: str) -> bool:
    return bool(SAFE_SLUG_PATTERN.fullmatch(slug))


def require_safe_slug(slug: str) -> None:
    if not is_safe_slug(slug):
        raise ValueError("ATS slug contains unsafe characters.")


def has_expected_host(url: object, host: str) -> bool:
    """Confirm that a provider-returned job URL belongs to its requested tenant."""
    if not isinstance(url, str):
        return False
    return urlparse(url).hostname == host


def has_expected_host_path_prefix(url: object, host: str, path_prefix: str) -> bool:
    """Confirm a provider URL belongs to a tenant encoded in its first path segment."""
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    path_segments = [segment for segment in parsed.path.split("/") if segment]
    return parsed.hostname == host and bool(path_segments) and path_segments[0] == path_prefix

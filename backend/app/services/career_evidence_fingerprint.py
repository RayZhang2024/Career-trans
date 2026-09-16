import hashlib
import re
from typing import Protocol


class _EvidenceClaim(Protocol):
    evidence_type: str
    title: str
    text: str


def career_evidence_fingerprint(item: _EvidenceClaim) -> str:
    """Canonical stable identity under harmless whitespace/case changes."""
    return _fingerprint(item, normalize_whitespace=True)


def legacy_career_evidence_fingerprint(item: _EvidenceClaim) -> str:
    """Pre-#148 identity for safely reconciling existing persisted records."""
    return _fingerprint(item, normalize_whitespace=False)


def confirmed_profile_source_ref(fact_type: str, values: list[str | None]) -> str:
    """Stable source reference independent of mutable profile-list ordering."""
    material = "\x1f".join(_normalise(value or "") for value in values)
    return f"{fact_type}:{hashlib.sha256(material.encode()).hexdigest()}"


def _fingerprint(item: _EvidenceClaim, *, normalize_whitespace: bool) -> str:
    values = [item.evidence_type, item.title, item.text]
    if normalize_whitespace:
        values = [_normalise(value) for value in values]
    else:
        values = [value.casefold() for value in values]
    return hashlib.sha256(
        "\x1f".join(values).encode()
    ).hexdigest()


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()

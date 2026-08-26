"""Deterministic normalization for extracted job profiles.

The extractor is semantic, but the representation consumed by matching must be
stable when a model groups or orders equivalent criteria differently.  This
module only normalizes text already supported by the model output; it never
creates a requirement from advert text that was not returned by the extractor.
"""

from __future__ import annotations

import re

from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementImportance,
)


_WHITESPACE = re.compile(r"\s+")
_PARENTHETICAL_LIST = re.compile(r"\((?P<items>[^()]*,[^()]*)\)")
_QUALIFICATION_TERMS = (
    r"(?:(?:strong|practical|hands-on|professional|demonstrated|proven|"
    r"solid|relevant)\s+)*(?:experience|familiarity|knowledge|understanding|"
    r"proficiency|expertise|skills?)"
)
_LIST_PREFIX = re.compile(
    rf"^(?P<prefix>{_QUALIFICATION_TERMS}\s+(?:with|in|of)|"
    rf"(?:(?:strong|practical|hands-on|professional|demonstrated|proven|solid|"
    rf"relevant)\s+)*experience\s+"
    rf"(?:implementing|using|building|integrating|developing|working\s+with))\s+"
    r"(?P<items>[^,;]+,\s*[^,;]+(?:,\s*|\s+and\s+)[^.;]+)$",
    re.IGNORECASE,
)
_NON_ATOMIC_CONNECTOR = re.compile(r"\b(?:and/or|or)\b", re.IGNORECASE)
_PROTECTED_LIST_QUALIFIER = re.compile(
    r"(?:\bideally\b|\bpreferred\b|\bpreferably\b|"
    r"\bnice(?:\s+|-)?to(?:\s+|-)?have\b|\bbonus\b|\ba\s+plus\b|"
    r"\badvantageous\b|\bbeneficial\b|\bsuch\s+as\b|e\.g\.|"
    r"\bfor\s+example\b|\bfor\s+instance\b|\bincluding\b|\bincludes?\b)",
    re.IGNORECASE,
)
_CRITERION_PREFIX = re.compile(
    rf"(?P<prefix>{_QUALIFICATION_TERMS}\s+(?:with|in|of)|"
    rf"(?:(?:strong|practical|hands-on|professional|demonstrated|proven|solid|"
    rf"relevant)\s+)*experience\s+"
    rf"(?:implementing|using|building|integrating|developing|working\s+with))\b",
    re.IGNORECASE,
)
_PARALLEL_ACTIONS = re.compile(
    r"^(?P<prefix>(?:(?:strong|practical|hands-on|professional|demonstrated|"
    r"proven|solid|relevant)\s+)*experience)\s+"
    r"(?P<first>(?:implementing|using|building|integrating|developing)\b[^.;]+?)\s+"
    r"and\s+(?P<second>(?:implementing|using|building|integrating|developing)\b[^.;]+)$",
    re.IGNORECASE,
)
_SOURCE_TEXT_LIMIT = 1_000


def normalize_job_profile(profile: JobProfile) -> JobProfile:
    """Return a stable, source-grounded representation of ``profile``.

    Clearly enumerated comma-separated criteria are expanded only when every
    item can be assessed independently.  Alternatives (``or``/``and-or``),
    unstructured conjunctions, and inseparable wording remain untouched.
    Requirements are then deduplicated and sorted by canonical text.  Other
    extracted collections receive whitespace normalization, deduplication, and
    stable ordering without changing their meaning.
    """

    requirements = _normalize_requirements(profile.requirements)
    return profile.model_copy(
        update={
            "title": _normalize_optional_text(profile.title),
            "company": _normalize_optional_text(profile.company),
            "location": _normalize_optional_text(profile.location),
            "work_arrangement": _normalize_optional_text(profile.work_arrangement),
            "seniority": _normalize_optional_text(profile.seniority),
            "salary": _normalize_optional_text(profile.salary),
            "employment_type": _normalize_optional_text(profile.employment_type),
            "application_deadline": _normalize_optional_text(profile.application_deadline),
            "responsibilities": _normalize_collection(profile.responsibilities),
            "requirements": requirements,
            "technical_skills": _normalize_collection(profile.technical_skills),
            "domain_knowledge": _normalize_collection(profile.domain_knowledge),
            "security_requirements": _normalize_collection(profile.security_requirements),
            "work_authorization_requirements": _normalize_collection(
                profile.work_authorization_requirements
            ),
        }
    )


def _normalize_requirements(requirements: list[JobRequirement]) -> list[JobRequirement]:
    by_semantics: dict[tuple[str, str, str], JobRequirement] = {}

    for requirement in requirements:
        text = _normalize_requirement_text(requirement.text)
        source_text = _normalize_optional_text(requirement.source_text)
        if not text:
            continue

        for atomic_text in _atomic_texts(text, source_text):
            candidate = JobRequirement(
                text=atomic_text,
                importance=requirement.importance,
                category=requirement.category,
                source_text=source_text,
            )
            # A duplicate wording with different employer-provided category or
            # importance labels is a semantic conflict.  Python cannot safely
            # decide which label is stronger or more appropriate, so retain
            # each source-grounded interpretation rather than silently changing
            # fit/blocker semantics.
            key = (
                atomic_text.casefold(),
                candidate.importance.value,
                candidate.category.value,
            )
            existing = by_semantics.get(key)
            by_semantics[key] = (
                _merge_requirements(existing, candidate)
                if existing is not None
                else candidate
            )

    return sorted(
        by_semantics.values(),
        key=lambda item: (
            item.text.casefold(),
            item.category.value,
            item.importance.value,
            (item.source_text or "").casefold(),
        ),
    )


def _atomic_texts(text: str, source_text: str | None) -> list[str]:
    if _has_protected_list_qualifier(text, source_text):
        return [text]

    direct_parts = _extract_list_parts(text)
    if direct_parts:
        prefix, items = direct_parts
        source_parts = _extract_list_parts(source_text) if source_text else None
        if source_parts:
            source_prefix, source_items = source_parts
            canonical_items = [
                _matching_source_item(item, source_items)
                for item in items
            ]
            if all(item is not None for item in canonical_items):
                return [
                    _join_prefix(source_prefix, item)
                    for item in canonical_items
                    if item is not None
                ]
        return [_join_prefix(prefix, item) for item in items]

    # A model may emit one atomic item while retaining the original grouped
    # wording as provenance.  Use that source wording to canonicalize the item
    # to the same representation as a grouped model response.
    source_parts = _extract_list_parts(source_text) if source_text else None
    if source_parts:
        prefix, source_items = source_parts
        matches = [
            item
            for item in source_items
            if _contains_item(text, item)
        ]
        if len(matches) == 1:
            return [_join_prefix(prefix, matches[0])]

    return [text]


def _extract_list_items(text: str | None) -> list[str] | None:
    parts = _extract_list_parts(text)
    return parts[1] if parts else None


def _extract_list_parts(text: str | None) -> tuple[str, list[str]] | None:
    if (
        not text
        or _NON_ATOMIC_CONNECTOR.search(text)
        or _PROTECTED_LIST_QUALIFIER.search(text)
    ):
        return None

    parenthetical_matches = list(_PARENTHETICAL_LIST.finditer(text))
    for match in reversed(parenthetical_matches):
        items = _split_list(match.group("items"))
        prefix = _criterion_prefix(text[: match.start()])
        if items and prefix:
            return prefix, items

    qualification_clause = _qualification_clause(text)
    match = _LIST_PREFIX.match(qualification_clause)
    if match is not None:
        items = _split_list(match.group("items"))
        if items:
            return _clean_prefix(match.group("prefix")), items

    parallel_actions = _PARALLEL_ACTIONS.match(qualification_clause)
    if parallel_actions is None:
        return None
    return (
        _clean_prefix(parallel_actions.group("prefix")),
        [parallel_actions.group("first"), parallel_actions.group("second")],
    )


def _criterion_prefix(value: str) -> str | None:
    match = _CRITERION_PREFIX.search(value)
    return _clean_prefix(match.group("prefix")) if match else None


def _qualification_clause(value: str) -> str:
    """Drop a section heading before matching an explicit qualification clause."""
    match = _CRITERION_PREFIX.search(value)
    return value[match.start() :] if match else value


def _clean_prefix(value: str) -> str:
    prefix = value.strip()
    for separator in (":", "—"):
        if separator in prefix:
            prefix = prefix.rsplit(separator, 1)[-1].strip()
    return prefix


def _join_prefix(prefix: str, item: str) -> str:
    return _normalize_text(f"{prefix} {item}")


def _split_list(value: str) -> list[str] | None:
    if _NON_ATOMIC_CONNECTOR.search(value):
        return None

    parts = [
        _normalize_text(part.strip(" .;:"))
        for part in re.split(r",\s*|\band\s+", value)
    ]
    parts = [part for part in parts if part]
    if len(parts) < 2 or len({part.casefold() for part in parts}) != len(parts):
        return None
    return parts


def _contains_item(text: str, item: str) -> bool:
    return item.casefold() in text.casefold()


def _matching_source_item(item: str, source_items: list[str]) -> str | None:
    matches = [
        source_item
        for source_item in source_items
        if _contains_item(item, source_item) or _contains_item(source_item, item)
    ]
    return matches[0] if len(matches) == 1 else None


def _merge_requirements(
    first: JobRequirement,
    second: JobRequirement,
) -> JobRequirement:
    """Merge only requirements with identical source-grounded semantics."""
    return first.model_copy(
        update={
            "text": min(
                (first.text, second.text),
                key=lambda value: (value.casefold(), value),
            ),
            "source_text": _merge_source_texts(
                first.source_text,
                second.source_text,
            )
        }
    )


def _has_protected_list_qualifier(text: str, source_text: str | None) -> bool:
    return bool(
        _PROTECTED_LIST_QUALIFIER.search(text)
        or (source_text and _PROTECTED_LIST_QUALIFIER.search(source_text))
    )


def _merge_source_texts(first: str | None, second: str | None) -> str | None:
    values = sorted(
        {value for value in (first, second) if value},
        key=str.casefold,
    )
    if not values:
        return None
    merged = " / ".join(values)
    if len(merged) > _SOURCE_TEXT_LIMIT:
        return merged[: _SOURCE_TEXT_LIMIT - 3].rstrip() + "..."
    return merged


def _normalize_collection(values: list[str]) -> list[str]:
    normalized: dict[str, str] = {}
    for value in values:
        cleaned = _normalize_text(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        existing = normalized.get(key)
        if existing is None or (cleaned.casefold(), cleaned) < (
            existing.casefold(),
            existing,
        ):
            normalized[key] = cleaned
    return sorted(normalized.values(), key=lambda value: (value.casefold(), value))


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _normalize_text(value)
    return cleaned or None


def _normalize_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _normalize_requirement_text(value: str) -> str:
    return _normalize_text(value).rstrip(" .;:")

"""Deterministic rendering and readiness checks for extracted vacancy detail."""

from app.schemas.agentic_discovery import ExtractedVacancy
from app.schemas.discovery import JobListing


MAX_ANALYSIS_DESCRIPTION_CHARS = 50_000
STRUCTURED_DETAIL_MARKER = "Structured vacancy evidence:"


def render_analysis_description(extracted: ExtractedVacancy) -> str | None:
    """Render structured evidence in a stable, labeled order within graph limits.

    Candidate criteria are written first so truncation can never discard them in
    favor of background narrative or responsibilities.
    """
    sections: list[tuple[str, list[str]]] = [
        ("Candidate requirements", [_criterion(item) for item in extracted.candidate_requirements]),
        ("Preferred qualifications", [_criterion(item) for item in extracted.preferred_qualifications]),
        ("Other fit-relevant conditions", [_criterion(item) for item in extracted.other_fit_relevant_conditions]),
        ("Responsibilities", [_clean(item) for item in extracted.responsibilities]),
        ("Vacancy detail", [_clean(extracted.description or "")]),
    ]
    rendered: list[str] = []
    # Reserve the separators between the at most five labeled sections too.
    remaining = MAX_ANALYSIS_DESCRIPTION_CHARS - len(STRUCTURED_DETAIL_MARKER) - 1 - 8
    for heading, values in sections:
        clean_values = [value for value in values if value]
        if not clean_values:
            continue
        header = f"{heading}:\n"
        if remaining <= len(header):
            break
        block = header
        remaining -= len(header)
        for value in clean_values:
            line = f"- {value}\n"
            if remaining <= 0:
                break
            if len(line) > remaining:
                if remaining > 3:
                    block += line[: remaining - 2].rstrip() + "…\n"
                    remaining = 0
                break
            block += line
            remaining -= len(line)
        rendered.append(block.rstrip())
        if remaining <= 0:
            break
    result = f"{STRUCTURED_DETAIL_MARKER}\n" + "\n\n".join(rendered).strip() if rendered else ""
    return result or None


def has_explicit_candidate_criterion(description: str | None) -> bool:
    """Readiness requires a persisted candidate qualification, never body length."""
    if not description or not description.startswith(STRUCTURED_DETAIL_MARKER):
        return False
    for section in description[len(STRUCTURED_DETAIL_MARKER):].lstrip("\n").split("\n\n"):
        heading, _, lines = section.partition(":\n")
        if heading not in {"Candidate requirements", "Preferred qualifications"}:
            continue
        if any(line.startswith("- ") and line[2:].strip(" …") for line in lines.splitlines()):
            return True
    return False


def is_agentic_web_analysis_ready(job: JobListing) -> bool:
    return job.source.casefold() != "agentic_web" or has_explicit_candidate_criterion(job.description)


def _criterion(item) -> str:
    text = _clean(item.text)
    if not text:
        return ""
    importance = getattr(item.importance, "value", item.importance)
    category = getattr(item.category, "value", item.category)
    return f"[{importance}; {category}] {text}"


def _clean(value: str) -> str:
    return " ".join(value.split())

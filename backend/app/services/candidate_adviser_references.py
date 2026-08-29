"""Canonical, supplied-only source references for candidate-adviser output."""

from collections.abc import Mapping

from app.schemas.candidate_adviser import CandidateAdviserIntake, CandidateAdviserSemanticInput


_INTAKE_FIELDS = (
    "career_direction",
    "work_preferences",
    "constraints",
    "self_assessment",
    "motivations",
    "tradeoffs",
)
_ELIGIBILITY_FIELDS = (
    "work_authorisation",
    "security_clearances",
    "locations",
)


def candidate_adviser_reference_catalog(semantic_input: CandidateAdviserSemanticInput) -> dict[str, list[str]]:
    """Return exact source-reference tokens available to one adviser call.

    Tokens intentionally identify only supplied source fields and evidence IDs.
    They are included in the model instruction and used verbatim by the
    deterministic output validator; no prefixes, indexes, or subpaths are
    accepted as aliases.
    """

    intake = semantic_input.intake
    intake_tokens = [field for field in _INTAKE_FIELDS if getattr(intake, field)]
    intake_tokens.extend(
        f"eligibility.{field}"
        for field in _ELIGIBILITY_FIELDS
        if getattr(intake.eligibility, field)
    )
    return {
        "intake": intake_tokens,
        "career_evidence": list(dict.fromkeys(item.evidence_id for item in semantic_input.career_evidence)),
    }


def candidate_adviser_reference_is_allowed(
    *,
    source_type: str,
    reference: str,
    catalog: Mapping[str, list[str]],
) -> bool:
    """Validate a model reference against the exact catalog for its call."""

    return reference in catalog.get(source_type, [])

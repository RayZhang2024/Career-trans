"""Deterministic, stage-specific views over a candidate context."""

import re

from app.schemas.candidate import (
    CandidateCareerProfile,
    CandidateContext,
    CandidateMatchingProfile,
    CandidateMatchingEvidence,
    CandidateSearchProfile,
    CareerEvidence,
)
from app.schemas.job import JobProfile
from app.schemas.matching import (
    REQUIREMENT_EVIDENCE_PER_REQUIREMENT_LIMIT,
    REQUIREMENT_EVIDENCE_PROVIDER_LIMIT,
    RequirementEvidencePlan,
    RequirementEvidenceScope,
)


_PROFILE_SUMMARY_LIMIT = 1_200
_DIRECTION_TEXT_LIMIT = 1_200
_SKILL_LIMIT = 40
_TOP_EVIDENCE_LIMIT = REQUIREMENT_EVIDENCE_PROVIDER_LIMIT
_PER_REQUIREMENT_EVIDENCE_LIMIT = REQUIREMENT_EVIDENCE_PER_REQUIREMENT_LIMIT
_TOKEN_PATTERN = re.compile(r"[a-z0-9+#.]{2,}", re.IGNORECASE)


def candidate_search_profile(candidate: CandidateContext) -> CandidateSearchProfile:
    return CandidateSearchProfile(
        profile_summary=_compact_text(candidate.profile_text),
        skills=_candidate_skills(candidate),
        career_strategy_text=_compact_text(
            candidate.career_strategy_text,
            limit=_DIRECTION_TEXT_LIMIT,
        ),
        job_search_criteria_text=_compact_text(
            candidate.job_search_criteria_text,
            limit=_DIRECTION_TEXT_LIMIT,
        ),
    )


def candidate_career_profile(candidate: CandidateContext) -> CandidateCareerProfile:
    return CandidateCareerProfile(
        profile_summary=_compact_text(candidate.profile_text),
        career_strategy_text=_compact_text(
            candidate.career_strategy_text,
            limit=_DIRECTION_TEXT_LIMIT,
        ),
        job_search_criteria_text=_compact_text(
            candidate.job_search_criteria_text,
            limit=_DIRECTION_TEXT_LIMIT,
        ),
        eligibility=candidate.eligibility,
    )


def candidate_matching_profile(
    candidate: CandidateContext,
    job_profile: JobProfile | None = None,
    *,
    evidence_plan: RequirementEvidencePlan | None = None,
    limit: int = _TOP_EVIDENCE_LIMIT,
) -> CandidateMatchingProfile:
    """Build the provider-safe view from one retrieval owner.

    Callers with a request-owned ``evidence_plan`` must pass it here.  The
    legacy ``job_profile`` path remains for direct callers, but workflow code
    constructs the plan first and never performs a second retrieval pass.
    """
    if evidence_plan is None:
        if job_profile is None:
            raise ValueError("A job profile or requirement evidence plan is required.")
        evidence_plan = requirement_evidence_plan(candidate.evidence, job_profile, limit=limit)

    return CandidateMatchingProfile(
        profile_summary=_compact_text(candidate.profile_text),
        skills=_candidate_skills(candidate),
        evidence=[
            CandidateMatchingEvidence(
                evidence_id=item.evidence_id,
                evidence_type=item.evidence_type,
                title=item.title,
                text=item.text,
                skills=item.skills,
            )
            for item in evidence_plan.provider_evidence
        ],
    )


def top_evidence(
    evidence: list[CareerEvidence],
    job_profile: JobProfile,
    *,
    limit: int = _TOP_EVIDENCE_LIMIT,
) -> list[CareerEvidence]:
    """Compatibility view of the provider union from the retrieval plan."""
    return list(requirement_evidence_plan(evidence, job_profile, limit=limit).provider_evidence)


def requirement_evidence_plan(
    evidence: list[CareerEvidence],
    job_profile: JobProfile,
    *,
    limit: int = _TOP_EVIDENCE_LIMIT,
    per_requirement_limit: int = _PER_REQUIREMENT_EVIDENCE_LIMIT,
) -> RequirementEvidencePlan:
    """Create one deterministic, requirement-scoped provider evidence plan.

    Lexical/skill overlap is the primary ordering signal.  Category/type
    affinity only settles equal-overlap candidates; evidence IDs are the final
    stable tie-break, so input list order cannot affect the plan.
    """
    evidence = _unique_evidence_by_id(evidence)
    if limit < 1:
        plan = RequirementEvidencePlan(
            provider_evidence=(),
            scopes=tuple(
                RequirementEvidenceScope(requirement_index=index)
                for index in range(len(job_profile.requirements))
            ),
        )
        plan.validate_for(
            requirement_count=len(job_profile.requirements),
            provider_limit=limit,
            per_requirement_limit=per_requirement_limit,
        )
        return plan

    ranked_by_requirement: list[list[CareerEvidence]] = []
    for requirement in job_profile.requirements:
        requirement_terms = _terms(requirement.text)
        ranked = sorted(
            (
                item
                for item in evidence
                if requirement_terms & _evidence_terms(item)
            ),
            key=lambda item: (
                -len(requirement_terms & _evidence_terms(item)),
                -_type_preference(requirement.category.value, item.evidence_type),
                item.evidence_id,
            ),
        )
        ranked_by_requirement.append(ranked[:per_requirement_limit])

    selected: list[CareerEvidence] = []
    selected_ids: set[str] = set()
    scoped_ids: list[list[str]] = [[] for _ in ranked_by_requirement]
    for rank in range(per_requirement_limit):
        for requirement_index, ranked in enumerate(ranked_by_requirement):
            if rank >= len(ranked):
                continue
            item = ranked[rank]
            if item.evidence_id not in selected_ids:
                if len(selected) == limit:
                    continue
                selected.append(item)
                selected_ids.add(item.evidence_id)
            # A duplicate is permitted in multiple scopes, but only after it
            # survives into the bounded provider union.
            if (
                item.evidence_id in selected_ids
                and item.evidence_id not in scoped_ids[requirement_index]
            ):
                scoped_ids[requirement_index].append(item.evidence_id)

    plan = RequirementEvidencePlan(
        provider_evidence=tuple(selected),
        scopes=tuple(
            RequirementEvidenceScope(
                requirement_index=index,
                allowed_evidence_ids=tuple(ids),
            )
            for index, ids in enumerate(scoped_ids)
        ),
    )
    plan.validate_for(
        requirement_count=len(job_profile.requirements),
        provider_limit=limit,
        per_requirement_limit=per_requirement_limit,
    )
    return plan


def _candidate_skills(candidate: CandidateContext) -> list[str]:
    skills: list[str] = []
    seen: set[str] = set()
    for skill in [
        *re.split(r"[,;\n]", candidate.skills_text),
        *(skill for item in candidate.evidence for skill in item.skills),
    ]:
        cleaned = " ".join(skill.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            skills.append(cleaned)
    return skills[:_SKILL_LIMIT]


def _compact_text(value: str, *, limit: int = _PROFILE_SUMMARY_LIMIT) -> str:
    return " ".join(value.split())[:limit]


def _terms(value: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(value)}


def _evidence_terms(item: CareerEvidence) -> set[str]:
    return _terms(" ".join([item.title, item.text, *item.skills]))


def _unique_evidence_by_id(evidence: list[CareerEvidence]) -> list[CareerEvidence]:
    """Deduplicate exact repeats; reject conflicting canonical records early.

    Evidence IDs are canonical identifiers.  Repeated identical records are
    harmless input duplication, while a single ID carrying different canonical
    content would make a citation ambiguous and therefore fails closed before
    any provider request.
    """
    by_id: dict[str, CareerEvidence] = {}
    for item in evidence:
        existing = by_id.get(item.evidence_id)
        if existing is None:
            by_id[item.evidence_id] = item
        elif existing != item:
            raise ValueError("Duplicate candidate evidence ID has conflicting content.")
    return list(by_id.values())


def _type_preference(requirement_category: str, evidence_type: str) -> int:
    """Conservative secondary ranking only; unknown types stay eligible."""
    normalized = evidence_type.casefold()
    preferred = {
        "education": {"education", "credential"},
        "experience": {"employment", "project", "achievement"},
        "leadership": {"employment", "project", "achievement"},
    }
    return int(normalized in preferred.get(requirement_category, set()))

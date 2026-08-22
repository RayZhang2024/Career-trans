"""Deterministic, stage-specific views over a candidate context."""

import re

from app.schemas.candidate import (
    CandidateCareerProfile,
    CandidateContext,
    CandidateMatchingProfile,
    CandidateSearchProfile,
    CareerEvidence,
)
from app.schemas.job import JobProfile


_PROFILE_SUMMARY_LIMIT = 1_200
_DIRECTION_TEXT_LIMIT = 1_200
_SKILL_LIMIT = 40
_TOP_EVIDENCE_LIMIT = 8
_PER_REQUIREMENT_EVIDENCE_LIMIT = 3
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
    job_profile: JobProfile,
    *,
    limit: int = _TOP_EVIDENCE_LIMIT,
) -> CandidateMatchingProfile:
    return CandidateMatchingProfile(
        profile_summary=_compact_text(candidate.profile_text),
        skills=_candidate_skills(candidate),
        evidence=top_evidence(candidate.evidence, job_profile, limit=limit),
    )


def top_evidence(
    evidence: list[CareerEvidence],
    job_profile: JobProfile,
    *,
    limit: int = _TOP_EVIDENCE_LIMIT,
) -> list[CareerEvidence]:
    """Select bounded, requirement-aware evidence with stable ordering.

    Each semantic requirement receives its own small lexical retrieval pass.
    The resulting evidence is selected round-robin by per-requirement rank,
    deduplicated by ID, and bounded by the total prompt budget. Requirement and
    source order break ties deterministically.
    """
    if limit < 1:
        return []

    ranked_by_requirement: list[list[CareerEvidence]] = []
    for requirement in job_profile.requirements:
        requirement_terms = _terms(requirement.text)
        ranked = [
            item
            for _, item in sorted(
            enumerate(evidence),
            key=lambda indexed: (
                -len(
                    requirement_terms
                    & _terms(
                        " ".join(
                            [
                                indexed[1].title,
                                indexed[1].text,
                                *indexed[1].skills,
                            ]
                        )
                    )
                ),
                indexed[0],
            ),
            )
            if requirement_terms
            & _terms(" ".join([item.title, item.text, *item.skills]))
        ]
        ranked_by_requirement.append(ranked[:_PER_REQUIREMENT_EVIDENCE_LIMIT])

    selected: list[CareerEvidence] = []
    selected_ids: set[str] = set()
    for rank in range(_PER_REQUIREMENT_EVIDENCE_LIMIT):
        for ranked in ranked_by_requirement:
            if rank >= len(ranked):
                continue
            item = ranked[rank]
            if item.evidence_id in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(item.evidence_id)
            if len(selected) == limit:
                return selected
    return selected


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

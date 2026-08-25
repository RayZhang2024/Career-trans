"""Deterministic compact input construction for career-alignment assessment."""

from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import (
    CareerAlignmentFitSummary,
    CareerAlignmentInput,
    CareerAlignmentJobProfile,
)
from app.schemas.job import JobProfile
from app.services.candidate_profile_compaction import candidate_career_profile


_RESPONSIBILITY_LIMIT = 8
_DOMAIN_LIMIT = 8
_THEME_TEXT_LIMIT = 240


def career_alignment_input(
    candidate_context: CandidateContext,
    job_profile: JobProfile,
    fit_assessment: FitAssessment,
) -> CareerAlignmentInput:
    """Build the minimum strategic context required by the alignment agent.

    Canonical requirements, their source text, and detailed fit gaps are used by
    deterministic matching/scoring stages but do not inform the six strategic
    alignment dimensions. The original objects remain available to those stages;
    this function only bounds the LLM payload.
    """
    return CareerAlignmentInput(
        job_profile=CareerAlignmentJobProfile(
            title=job_profile.title,
            company=job_profile.company,
            location=job_profile.location,
            work_arrangement=job_profile.work_arrangement,
            seniority=job_profile.seniority,
            salary=job_profile.salary,
            employment_type=job_profile.employment_type,
            application_deadline=job_profile.application_deadline,
            responsibilities=_compact_themes(
                job_profile.responsibilities,
                limit=_RESPONSIBILITY_LIMIT,
            ),
            domain_knowledge=_compact_themes(
                job_profile.domain_knowledge,
                limit=_DOMAIN_LIMIT,
            ),
        ),
        candidate_context=candidate_career_profile(candidate_context),
        fit_assessment=CareerAlignmentFitSummary(
            fit_score=fit_assessment.fit_score,
            essential_score=fit_assessment.essential_score,
            desirable_score=fit_assessment.desirable_score,
            strength_count=len(fit_assessment.strengths),
            gap_count=len(fit_assessment.gaps),
            hard_blocker_count=len(fit_assessment.hard_blockers),
        ),
    )


def _compact_themes(values: list[str], *, limit: int) -> list[str]:
    """Normalize, deduplicate, and bound existing extracted theme text."""
    themes: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split())[:_THEME_TEXT_LIMIT]
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        themes.append(cleaned)
        if len(themes) == limit:
            break
    return themes

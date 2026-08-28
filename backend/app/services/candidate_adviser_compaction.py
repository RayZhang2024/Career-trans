"""Deterministic, bounded semantic input for candidate-adviser synthesis."""

from collections.abc import Iterable

from app.schemas.candidate import CandidateEligibility
from app.schemas.candidate_adviser import CandidateAdviserEvidenceInput, CandidateAdviserIntake, CandidateAdviserSemanticInput
from app.schemas.cv_ingestion import Achievement, CandidateCVData, Education, Employment, Project, Skill


_INTAKE_TEXT_LIMIT = 600
_INTAKE_LIST_LIMIT = 12
_INTAKE_ITEM_LIMIT = 240
_ELIGIBILITY_LIST_LIMIT = 12
_ELIGIBILITY_ITEM_LIMIT = 120
_EMPLOYMENT_LIMIT = 12
_EDUCATION_LIMIT = 8
_SKILL_LIMIT = 60
_PROJECT_LIMIT = 8
_ACHIEVEMENT_LIMIT = 12
_DESCRIPTION_LIMIT = 600
_EVIDENCE_LIMIT = 24
_EVIDENCE_TITLE_LIMIT = 240
_EVIDENCE_TEXT_LIMIT = 1_200
_EVIDENCE_SKILL_LIMIT = 16
_SKILL_ITEM_LIMIT = 120


def compact_candidate_adviser_input(
    *,
    intake: CandidateAdviserIntake,
    structured_cv: CandidateCVData,
    career_evidence: Iterable[dict[str, object]],
) -> CandidateAdviserSemanticInput:
    """Project persisted sources into the one bounded payload sent to the model.

    Source records themselves remain intact; only this semantic projection is
    truncated. Stable source order is retained so the projection and its
    fingerprint are reproducible.
    """
    return CandidateAdviserSemanticInput(
        intake=_compact_intake(intake),
        structured_cv=_compact_structured_cv(structured_cv),
        career_evidence=[
            CandidateAdviserEvidenceInput(
                evidence_id=str(item["evidence_id"]),
                title=_text(str(item.get("title", "")), _EVIDENCE_TITLE_LIMIT),
                text=_text(str(item.get("text", "")), _EVIDENCE_TEXT_LIMIT),
                skills=_items(item.get("skills", []), limit=_EVIDENCE_SKILL_LIMIT, item_limit=_SKILL_ITEM_LIMIT),
            )
            for item in list(career_evidence)[:_EVIDENCE_LIMIT]
        ],
    )


def _compact_intake(intake: CandidateAdviserIntake) -> CandidateAdviserIntake:
    return CandidateAdviserIntake(
        career_direction=_text(intake.career_direction, _INTAKE_TEXT_LIMIT),
        work_preferences=_items(intake.work_preferences, limit=_INTAKE_LIST_LIMIT, item_limit=_INTAKE_ITEM_LIMIT),
        constraints=_items(intake.constraints, limit=_INTAKE_LIST_LIMIT, item_limit=_INTAKE_ITEM_LIMIT),
        self_assessment=_items(intake.self_assessment, limit=_INTAKE_LIST_LIMIT, item_limit=_INTAKE_ITEM_LIMIT),
        motivations=_items(intake.motivations, limit=_INTAKE_LIST_LIMIT, item_limit=_INTAKE_ITEM_LIMIT),
        tradeoffs=_items(intake.tradeoffs, limit=_INTAKE_LIST_LIMIT, item_limit=_INTAKE_ITEM_LIMIT),
        eligibility=CandidateEligibility(
            work_authorisation=_items(intake.eligibility.work_authorisation, limit=_ELIGIBILITY_LIST_LIMIT, item_limit=_ELIGIBILITY_ITEM_LIMIT),
            security_clearances=_items(intake.eligibility.security_clearances, limit=_ELIGIBILITY_LIST_LIMIT, item_limit=_ELIGIBILITY_ITEM_LIMIT),
            locations=_items(intake.eligibility.locations, limit=_ELIGIBILITY_LIST_LIMIT, item_limit=_ELIGIBILITY_ITEM_LIMIT),
        ),
    )


def _compact_structured_cv(data: CandidateCVData) -> CandidateCVData:
    return CandidateCVData(
        employment=[
            Employment(
                employer=_text(item.employer, _SKILL_ITEM_LIMIT),
                title=_text(item.title, _SKILL_ITEM_LIMIT),
                start_date=item.start_date,
                end_date=item.end_date,
                location=_text(item.location or "", _SKILL_ITEM_LIMIT) or None,
                description=_text(item.description, _DESCRIPTION_LIMIT),
            )
            for item in data.employment[:_EMPLOYMENT_LIMIT]
        ],
        education=[
            Education(
                institution=_text(item.institution, _SKILL_ITEM_LIMIT),
                qualification=_text(item.qualification, _SKILL_ITEM_LIMIT),
                field_of_study=_text(item.field_of_study or "", _SKILL_ITEM_LIMIT) or None,
                description=_text(item.description, _DESCRIPTION_LIMIT),
            )
            for item in data.education[:_EDUCATION_LIMIT]
        ],
        skills=[
            Skill(name=_text(item.name, _SKILL_ITEM_LIMIT), category=_text(item.category or "", _SKILL_ITEM_LIMIT) or None)
            for item in data.skills[:_SKILL_LIMIT]
        ],
        projects=[
            Project(
                name=_text(item.name, _SKILL_ITEM_LIMIT),
                description=_text(item.description, _DESCRIPTION_LIMIT),
                skills=_items(item.skills, limit=_EVIDENCE_SKILL_LIMIT, item_limit=_SKILL_ITEM_LIMIT),
            )
            for item in data.projects[:_PROJECT_LIMIT]
        ],
        achievements=[Achievement(text=_text(item.text, _DESCRIPTION_LIMIT)) for item in data.achievements[:_ACHIEVEMENT_LIMIT]],
    )


def _items(values: object, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(values, Iterable) or isinstance(values, str):
        return []
    return [_text(str(value), item_limit) for value in list(values)[:limit] if _text(str(value), item_limit)]


def _text(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel

from app.schemas.candidate import CandidateAdviserContext, CandidateContext, CandidateEligibility, CareerEvidence
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessment,
    CandidateAdviserSourceContext,
    CandidateIntakeProfileData,
)


_PROFILE_LIMIT = 6_000
_DIRECTION_LIMIT = 3_000
_SKILL_LIMIT = 80
_EVIDENCE_LIMIT = 40
_EVIDENCE_TEXT_LIMIT = 1_000
_EVIDENCE_TITLE_LIMIT = 300
_EVIDENCE_SKILL_LIMIT = 20
_INTAKE_TEXT_LIMIT = 1_200
_INTAKE_LIST_LIMIT = 20
_INTAKE_LIST_ITEM_LIMIT = 400


def candidate_adviser_source_context(candidate: CandidateContext) -> CandidateAdviserSourceContext:
    return CandidateAdviserSourceContext(
        profile_summary=_compact_text(candidate.profile_text, _PROFILE_LIMIT),
        skills=_candidate_skills(candidate),
        career_strategy_text=_compact_text(candidate.career_strategy_text, _DIRECTION_LIMIT),
        job_search_criteria_text=_compact_text(candidate.job_search_criteria_text, _DIRECTION_LIMIT),
        eligibility=CandidateEligibility(
            work_authorisation=_compact_list(candidate.eligibility.work_authorisation),
            security_clearances=_compact_list(candidate.eligibility.security_clearances),
            locations=_compact_list(candidate.eligibility.locations),
        ),
        evidence=_bounded_evidence(candidate.evidence),
    )


def compact_candidate_intake(intake: CandidateIntakeProfileData) -> CandidateIntakeProfileData:
    """Bound semantic intake without changing persisted candidate-authored source data."""
    return CandidateIntakeProfileData.model_validate(
        _compact_value(intake.model_dump(mode="json"))
    )


def candidate_adviser_input_fingerprint(
    *,
    candidate_context: CandidateContext,
    intake: CandidateIntakeProfileData,
) -> str:
    """Fingerprint the exact bounded source payload supplied to the adviser."""
    payload = {
        "candidate_context": candidate_adviser_source_context(candidate_context).model_dump(mode="json"),
        "intake": compact_candidate_intake(intake).model_dump(mode="json"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def intake_source_refs(intake: CandidateIntakeProfileData) -> set[str]:
    refs: set[str] = set()
    _collect_nonempty_paths(intake.model_dump(mode="json"), "", refs)
    return refs


def adviser_context_from_assessment(assessment: CandidateAdviserAssessment) -> CandidateAdviserContext:
    return CandidateAdviserContext(
        professional_identity=assessment.professional_identity,
        career_strategy_summary=assessment.career_strategy_summary,
        job_search_strategy_summary=assessment.job_search_strategy_summary,
        role_hypotheses=[item.role_family for item in assessment.role_hypotheses],
        development_priorities=[item.text for item in assessment.development_gaps],
    )


def _candidate_skills(candidate: CandidateContext) -> list[str]:
    return _compact_list(
        [
            *re.split(r"[,;\n]", candidate.skills_text),
            *(skill for item in candidate.evidence for skill in item.skills),
        ],
        limit=_SKILL_LIMIT,
    )


def _bounded_evidence(evidence: list[CareerEvidence]) -> list[CareerEvidence]:
    selected = evidence
    if len(evidence) > _EVIDENCE_LIMIT:
        half = _EVIDENCE_LIMIT // 2
        selected = [*evidence[:half], *evidence[-half:]]
    return [
        CareerEvidence(
            evidence_id=item.evidence_id,
            title=_compact_text(item.title, _EVIDENCE_TITLE_LIMIT),
            text=_compact_text(item.text, _EVIDENCE_TEXT_LIMIT),
            skills=_compact_list(item.skills, limit=_EVIDENCE_SKILL_LIMIT),
        )
        for item in selected
    ]


def _compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _compact_value(child) for key, child in value.items()}
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return _compact_list(value, limit=_INTAKE_LIST_LIMIT)
        return [_compact_value(item) for item in value[:_INTAKE_LIST_LIMIT]]
    if isinstance(value, str):
        return _compact_text(value, _INTAKE_TEXT_LIMIT)
    return value


def _compact_list(values: list[str], *, limit: int = _INTAKE_LIST_LIMIT) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _compact_text(value, _INTAKE_LIST_ITEM_LIMIT)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
        if len(output) == limit:
            break
    return output


def _compact_text(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _collect_nonempty_paths(value: Any, prefix: str, output: set[str]) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            _collect_nonempty_paths(child, path, output)
        return
    if isinstance(value, list):
        if any(_nonempty(item) for item in value):
            output.add(prefix)
        return
    if _nonempty(value):
        output.add(prefix)


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True

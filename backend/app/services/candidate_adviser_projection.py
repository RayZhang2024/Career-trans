import hashlib
import json
from typing import Any

from pydantic import BaseModel

from app.schemas.candidate import CandidateAdviserContext
from app.schemas.candidate_adviser import CandidateAdviserAssessment, CandidateIntakeProfileData


def candidate_adviser_input_fingerprint(
    *,
    structured_profile_json: str,
    evidence_fingerprints: list[str],
    intake: CandidateIntakeProfileData,
) -> str:
    payload = {
        "structured_profile": json.loads(structured_profile_json),
        "evidence_fingerprints": sorted(evidence_fingerprints),
        "intake": intake.model_dump(mode="json"),
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

"""Canonical snapshot fixtures for provider-free downstream service tests."""

from datetime import datetime, timezone

from app.schemas.candidate import (
    CandidateContext,
    CandidateEvidenceMaterializationStatus,
)
from app.schemas.candidate_profile import CandidateProfileRead
from app.schemas.candidate_read_snapshot import (
    CandidateReadiness,
    CanonicalCandidateReadSnapshot,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService


def snapshot_for_context(
    context: CandidateContext,
    *,
    structured_profile_available: bool = True,
    evidence_status: CandidateEvidenceMaterializationStatus = CandidateEvidenceMaterializationStatus.COMPLETE,
) -> CanonicalCandidateReadSnapshot:
    now = datetime.now(timezone.utc)
    profile = CandidateProfileRead(
        id="test-profile",
        user_id="test-user",
        summary=context.profile_text or None,
        career_goal=context.career_strategy_text or None,
        job_search_criteria=context.job_search_criteria_text or None,
        created_at=now,
        updated_at=now,
    )
    structured = CandidateCVData(
        skills=[{"name": skill.strip()} for skill in context.skills_text.split(",") if skill.strip()]
    )
    missing = 1 if evidence_status is CandidateEvidenceMaterializationStatus.INCOMPLETE else 0
    readiness = CandidateReadiness(
        structured_profile_available=structured_profile_available,
        ready_for_candidate_context=(
            structured_profile_available
            and evidence_status is not CandidateEvidenceMaterializationStatus.INCOMPLETE
        ),
        evidence_materialization_status=evidence_status,
        expected_evidence_count=len(context.evidence) + missing,
        materialized_evidence_count=len(context.evidence),
        missing_evidence_count=missing,
        stale_evidence_count=0,
    )
    return CanonicalCandidateReadSnapshot(
        profile=profile,
        structured_profile=structured if structured_profile_available else None,
        active_evidence=context.evidence,
        eligibility=context.eligibility,
        readiness=readiness,
    )


class StaticCandidateReader(CanonicalCandidateReadService):
    """Injectable canonical-reader test double returning typed snapshots."""

    def __init__(self, snapshot: CanonicalCandidateReadSnapshot) -> None:
        self.snapshot = snapshot
        self.read_user_ids: list[str] = []

    def read(self, user_id: str) -> CanonicalCandidateReadSnapshot:
        self.read_user_ids.append(user_id)
        return self.snapshot


def patch_canonical_snapshot(monkeypatch, snapshot: CanonicalCandidateReadSnapshot) -> None:
    """Patch the canonical boundary while retaining its real context projection."""

    monkeypatch.setattr(CanonicalCandidateReadService, "read", lambda _self, _user_id: snapshot)


def patch_candidate_context(monkeypatch, context: CandidateContext, **snapshot_options) -> None:
    patch_canonical_snapshot(monkeypatch, snapshot_for_context(context, **snapshot_options))

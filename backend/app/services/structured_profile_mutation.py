"""Shared canonical structured-profile persistence and Adviser attribution."""

import json

from sqlalchemy.orm import Session

from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredItemSourceKind,
    StructuredProfileItemLineageInput,
    StructuredProfileSection,
)
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_structured_item_lineage import CandidateStructuredItemLineageService
from app.services.structured_profile_comparison import StructuredProfileComparisonService
from app.services.structured_profile_identity import structured_profile_item_fingerprint
from app.services.structured_profile_lineage_transitions import StructuredProfileLineageTransitionAnalyzer


def persist_structured_profile(
    session: Session,
    user_id: str,
    row: CandidateStructuredProfile | None,
    data: CandidateCVData,
) -> CandidateStructuredProfile:
    """Persist canonical structured data and reconcile its derived evidence."""
    encoded = json.dumps(data.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if row is None:
        row = CandidateStructuredProfile(user_id=user_id, structured_json=encoded)
        session.add(row)
    else:
        row.structured_json = encoded
    ActiveCandidateEvidenceResolver(session).resolve(user_id, data)
    return row


def adviser_lineage_event(
    before: CandidateCVData | None,
    after: CandidateCVData,
    section: StructuredProfileSection,
    proposed_item,
    *,
    source_ref: str,
) -> StructuredProfileItemLineageInput | None:
    """Attribute the surviving canonical item to its Adviser proposal."""
    incoming_fingerprint = structured_profile_item_fingerprint(section, proposed_item)
    items = getattr(after, section.value)
    survivor = next((
        item for item in items
        if structured_profile_item_fingerprint(section, item) == incoming_fingerprint
    ), None)
    if survivor is None:
        supported = [
            item for item in items
            if StructuredProfileComparisonService().compare(section, proposed_item, [item]).relationship
            is StructuredItemRelationship.REINFORCEMENT
        ]
        if len(supported) == 1:
            survivor = supported[0]
    if survivor is None:
        return None
    transition = StructuredProfileLineageTransitionAnalyzer().analyze_item(before, section, survivor)
    return StructuredProfileItemLineageInput(
        section=transition.section,
        item=transition.item,
        source_kind=StructuredItemSourceKind.CANDIDATE_ADVISER,
        source_ref=source_ref,
        relationship=transition.relationship,
        predecessor_item=transition.predecessor_item,
    )


def stage_adviser_lineage(
    session: Session,
    user_id: str,
    before: CandidateCVData | None,
    after: CandidateCVData,
    section: StructuredProfileSection,
    proposed_item,
    *,
    source_ref: str,
) -> None:
    event = adviser_lineage_event(before, after, section, proposed_item, source_ref=source_ref)
    if event is not None:
        CandidateStructuredItemLineageService(session).stage_many(user_id, [event])

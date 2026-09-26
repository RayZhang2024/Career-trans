"""Provider-free, read-only source history for current structured Profile items."""

import json
from collections import deque
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_adviser import CandidateAdviserClarificationRecord
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.candidate_structured_item_lineage import CandidateStructuredItemLineageRecord
from app.schemas.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalUpdate
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredItemSourceKind,
    StructuredProfileSection,
    typed_structured_item,
)
from app.schemas.structured_profile_provenance import (
    CVStructuredProfileSource,
    CandidateAdviserStructuredSource,
    ManualProfileStructuredSource,
    StructuredProfileCurrentItemProvenanceRead,
    StructuredProfileHistoricalLineageEventRead,
    StructuredProfileLineageEventRead,
    StructuredProfileProvenanceRead,
)
from app.services.structured_profile_identity import structured_profile_item_fingerprint


MAX_PREDECESSOR_DEPTH = 20
MAX_LINEAGE_EVENTS_PER_ITEM = 100
_PROPOSAL_UPDATE = TypeAdapter(CandidateAdviserProfileProposalUpdate)


class StructuredProfileProvenanceService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._source_cache: dict[tuple[StructuredItemSourceKind, str], Any] = {}

    def read_current(
        self, user_id: str, current: CandidateCVData | None
    ) -> StructuredProfileProvenanceRead:
        with self._session.no_autoflush:
            return self._read_current_projection(user_id, current)

    def _read_current_projection(
        self, user_id: str, current: CandidateCVData | None
    ) -> StructuredProfileProvenanceRead:
        data = current or CandidateCVData()
        items: list[StructuredProfileCurrentItemProvenanceRead] = []
        for section in StructuredProfileSection:
            for item_index, item in enumerate(getattr(data, section.value)):
                fingerprint = structured_profile_item_fingerprint(section, item)
                direct_rows = self._events(user_id, section, fingerprint)
                direct_rows = direct_rows[:MAX_LINEAGE_EVENTS_PER_ITEM]
                direct = [self._event(user_id, row) for row in direct_rows]
                history: list[StructuredProfileHistoricalLineageEventRead] = []
                visited_event_ids = {row.id for row in direct_rows}
                visited_fingerprints = {fingerprint}
                queue: deque[tuple[str, int]] = deque(
                    (row.predecessor_fingerprint, 1)
                    for row in direct_rows if row.predecessor_fingerprint is not None
                )
                while queue and len(direct) + len(history) < MAX_LINEAGE_EVENTS_PER_ITEM:
                    predecessor_fp, depth = queue.popleft()
                    if depth > MAX_PREDECESSOR_DEPTH or predecessor_fp in visited_fingerprints:
                        continue
                    visited_fingerprints.add(predecessor_fp)
                    for row in self._events(user_id, section, predecessor_fp):
                        if row.id in visited_event_ids:
                            continue
                        visited_event_ids.add(row.id)
                        history.append(StructuredProfileHistoricalLineageEventRead(
                            depth=depth, lineage_event=self._event(user_id, row)
                        ))
                        if row.predecessor_fingerprint is not None and depth < MAX_PREDECESSOR_DEPTH:
                            queue.append((row.predecessor_fingerprint, depth + 1))
                        if len(direct) + len(history) >= MAX_LINEAGE_EVENTS_PER_ITEM:
                            break
                items.append(StructuredProfileCurrentItemProvenanceRead(
                    section=section,
                    item_index=item_index,
                    item=item,
                    item_fingerprint=fingerprint,
                    direct_events=direct,
                    history=history,
                    source_history_available=bool(direct or history),
                ))
        return StructuredProfileProvenanceRead(items=items)

    def _events(self, user_id: str, section: StructuredProfileSection, fingerprint: str):
        with self._session.no_autoflush:
            return self._session.scalars(
                select(CandidateStructuredItemLineageRecord)
                .where(
                    CandidateStructuredItemLineageRecord.user_id == user_id,
                    CandidateStructuredItemLineageRecord.section == section.value,
                    CandidateStructuredItemLineageRecord.item_fingerprint == fingerprint,
                )
                .order_by(CandidateStructuredItemLineageRecord.created_at, CandidateStructuredItemLineageRecord.id)
                .execution_options(populate_existing=True)
            ).all()

    def _event(self, user_id: str, row: CandidateStructuredItemLineageRecord):
        section = StructuredProfileSection(row.section)
        item = typed_structured_item(section, json.loads(row.item_json))
        predecessor = (
            typed_structured_item(section, json.loads(row.predecessor_item_json))
            if row.predecessor_item_json is not None else None
        )
        if structured_profile_item_fingerprint(section, item) != row.item_fingerprint:
            raise ValueError("Persisted lineage item fingerprint is invalid.")
        if (predecessor is None) != (row.predecessor_fingerprint is None):
            raise ValueError("Persisted lineage predecessor is invalid.")
        if predecessor is not None and structured_profile_item_fingerprint(section, predecessor) != row.predecessor_fingerprint:
            raise ValueError("Persisted lineage predecessor fingerprint is invalid.")
        source_kind = StructuredItemSourceKind(row.source_kind)
        return StructuredProfileLineageEventRead(
            event_id=row.id,
            section=section,
            item_fingerprint=row.item_fingerprint,
            item=item,
            source_kind=source_kind,
            relationship=StructuredItemRelationship(row.relationship),
            predecessor_fingerprint=row.predecessor_fingerprint,
            predecessor_item=predecessor,
            created_at=row.created_at,
            source=self._resolve_source(user_id, source_kind, row.source_ref),
        )

    def _resolve_source(self, user_id: str, kind: StructuredItemSourceKind, source_ref: str):
        key = (kind, source_ref)
        if key in self._source_cache:
            return self._source_cache[key]
        if kind is StructuredItemSourceKind.CV:
            row = self._session.scalar(select(CandidateCVIngestionDraft).where(
                CandidateCVIngestionDraft.id == source_ref,
                CandidateCVIngestionDraft.user_id == user_id,
            ))
            filenames: list[str] = []
            if row is not None:
                try:
                    documents = json.loads(row.documents_json)
                    filenames = [
                        str(document["provenance"]["filename"])
                        for document in documents
                        if isinstance(document, dict)
                        and isinstance(document.get("provenance"), dict)
                        and isinstance(document["provenance"].get("filename"), str)
                    ]
                except (TypeError, ValueError, KeyError):
                    filenames = []
            descriptor = CVStructuredProfileSource(
                kind="cv", source_id=source_ref, filenames=filenames,
                source_state=row.state if row is not None else None,
                source_created_at=row.created_at if row is not None else None,
                source_updated_at=row.updated_at if row is not None else None,
                available=row is not None,
            )
        elif kind is StructuredItemSourceKind.MANUAL_PROFILE:
            row = self._session.scalar(select(CandidateProfileRevisionRecord).where(
                CandidateProfileRevisionRecord.id == source_ref,
                CandidateProfileRevisionRecord.user_id == user_id,
            ))
            confirmed = row is not None and row.state == "confirmed" and row.confirmed_at is not None
            descriptor = ManualProfileStructuredSource(
                kind="manual_profile", source_id=source_ref,
                confirmed_at=row.confirmed_at if confirmed else None,
                available=confirmed,
            )
        else:
            row = self._session.scalar(select(CandidateAdviserProfileProposalRecord).where(
                CandidateAdviserProfileProposalRecord.id == source_ref,
                CandidateAdviserProfileProposalRecord.user_id == user_id,
            ))
            clarification = None
            proposal_item = None
            if row is not None:
                clarification = self._session.scalar(select(CandidateAdviserClarificationRecord).where(
                    CandidateAdviserClarificationRecord.user_id == user_id,
                    CandidateAdviserClarificationRecord.clarification_id == row.source_clarification_id,
                ))
                try:
                    proposal_item = _PROPOSAL_UPDATE.validate_json(row.proposed_update_json).item
                except (ValidationError, TypeError, ValueError):
                    proposal_item = None
            descriptor = CandidateAdviserStructuredSource(
                kind="candidate_adviser", source_id=source_ref,
                source_clarification_id=row.source_clarification_id if row is not None else None,
                clarification_question=clarification.question_text if clarification is not None else None,
                proposal_item=proposal_item,
                transferred_at=row.transferred_at if row is not None else None,
                available=row is not None,
            )
        self._source_cache[key] = descriptor
        return descriptor

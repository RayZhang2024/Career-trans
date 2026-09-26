import json
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.candidate_structured_item_lineage import CandidateStructuredItemLineageRecord
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredProfileItem,
    StructuredProfileItemLineageInput,
    StructuredProfileLineageRead,
    StructuredProfileSection,
    typed_structured_item,
)
from app.services.structured_profile_identity import (
    structured_item_lineage_key,
    structured_profile_item_fingerprint,
)
from app.services.structured_profile_comparison import StructuredProfileComparisonService


class CandidateStructuredItemLineageConflict(ValueError):
    """A purported historical item does not agree with its claimed identity."""


class CandidateStructuredItemLineageService:
    """Provider-free history storage/read service, separate from current authority."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        user_id: str,
        event: StructuredProfileItemLineageInput,
    ) -> StructuredProfileLineageRead:
        result = self.stage(user_id, event)
        self._session.commit()
        persisted = self._session.scalar(
            select(CandidateStructuredItemLineageRecord)
            .where(CandidateStructuredItemLineageRecord.id == result.id)
            .execution_options(populate_existing=True)
        )
        if persisted is None:
            raise CandidateStructuredItemLineageConflict("The staged lineage event was not persisted.")
        return self._read(persisted)

    def stage(
        self,
        user_id: str,
        event: StructuredProfileItemLineageInput,
    ) -> StructuredProfileLineageRead:
        """Stage one validated event in the caller's transaction; never commit."""
        return self.stage_many(user_id, [event])[0]

    def stage_many(
        self,
        user_id: str,
        events: list[StructuredProfileItemLineageInput],
    ) -> list[StructuredProfileLineageRead]:
        """Stage validated events atomically into an existing caller-owned transaction."""
        if not user_id:
            raise ValueError("user_id is required.")
        prepared: list[
            tuple[
                str,
                StructuredProfileItemLineageInput,
                StructuredProfileItem,
                StructuredProfileItem | None,
                str,
                str | None,
            ]
        ] = []
        seen: set[str] = set()
        for raw_event in events:
            try:
                event = StructuredProfileItemLineageInput.model_validate(raw_event)
                item = typed_structured_item(event.section, event.item)
                predecessor = (
                    typed_structured_item(event.section, event.predecessor_item)
                    if event.predecessor_item is not None else None
                )
                item_fingerprint = structured_profile_item_fingerprint(event.section, item)
                predecessor_fingerprint = (
                    structured_profile_item_fingerprint(event.section, predecessor)
                    if predecessor is not None else None
                )
                if (
                    event.relationship is StructuredItemRelationship.REINFORCEMENT
                    and predecessor_fingerprint is not None
                    and predecessor_fingerprint != item_fingerprint
                ):
                    comparison = StructuredProfileComparisonService().compare(
                        event.section, item, [predecessor]
                    )
                    if (
                        comparison.relationship is not StructuredItemRelationship.REINFORCEMENT
                        or comparison.current_item is None
                        or comparison.target_fingerprint != predecessor_fingerprint
                        or len(comparison.candidate_matches) != 1
                        or comparison.candidate_matches[0].fingerprint != predecessor_fingerprint
                    ):
                        raise ValueError(
                            "A reinforcement predecessor with a different exact fingerprint "
                            "must be the unique normalized-equivalent comparator target."
                        )
                if (
                    event.relationship is StructuredItemRelationship.AMBIGUOUS
                    and predecessor is not None
                ):
                    comparison = StructuredProfileComparisonService().compare(
                        event.section, item, [predecessor]
                    )
                    if (
                        comparison.relationship is StructuredItemRelationship.NEW
                        or comparison.current_item is None
                        or comparison.target_fingerprint != predecessor_fingerprint
                        or len(comparison.candidate_matches) != 1
                    ):
                        raise ValueError(
                            "A selected ambiguous predecessor must be a deterministic overlap target."
                        )
                key = structured_item_lineage_key(
                    user_id=user_id,
                    section=event.section,
                    item_fingerprint=item_fingerprint,
                    source_kind=event.source_kind.value,
                    source_ref=event.source_ref,
                    relationship=event.relationship.value,
                    predecessor_fingerprint=predecessor_fingerprint,
                )
            except (ValidationError, TypeError, ValueError) as exc:
                raise CandidateStructuredItemLineageConflict(str(exc)) from exc
            if key not in seen:
                prepared.append((key, event, item, predecessor, item_fingerprint, predecessor_fingerprint))
                seen.add(key)

        results: list[StructuredProfileLineageRead] = []
        for key, event, item, predecessor, item_fingerprint, predecessor_fingerprint in prepared:
            existing = self._session.scalar(
                select(CandidateStructuredItemLineageRecord)
                .where(
                    CandidateStructuredItemLineageRecord.user_id == user_id,
                    CandidateStructuredItemLineageRecord.lineage_key == key,
                )
                .execution_options(populate_existing=True)
            )
            if existing is not None:
                results.append(self._read(existing))
                continue

            row = CandidateStructuredItemLineageRecord(
                user_id=user_id,
                lineage_key=key,
                section=event.section.value,
                item_fingerprint=item_fingerprint,
                item_json=json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                source_kind=event.source_kind.value,
                source_ref=event.source_ref,
                relationship=event.relationship.value,
                predecessor_fingerprint=predecessor_fingerprint,
                predecessor_item_json=(
                    json.dumps(predecessor.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    if predecessor is not None else None
                ),
            )
            try:
                # The nested transaction isolates a uniqueness race from other
                # canonical mutations already staged by the caller.
                self._ensure_outer_sqlite_transaction()
                with self._session.begin_nested():
                    self._session.add(row)
                    self._session.flush([row])
            except IntegrityError:
                winner = self._session.scalar(
                    select(CandidateStructuredItemLineageRecord)
                    .where(
                        CandidateStructuredItemLineageRecord.user_id == user_id,
                        CandidateStructuredItemLineageRecord.lineage_key == key,
                    )
                    .execution_options(populate_existing=True)
                )
                if winner is None:
                    raise
                results.append(self._read(winner))
            else:
                results.append(self._read(row))
        return results

    def _ensure_outer_sqlite_transaction(self) -> None:
        """Keep SQLite SAVEPOINTs nested in the caller's transaction.

        The sqlite driver may not emit BEGIN for a logical SQLAlchemy transaction
        until the first write. Releasing a SAVEPOINT created before that point
        commits it, defeating the caller's ability to roll back staged events.
        """
        connection = self._session.connection()
        if connection.dialect.name != "sqlite":
            return
        driver_connection = connection.connection.driver_connection
        if not driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")

    def read_history(self, user_id: str) -> list[StructuredProfileLineageRead]:
        with self._session.no_autoflush:
            rows = self._session.scalars(
                select(CandidateStructuredItemLineageRecord)
                .where(CandidateStructuredItemLineageRecord.user_id == user_id)
                .order_by(CandidateStructuredItemLineageRecord.created_at, CandidateStructuredItemLineageRecord.id)
                .execution_options(populate_existing=True)
            ).all()
        return [self._read(row) for row in rows]

    def read_current(
        self,
        user_id: str,
        data: CandidateCVData,
    ) -> dict[StructuredProfileSection, list[StructuredProfileLineageRead]]:
        """Attach history only to exact item fingerprints in supplied current state."""
        result: dict[StructuredProfileSection, list[StructuredProfileLineageRead]] = {}
        for section in StructuredProfileSection:
            fingerprints = {
                structured_profile_item_fingerprint(section, item)
                for item in getattr(data, section.value)
            }
            if not fingerprints:
                result[section] = []
                continue
            with self._session.no_autoflush:
                rows = self._session.scalars(
                    select(CandidateStructuredItemLineageRecord)
                    .where(
                        CandidateStructuredItemLineageRecord.user_id == user_id,
                        CandidateStructuredItemLineageRecord.section == section.value,
                        CandidateStructuredItemLineageRecord.item_fingerprint.in_(fingerprints),
                    )
                    .order_by(CandidateStructuredItemLineageRecord.created_at, CandidateStructuredItemLineageRecord.id)
                    .execution_options(populate_existing=True)
                ).all()
            result[section] = [self._read(row) for row in rows]
        return result

    @staticmethod
    def _read(row: CandidateStructuredItemLineageRecord) -> StructuredProfileLineageRead:
        section = StructuredProfileSection(row.section)
        item = typed_structured_item(section, json.loads(row.item_json))
        predecessor = (
            typed_structured_item(section, json.loads(row.predecessor_item_json))
            if row.predecessor_item_json is not None else None
        )
        if structured_profile_item_fingerprint(section, item) != row.item_fingerprint:
            raise CandidateStructuredItemLineageConflict("Persisted item snapshot does not match its fingerprint.")
        if (predecessor is None) != (row.predecessor_fingerprint is None):
            raise CandidateStructuredItemLineageConflict("Persisted predecessor snapshot and fingerprint disagree.")
        if predecessor is not None and structured_profile_item_fingerprint(section, predecessor) != row.predecessor_fingerprint:
            raise CandidateStructuredItemLineageConflict("Persisted predecessor does not match its fingerprint.")
        return StructuredProfileLineageRead(
            id=row.id, user_id=row.user_id, lineage_key=row.lineage_key,
            section=section, item_fingerprint=row.item_fingerprint, item=item,
            source_kind=row.source_kind, source_ref=row.source_ref,
            relationship=row.relationship, predecessor_fingerprint=row.predecessor_fingerprint,
            predecessor_item=predecessor, created_at=row.created_at or datetime.min,
        )

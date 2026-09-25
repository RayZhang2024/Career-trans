import hashlib
import json
from datetime import datetime, timezone

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models.candidate_adviser import CandidateAdviserClarificationRecord
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.schemas.candidate_adviser import (
    CandidateAdviserClarificationStatus,
    ClarificationAnswerKind,
    ClarificationInterpretation,
)
from app.schemas.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalRead,
    CandidateAdviserProfileProposalState,
    CandidateAdviserProfileProposalUpdate,
    StructuredProfileSection,
)

_UPDATE_ADAPTER = TypeAdapter(CandidateAdviserProfileProposalUpdate)


class CandidateAdviserProfileProposalNotFound(LookupError):
    """No proposal with this ID is owned by the authenticated user."""


class CandidateAdviserProfileProposalConflict(ValueError):
    """The proposal state or expected revision does not permit the operation."""


def structured_profile_item_fingerprint(
    section: StructuredProfileSection | str,
    item: BaseModel,
) -> str:
    """Hash exact typed item identity, including its structured section."""
    canonical_section = StructuredProfileSection(section).value
    canonical = json.dumps(
        {
            "section": canonical_section,
            "item": item.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class CandidateAdviserProfileProposalService:
    """Provider-free persistence and lifecycle for source-linked proposals."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def materialize_from_confirmed_clarification(
        self,
        user_id: str,
        clarification_id: str,
        update: CandidateAdviserProfileProposalUpdate,
    ) -> CandidateAdviserProfileProposalRead:
        source = self._confirmed_source(user_id, clarification_id)
        typed_update = self._validate_update(update)
        original_json = _canonical_json(typed_update.model_dump(mode="json"))
        key_payload = {
            "source_clarification_id": source.clarification_id,
            "source_assessment_fingerprint": source.origin_assessment_fingerprint,
            "original_update": typed_update.model_dump(mode="json"),
        }
        proposal_key = hashlib.sha256(_canonical_json(key_payload).encode("utf-8")).hexdigest()
        existing = self._session.scalar(
            select(CandidateAdviserProfileProposalRecord).where(
                CandidateAdviserProfileProposalRecord.user_id == user_id,
                CandidateAdviserProfileProposalRecord.proposal_key == proposal_key,
            )
        )
        if existing is not None:
            return self._read(existing)

        record = CandidateAdviserProfileProposalRecord(
            user_id=user_id,
            proposal_key=proposal_key,
            state=CandidateAdviserProfileProposalState.PENDING,
            revision=1,
            source_clarification_id=source.clarification_id,
            source_assessment_fingerprint=source.origin_assessment_fingerprint,
            original_update_json=original_json,
            proposed_update_json=original_json,
        )
        self._session.add(record)
        try:
            self._session.commit()
            self._session.refresh(record)
        except IntegrityError:
            self._session.rollback()
            existing = self._session.scalar(
                select(CandidateAdviserProfileProposalRecord).where(
                    CandidateAdviserProfileProposalRecord.user_id == user_id,
                    CandidateAdviserProfileProposalRecord.proposal_key == proposal_key,
                )
            )
            if existing is None:
                raise
            return self._read(existing)
        return self._read(record)

    def list_for_user(
        self, user_id: str, *, limit: int = 20
    ) -> list[CandidateAdviserProfileProposalRead]:
        if not 1 <= limit <= 100:
            raise ValueError("Proposal history limit must be between 1 and 100.")
        records = self._session.scalars(
            select(CandidateAdviserProfileProposalRecord)
            .where(CandidateAdviserProfileProposalRecord.user_id == user_id)
            .order_by(
                CandidateAdviserProfileProposalRecord.created_at.desc(),
                CandidateAdviserProfileProposalRecord.id.desc(),
            )
            .limit(limit)
        ).all()
        return [self._read(record) for record in records]

    def get_for_user(
        self, user_id: str, proposal_id: str
    ) -> CandidateAdviserProfileProposalRead:
        return self._read(self._owned_record(user_id, proposal_id))

    def edit_pending(
        self,
        user_id: str,
        proposal_id: str,
        *,
        expected_revision: int,
        proposed_update: CandidateAdviserProfileProposalUpdate,
    ) -> CandidateAdviserProfileProposalRead:
        record = self._owned_record(user_id, proposal_id)
        if record.state != CandidateAdviserProfileProposalState.PENDING:
            raise CandidateAdviserProfileProposalConflict(
                "Only a pending proposal can be edited."
            )
        self._expect_revision(record, expected_revision)
        self._confirmed_source(user_id, record.source_clarification_id)
        typed_update = self._validate_update(proposed_update)
        original_section = json.loads(record.original_update_json)["section"]
        if typed_update.section != original_section:
            raise CandidateAdviserProfileProposalConflict(
                "A proposal edit must remain within its original structured section."
            )
        record.proposed_update_json = _canonical_json(typed_update.model_dump(mode="json"))
        record.revision += 1
        try:
            self._session.commit()
            self._session.refresh(record)
        except StaleDataError as exc:
            self._session.rollback()
            raise CandidateAdviserProfileProposalConflict(
                "Proposal revision conflict: another request changed this proposal."
            ) from exc
        except Exception:
            self._session.rollback()
            raise
        return self._read(record)

    def reject_pending(
        self,
        user_id: str,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> CandidateAdviserProfileProposalRead:
        record = self._owned_record(user_id, proposal_id)
        if record.state == CandidateAdviserProfileProposalState.REJECTED:
            return self._read(record)
        self._expect_revision(record, expected_revision)
        record.state = CandidateAdviserProfileProposalState.REJECTED
        record.rejected_at = datetime.now(timezone.utc)
        record.revision += 1
        try:
            self._session.commit()
            self._session.refresh(record)
        except StaleDataError as exc:
            self._session.rollback()
            raise CandidateAdviserProfileProposalConflict(
                "Proposal revision conflict: another request changed this proposal."
            ) from exc
        except Exception:
            self._session.rollback()
            raise
        return self._read(record)

    def _confirmed_source(
        self, user_id: str, clarification_id: str
    ) -> CandidateAdviserClarificationRecord:
        source = self._session.scalar(
            select(CandidateAdviserClarificationRecord).where(
                CandidateAdviserClarificationRecord.user_id == user_id,
                CandidateAdviserClarificationRecord.clarification_id == clarification_id,
            )
        )
        if source is None:
            raise CandidateAdviserProfileProposalNotFound("Confirmed clarification not found.")
        if (
            source.status != CandidateAdviserClarificationStatus.CONFIRMED
            or source.interpretation_json is None
        ):
            raise CandidateAdviserProfileProposalConflict(
                "A confirmed, interpreted clarification is required as proposal source."
            )
        interpretation = ClarificationInterpretation.model_validate_json(
            source.interpretation_json
        )
        if interpretation.answer_kind not in {
            ClarificationAnswerKind.CAREER_FACT,
            ClarificationAnswerKind.MIXED,
        }:
            raise CandidateAdviserProfileProposalConflict(
                "Only career-fact or mixed clarifications can source Profile proposals."
            )
        return source

    def _owned_record(
        self, user_id: str, proposal_id: str
    ) -> CandidateAdviserProfileProposalRecord:
        record = self._session.scalar(
            select(CandidateAdviserProfileProposalRecord).where(
                CandidateAdviserProfileProposalRecord.id == proposal_id,
                CandidateAdviserProfileProposalRecord.user_id == user_id,
            )
        )
        if record is None:
            raise CandidateAdviserProfileProposalNotFound("Profile proposal not found.")
        return record

    @staticmethod
    def _expect_revision(
        record: CandidateAdviserProfileProposalRecord, expected_revision: int
    ) -> None:
        if record.revision != expected_revision:
            raise CandidateAdviserProfileProposalConflict(
                f"Proposal version conflict: expected {expected_revision}, current version is {record.revision}."
            )

    @staticmethod
    def _validate_update(
        update: CandidateAdviserProfileProposalUpdate,
    ) -> CandidateAdviserProfileProposalUpdate:
        return _UPDATE_ADAPTER.validate_python(update)

    @staticmethod
    def _read(
        record: CandidateAdviserProfileProposalRecord,
    ) -> CandidateAdviserProfileProposalRead:
        return CandidateAdviserProfileProposalRead(
            id=record.id,
            state=record.state,
            revision=record.revision,
            source_clarification_id=record.source_clarification_id,
            source_assessment_fingerprint=record.source_assessment_fingerprint,
            original_update=_UPDATE_ADAPTER.validate_json(record.original_update_json),
            proposed_update=_UPDATE_ADAPTER.validate_json(record.proposed_update_json),
            created_at=record.created_at,
            updated_at=record.updated_at,
            rejected_at=record.rejected_at,
        )

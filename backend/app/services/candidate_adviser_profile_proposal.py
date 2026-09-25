import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models.candidate_adviser import CandidateAdviserClarificationRecord
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.schemas.candidate_adviser import (
    CandidateAdviserClarificationStatus,
    ClarificationAnswerKind,
    ClarificationInterpretation,
)
from app.schemas.candidate_adviser_profile_proposal import (
    ConfirmedClarificationProposalSource,
    CandidateAdviserProfileProposalRead,
    CandidateAdviserProfileProposalTransferRead,
    CandidateAdviserProfileProposalState,
    CandidateAdviserProfileProposalUpdate,
    StructuredProfileProposalTargetCatalogue,
    AchievementProposalTarget,
    CredentialProposalTarget,
    EducationProposalTarget,
    EmploymentProposalTarget,
    ProjectProposalTarget,
    SkillProposalTarget,
    StructuredProfileSection,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.profile_revision import EditableCandidateStructuredData
from app.services.profile_revision_service import (
    CandidateProfileRevisionService,
    ProfileRevisionConflict,
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
        return self.materialize_batch_from_confirmed_clarification(
            user_id, clarification_id, [update]
        )[0]

    def materialize_batch_from_confirmed_clarification(
        self,
        user_id: str,
        clarification_id: str,
        updates: list[CandidateAdviserProfileProposalUpdate],
    ) -> list[CandidateAdviserProfileProposalRead]:
        """Validate and persist a complete generated batch in one commit."""
        source = self._confirmed_source(user_id, clarification_id)
        prepared: list[tuple[str, str]] = []
        for update in updates:
            typed_update = self._validate_update(update)
            update_json = _canonical_json(typed_update.model_dump(mode="json"))
            key_payload = {
                "source_clarification_id": source.clarification_id,
                "source_assessment_fingerprint": source.origin_assessment_fingerprint,
                "original_update": typed_update.model_dump(mode="json"),
            }
            proposal_key = hashlib.sha256(_canonical_json(key_payload).encode("utf-8")).hexdigest()
            prepared.append((proposal_key, update_json))

        keys = [key for key, _ in prepared]
        if len(set(keys)) != len(keys):
            raise CandidateAdviserProfileProposalConflict(
                "A generated batch cannot contain duplicate proposal updates."
            )
        existing_rows = self._session.scalars(
            select(CandidateAdviserProfileProposalRecord).where(
                CandidateAdviserProfileProposalRecord.user_id == user_id,
                CandidateAdviserProfileProposalRecord.proposal_key.in_(keys),
            )
        ).all() if keys else []
        existing_by_key = {row.proposal_key: row for row in existing_rows}
        records: list[CandidateAdviserProfileProposalRecord] = []
        new_records: list[CandidateAdviserProfileProposalRecord] = []
        for proposal_key, update_json in prepared:
            existing = existing_by_key.get(proposal_key)
            if existing is not None:
                records.append(existing)
                continue
            record = CandidateAdviserProfileProposalRecord(
                user_id=user_id,
                proposal_key=proposal_key,
                state=CandidateAdviserProfileProposalState.PENDING,
                revision=1,
                source_clarification_id=source.clarification_id,
                source_assessment_fingerprint=source.origin_assessment_fingerprint,
                original_update_json=update_json,
                proposed_update_json=update_json,
            )
            records.append(record)
            new_records.append(record)
        if not new_records:
            return [self._read(record) for record in records]

        self._session.add_all(new_records)
        try:
            self._session.commit()
            for record in new_records:
                self._session.refresh(record)
        except IntegrityError:
            self._session.rollback()
            recovered = self._session.scalars(
                select(CandidateAdviserProfileProposalRecord).where(
                    CandidateAdviserProfileProposalRecord.user_id == user_id,
                    CandidateAdviserProfileProposalRecord.proposal_key.in_(keys),
                )
            ).all() if keys else []
            recovered_by_key = {row.proposal_key: row for row in recovered}
            if not all(key in recovered_by_key for key in keys):
                raise CandidateAdviserProfileProposalConflict(
                    "A concurrent proposal batch changed the idempotency set; retry generation."
                )
            return [self._read(recovered_by_key[key]) for key in keys]
        return [self._read(record) for record in records]

    def generation_source(
        self, user_id: str, clarification_id: str, *, fresh: bool = False
    ) -> tuple[CandidateAdviserClarificationRecord, ConfirmedClarificationProposalSource]:
        """Return only the confirmed, affirmative facts allowed into generation."""
        record = self._confirmed_source(user_id, clarification_id, fresh=fresh)
        interpretation = ClarificationInterpretation.model_validate_json(record.interpretation_json)
        source = ConfirmedClarificationProposalSource(
            clarification_id=record.clarification_id,
            question_text=record.question_text[:1_200],
            confirmed_context_summary=interpretation.confirmed_context_summary[:1_200],
            proposed_evidence=interpretation.proposed_evidence,
        )
        return record, source

    def target_catalogue(
        self, user_id: str, *, fresh: bool = False
    ) -> StructuredProfileProposalTargetCatalogue:
        """Build a deterministic, bounded catalogue without exposing CV evidence."""
        data = self._structured_data(user_id, fresh=fresh)
        mappings = (
            (StructuredProfileSection.EMPLOYMENT, data.employment, EmploymentProposalTarget),
            (StructuredProfileSection.EDUCATION, data.education, EducationProposalTarget),
            (StructuredProfileSection.CREDENTIALS, data.credentials, CredentialProposalTarget),
            (StructuredProfileSection.SKILLS, data.skills, SkillProposalTarget),
            (StructuredProfileSection.PROJECTS, data.projects, ProjectProposalTarget),
            (StructuredProfileSection.ACHIEVEMENTS, data.achievements, AchievementProposalTarget),
        )
        values: dict[str, object] = {}
        truncated: list[StructuredProfileSection] = []
        for section, items, target_model in mappings:
            values[section.value] = [
                target_model(
                    fingerprint=structured_profile_item_fingerprint(section, item),
                    item=item,
                )
                for item in items[:10]
            ]
            if len(items) > 10:
                truncated.append(section)
        values["truncated_sections"] = truncated
        return StructuredProfileProposalTargetCatalogue.model_validate(values)

    def target_fingerprint_counts(
        self, user_id: str, *, fresh: bool = False
    ) -> dict[StructuredProfileSection, Counter[str]]:
        """Count exact targets across full current state, including catalogue omissions."""
        data = self._structured_data(user_id, fresh=fresh)
        sections = (
            (StructuredProfileSection.EMPLOYMENT, data.employment),
            (StructuredProfileSection.EDUCATION, data.education),
            (StructuredProfileSection.CREDENTIALS, data.credentials),
            (StructuredProfileSection.SKILLS, data.skills),
            (StructuredProfileSection.PROJECTS, data.projects),
            (StructuredProfileSection.ACHIEVEMENTS, data.achievements),
        )
        return {
            section: Counter(
                structured_profile_item_fingerprint(section, item) for item in items
            )
            for section, items in sections
        }

    def _structured_data(self, user_id: str, *, fresh: bool) -> CandidateCVData:
        statement = select(CandidateStructuredProfile).where(
            CandidateStructuredProfile.user_id == user_id
        )
        if fresh:
            statement = statement.execution_options(populate_existing=True)
        row = self._session.scalar(statement)
        return CandidateCVData.model_validate_json(row.structured_json) if row else CandidateCVData()

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

    def transfer_to_profile_revision(
        self,
        user_id: str,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> CandidateAdviserProfileProposalTransferRead:
        """Atomically hand a pending Adviser proposal into one #207 draft."""
        record = self._session.scalar(
            select(CandidateAdviserProfileProposalRecord)
            .where(
                CandidateAdviserProfileProposalRecord.id == proposal_id,
                CandidateAdviserProfileProposalRecord.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise CandidateAdviserProfileProposalNotFound("Profile proposal not found.")

        revision_service = CandidateProfileRevisionService(self._session)
        if record.state == CandidateAdviserProfileProposalState.TRANSFERRED:
            return self._existing_transfer_result(record, user_id, revision_service)
        if record.state == CandidateAdviserProfileProposalState.REJECTED:
            raise CandidateAdviserProfileProposalConflict(
                "A rejected Adviser proposal cannot be transferred."
            )
        self._expect_revision(record, expected_revision)

        try:
            source = self._confirmed_source(
                user_id, record.source_clarification_id, fresh=True
            )
        except CandidateAdviserProfileProposalNotFound as exc:
            raise CandidateAdviserProfileProposalConflict(
                "The Adviser proposal's confirmed source clarification is unavailable."
            ) from exc
        if source.origin_assessment_fingerprint != record.source_assessment_fingerprint:
            raise CandidateAdviserProfileProposalConflict(
                "The Adviser proposal source provenance does not match its clarification."
            )

        try:
            update = _UPDATE_ADAPTER.validate_json(record.proposed_update_json)
        except ValidationError as exc:
            raise CandidateAdviserProfileProposalConflict(
                "The persisted proposal update is invalid and cannot be transferred."
            ) from exc

        def apply_to_current(
            current: CandidateCVData | None,
        ) -> EditableCandidateStructuredData:
            current = current or CandidateCVData()
            sections = {
                StructuredProfileSection.EMPLOYMENT: list(current.employment),
                StructuredProfileSection.EDUCATION: list(current.education),
                StructuredProfileSection.CREDENTIALS: list(current.credentials),
                StructuredProfileSection.SKILLS: list(current.skills),
                StructuredProfileSection.PROJECTS: list(current.projects),
                StructuredProfileSection.ACHIEVEMENTS: list(current.achievements),
            }
            target_section = StructuredProfileSection(update.section)
            if update.operation == "add":
                candidate_fingerprint = structured_profile_item_fingerprint(
                    target_section, update.item
                )
                if any(
                    structured_profile_item_fingerprint(target_section, item)
                    == candidate_fingerprint
                    for item in sections[target_section]
                ):
                    raise CandidateAdviserProfileProposalConflict(
                        "An exact matching structured item already exists; the proposal cannot be added."
                    )
                sections[target_section].append(update.item)
            else:
                fingerprint = update.target_fingerprint
                matches: list[tuple[StructuredProfileSection, int]] = []
                for section, items in sections.items():
                    for index, item in enumerate(items):
                        if structured_profile_item_fingerprint(section, item) == fingerprint:
                            matches.append((section, index))
                if len(matches) != 1 or matches[0][0] != target_section:
                    raise CandidateAdviserProfileProposalConflict(
                        "The exact replacement target is missing, duplicated, or in another section."
                    )
                sections[target_section][matches[0][1]] = update.item
            return EditableCandidateStructuredData(
                employment=sections[StructuredProfileSection.EMPLOYMENT],
                education=sections[StructuredProfileSection.EDUCATION],
                credentials=sections[StructuredProfileSection.CREDENTIALS],
                skills=sections[StructuredProfileSection.SKILLS],
                projects=sections[StructuredProfileSection.PROJECTS],
                achievements=sections[StructuredProfileSection.ACHIEVEMENTS],
            )

        try:
            revision = revision_service.stage_new_revision(
                user_id, structured_transform=apply_to_current
            )
            record.state = CandidateAdviserProfileProposalState.TRANSFERRED
            record.transferred_profile_revision_id = revision.id
            record.transferred_at = datetime.now(timezone.utc)
            record.revision += 1
            self._session.commit()
            self._session.refresh(record)
            self._session.refresh(revision)
        except StaleDataError as exc:
            self._session.rollback()
            raise CandidateAdviserProfileProposalConflict(
                "Proposal or Profile revision changed concurrently; retry the transfer."
            ) from exc
        except (IntegrityError, ProfileRevisionConflict) as exc:
            self._session.rollback()
            raise CandidateAdviserProfileProposalConflict(
                "An active Profile draft exists or was created concurrently; finish or discard it before transferring."
            ) from exc
        except Exception:
            self._session.rollback()
            raise

        return CandidateAdviserProfileProposalTransferRead(
            proposal=self._read(record),
            profile_revision=revision_service.read_record(revision, user_id),
        )

    def _existing_transfer_result(
        self,
        record: CandidateAdviserProfileProposalRecord,
        user_id: str,
        revision_service: CandidateProfileRevisionService,
    ) -> CandidateAdviserProfileProposalTransferRead:
        revision_id = record.transferred_profile_revision_id
        if revision_id is None or record.transferred_at is None:
            raise CandidateAdviserProfileProposalConflict(
                "Transferred proposal history has no valid linked Profile revision."
            )
        revision = self._session.scalar(
            select(CandidateProfileRevisionRecord)
            .where(
                CandidateProfileRevisionRecord.id == revision_id,
                CandidateProfileRevisionRecord.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
        if revision is None:
            raise CandidateAdviserProfileProposalConflict(
                "Transferred proposal history has no valid linked Profile revision."
            )
        return CandidateAdviserProfileProposalTransferRead(
            proposal=self._read(record),
            profile_revision=revision_service.read_record(revision, user_id),
        )

    def _confirmed_source(
        self, user_id: str, clarification_id: str, *, fresh: bool = False
    ) -> CandidateAdviserClarificationRecord:
        statement = select(CandidateAdviserClarificationRecord).where(
            CandidateAdviserClarificationRecord.user_id == user_id,
            CandidateAdviserClarificationRecord.clarification_id == clarification_id,
        )
        if fresh:
            statement = statement.execution_options(populate_existing=True)
        source = self._session.scalar(statement)
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
            transferred_at=record.transferred_at,
            transferred_profile_revision_id=record.transferred_profile_revision_id,
        )

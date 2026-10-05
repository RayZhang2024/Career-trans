"""Provider-free persisted manual Profile revision drafts and review gating."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Callable

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.orm import Session

from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.user import User
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalState,
    CandidateAdviserProfileProposalUpdate,
)
from app.schemas.profile_revision import (
    CandidateProfileRevisionRead,
    CandidateProfileRevisionState,
    EditableCandidateProfileData,
    EditableCandidateStructuredData,
    RevisionAuthority,
)
from app.schemas.cv_overlap_review import StructuredProfileChangeComparison
from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredItemSourceKind,
    StructuredProfileItemLineageInput,
    StructuredProfileSection,
)
from app.services.candidate_structured_item_lineage import CandidateStructuredItemLineageService
from app.services.structured_profile_identity import structured_profile_item_fingerprint
from app.services.structured_profile_comparison import StructuredProfileComparisonService
from app.services.structured_profile_lineage_transitions import (
    StructuredItemLineageTransition,
    StructuredProfileLineageTransitionAnalyzer,
)
from app.services.structured_profile_mutation import adviser_lineage_event, persist_structured_profile

_ADVISER_PROPOSAL_UPDATE = TypeAdapter(CandidateAdviserProfileProposalUpdate)


class ProfileRevisionNotFound(LookupError):
    """The user does not own a revision with the requested ID."""


class ProfileRevisionConflict(ValueError):
    """The revision is no longer in the state/version expected by the caller."""


class ProfileRevisionStale(ProfileRevisionConflict):
    """A changed proposal authority no longer matches its creation baseline."""


_PROFILE_FIELDS = tuple(EditableCandidateProfileData.model_fields)
_STRUCTURED_FIELDS = tuple(EditableCandidateStructuredData.model_fields)


class CandidateProfileRevisionService:
    """CRUD, version checks, and currentness checks for noncanonical proposals."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def active(self, user_id: str) -> CandidateProfileRevisionRead | None:
        revision = self._session.scalar(
            select(CandidateProfileRevisionRecord).where(
                CandidateProfileRevisionRecord.active_user_id == user_id,
                CandidateProfileRevisionRecord.user_id == user_id,
            )
        )
        return self._read(revision, user_id) if revision is not None else None

    def read_record(
        self, revision: CandidateProfileRevisionRecord, user_id: str
    ) -> CandidateProfileRevisionRead:
        """Serialize a scoped revision record, including one staged in this transaction."""
        if revision.user_id != user_id:
            raise ProfileRevisionNotFound("Profile revision not found.")
        return self._read(revision, user_id)

    def create_or_resume(self, user_id: str) -> CandidateProfileRevisionRead:
        self.lock_user_authority(user_id)
        existing = self._active_row(user_id, fresh=True, for_update=True)
        if existing is not None:
            return self._read(existing, user_id)

        revision = self._new_revision_record(user_id)
        self._session.add(revision)
        try:
            self._session.commit()
            self._session.refresh(revision)
        except IntegrityError:
            # Another request may have won the unique active slot after our read.
            self._session.rollback()
            existing = self._active_row(user_id)
            if existing is None:
                raise
            return self._read(existing, user_id)
        return self._read(revision, user_id)

    def stage_new_revision(
        self,
        user_id: str,
        *,
        structured_transform: Callable[
            [CandidateCVData | None], EditableCandidateStructuredData
        ],
    ) -> CandidateProfileRevisionRecord:
        """Stage a new baseline-consistent draft without committing the transaction.

        This internal workflow hook is for atomic domain transitions that must
        insert a normal #207 revision together with another record mutation.
        The transform receives the locked, freshly read full structured state.
        """
        self.lock_user_authority(user_id)
        if self._active_row(user_id, fresh=True, for_update=True) is not None:
            raise ProfileRevisionConflict(
                "An active Profile draft already exists. Finish or discard it before transferring this proposal."
            )
        revision = self._new_revision_record(
            user_id,
            structured_transform=structured_transform,
            lock_authorities=True,
        )
        self._session.add(revision)
        self._session.flush()
        return revision

    def lock_user_authority(self, user_id: str) -> None:
        """Serialize canonical apply and revision creation, including empty Profile rows."""
        user = self._session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        if user is None:
            raise ProfileRevisionConflict("The authenticated Profile owner is unavailable.")

    def _new_revision_record(
        self,
        user_id: str,
        *,
        structured_transform: Callable[
            [CandidateCVData | None], EditableCandidateStructuredData
        ] | None = None,
        lock_authorities: bool = False,
    ) -> CandidateProfileRevisionRecord:
        profile = self._profile(user_id, for_update=lock_authorities)
        structured = self._structured(user_id, for_update=lock_authorities)
        full_structured = self._full_structured_data(structured)
        editable_structured = self._editable_structured_data(full_structured)
        proposed_structured = (
            structured_transform(full_structured)
            if structured_transform is not None
            else editable_structured
        )
        return CandidateProfileRevisionRecord(
            user_id=user_id,
            active_user_id=user_id,
            state=CandidateProfileRevisionState.DRAFT,
            revision=1,
            base_profile_fingerprint=profile_authority_fingerprint(profile),
            base_structured_fingerprint=structured_authority_fingerprint(full_structured),
            base_editable_structured_fingerprint=editable_structured_fingerprint(
                editable_structured, present=structured is not None
            ),
            proposed_profile_json=(
                _canonical_json(self._profile_payload(profile).model_dump(mode="json"))
                if profile is not None
                else None
            ),
            proposed_structured_json=(
                _canonical_json(proposed_structured.model_dump(mode="json"))
                if structured is not None or structured_transform is not None
                else None
            ),
        )

    def save(
        self,
        user_id: str,
        revision_id: str,
        *,
        expected_revision: int,
        patch_fields: set[str],
        proposed_profile: EditableCandidateProfileData | None,
        proposed_structured: EditableCandidateStructuredData | None,
    ) -> CandidateProfileRevisionRead:
        revision = self._owned_active_row(user_id, revision_id)
        self._expect_revision(revision, expected_revision)
        if not patch_fields:
            raise ProfileRevisionConflict("At least one proposal field must be supplied.")
        try:
            if "proposed_profile" in patch_fields:
                revision.proposed_profile_json = (
                    _canonical_json(proposed_profile.model_dump(mode="json"))
                    if proposed_profile is not None
                    else None
                )
            if "proposed_structured" in patch_fields:
                revision.proposed_structured_json = (
                    _canonical_json(proposed_structured.model_dump(mode="json"))
                    if proposed_structured is not None
                    else None
                )
            revision.state = CandidateProfileRevisionState.DRAFT
            revision.revision += 1
            self._session.commit()
            self._session.refresh(revision)
        except StaleDataError as exc:
            self._session.rollback()
            raise ProfileRevisionConflict(
                "Revision version conflict: another request changed this revision."
            ) from exc
        except Exception:
            self._session.rollback()
            raise
        return self._read(revision, user_id)

    def review(
        self, user_id: str, revision_id: str, *, expected_revision: int
    ) -> CandidateProfileRevisionRead:
        revision = self._owned_active_row(user_id, revision_id)
        self._expect_revision(revision, expected_revision)
        if revision.state != CandidateProfileRevisionState.DRAFT:
            raise ProfileRevisionConflict("Only a draft revision can be reviewed.")
        changed = self._changed_authorities(revision)
        stale = self._stale_authorities(
            revision, user_id, changed,
            structured_dependency=self._has_linked_adviser_structured_dependency(revision, user_id),
        )
        if stale:
            raise ProfileRevisionStale(
                "The current authority changed after this revision was created: "
                + ", ".join(stale)
                + ". Discard this revision and start again from current information."
            )
        if "structured" in changed:
            self._reject_reinforcement_duplicate_growth(
                self._full_structured_data(self._structured(user_id, fresh=True)),
                EditableCandidateStructuredData.model_validate_json(revision.proposed_structured_json),
            )
        try:
            revision.state = CandidateProfileRevisionState.REVIEW_READY
            revision.revision += 1
            self._session.commit()
            self._session.refresh(revision)
        except StaleDataError as exc:
            self._session.rollback()
            raise ProfileRevisionConflict(
                "Revision version conflict: another request changed this revision."
            ) from exc
        except Exception:
            self._session.rollback()
            raise
        return self._read(revision, user_id)

    def discard(
        self, user_id: str, revision_id: str, *, expected_revision: int
    ) -> CandidateProfileRevisionRead:
        revision = self._owned_active_row(user_id, revision_id)
        self._expect_revision(revision, expected_revision)
        try:
            revision.state = CandidateProfileRevisionState.DISCARDED
            revision.active_user_id = None
            revision.discarded_at = datetime.now(timezone.utc)
            revision.revision += 1
            self._session.commit()
            self._session.refresh(revision)
        except StaleDataError as exc:
            self._session.rollback()
            raise ProfileRevisionConflict(
                "Revision version conflict: another request changed this revision."
            ) from exc
        except Exception:
            self._session.rollback()
            raise
        return self._read(revision, user_id)

    def confirm(
        self, user_id: str, revision_id: str, *, expected_revision: int
    ) -> CandidateProfileRevisionRead:
        """Atomically promote a reviewed proposal and mark it confirmed.

        Repeating confirmation for an already-confirmed owned revision returns
        its confirmed representation. Discarded revisions are never confirmable.
        """
        revision = self._session.scalar(
            select(CandidateProfileRevisionRecord).where(
                CandidateProfileRevisionRecord.id == revision_id,
                CandidateProfileRevisionRecord.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if revision is None:
            raise ProfileRevisionNotFound("Profile revision not found.")
        if revision.state == CandidateProfileRevisionState.CONFIRMED:
            return self._read(revision, user_id)
        if revision.active_user_id != user_id:
            raise ProfileRevisionConflict("This revision is no longer active.")
        self._expect_revision(revision, expected_revision)
        if revision.state != CandidateProfileRevisionState.REVIEW_READY:
            raise ProfileRevisionConflict("Only a review-ready revision can be confirmed.")

        changed = self._changed_authorities(revision)
        linked_adviser = self._linked_adviser_proposal(user_id, revision.id)
        # Lock and fingerprint only the authorities this proposal changes.
        # The full structured fingerprint includes semantic evidence, which is
        # preserved verbatim when constructing a changed structured authority.
        profile = self._profile(user_id, for_update="profile" in changed)
        structured = self._structured(
            user_id, for_update=("structured" in changed or linked_adviser is not None)
        )
        full_structured = self._full_structured_data(structured)
        stale: list[RevisionAuthority] = []
        if "profile" in changed and profile_authority_fingerprint(
            profile
        ) != revision.base_profile_fingerprint:
            stale.append("profile")
        if "structured" in changed and structured_authority_fingerprint(
            full_structured
        ) != revision.base_structured_fingerprint:
            stale.append("structured")
        elif (
            linked_adviser is not None
            and revision.proposed_structured_json is not None
            and structured_authority_fingerprint(full_structured)
            != revision.base_structured_fingerprint
        ):
            stale.append("structured")
        if stale:
            raise ProfileRevisionStale(
                "The current authority changed after this revision was created: "
                + ", ".join(stale)
                + ". Discard this revision and start again from current information."
            )
        if "structured" in changed:
            self._reject_reinforcement_duplicate_growth(
                full_structured,
                EditableCandidateStructuredData.model_validate_json(revision.proposed_structured_json),
            )

        try:
            if "profile" in changed:
                proposal = EditableCandidateProfileData.model_validate_json(
                    revision.proposed_profile_json
                )
                if profile is None:
                    profile = CandidateProfile(user_id=user_id)
                    self._session.add(profile)
                for field in _PROFILE_FIELDS:
                    setattr(profile, field, getattr(proposal, field))

            final_structured = full_structured
            if "structured" in changed:
                structured_proposal = EditableCandidateStructuredData.model_validate_json(
                    revision.proposed_structured_json
                )
                final_structured = CandidateCVData(
                    **structured_proposal.model_dump(mode="python"),
                    evidence=full_structured.evidence if full_structured is not None else [],
                )
                structured = persist_structured_profile(
                    self._session, user_id, structured, final_structured
                )

            analyzer = StructuredProfileLineageTransitionAnalyzer()
            manual_transitions = (
                analyzer.analyze_changed_resulting_items(full_structured, final_structured)
                if "structured" in changed else []
            )
            adviser_event: StructuredProfileItemLineageInput | None = None
            if linked_adviser is not None and final_structured is not None:
                adviser_record, adviser_update = linked_adviser
                adviser_event = adviser_lineage_event(
                    full_structured,
                    final_structured,
                    StructuredProfileSection(adviser_update.section),
                    adviser_update.item,
                    source_ref=adviser_record.id,
                )

            events: list[StructuredProfileItemLineageInput] = []
            if adviser_event is not None:
                events.append(adviser_event)
            for transition in manual_transitions:
                if adviser_event is not None and transition.section is adviser_event.section:
                    transition_fingerprint = structured_profile_item_fingerprint(
                        transition.section, transition.item
                    )
                    if transition_fingerprint == structured_profile_item_fingerprint(
                        adviser_event.section, adviser_event.item
                    ):
                        continue
                events.append(self._lineage_event(
                    transition,
                    source_kind=StructuredItemSourceKind.MANUAL_PROFILE,
                    source_ref=revision.id,
                ))
            CandidateStructuredItemLineageService(self._session).stage_many(user_id, events)

            revision.state = CandidateProfileRevisionState.CONFIRMED
            revision.active_user_id = None
            revision.confirmed_at = datetime.now(timezone.utc)
            revision.revision += 1
            self._session.commit()
            self._session.refresh(revision)
        except StaleDataError as exc:
            self._session.rollback()
            raise ProfileRevisionConflict(
                "Revision version conflict: another request changed this revision."
            ) from exc
        except Exception:
            self._session.rollback()
            raise
        return self._read(revision, user_id)

    def _linked_adviser_proposal(self, user_id: str, revision_id: str):
        """Validate any durable Adviser→revision link and its saved typed item."""
        rows = self._session.scalars(
            select(CandidateAdviserProfileProposalRecord)
            .where(CandidateAdviserProfileProposalRecord.transferred_profile_revision_id == revision_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        if not rows:
            return None
        if len(rows) != 1:
            raise ProfileRevisionConflict("The Adviser proposal link to this Profile revision is ambiguous.")
        proposal = rows[0]
        if (
            proposal.user_id != user_id
            or proposal.state != CandidateAdviserProfileProposalState.TRANSFERRED
            or proposal.transferred_at is None
        ):
            raise ProfileRevisionConflict("The linked Adviser proposal is not a valid transferred source.")
        try:
            update = _ADVISER_PROPOSAL_UPDATE.validate_json(proposal.proposed_update_json)
        except (ValidationError, TypeError) as exc:
            raise ProfileRevisionConflict("The linked Adviser proposal has an invalid saved structured item.") from exc
        return proposal, update

    @staticmethod
    def _lineage_event(transition, *, source_kind, source_ref):
        return StructuredProfileItemLineageInput(
            section=transition.section,
            item=transition.item,
            source_kind=source_kind,
            source_ref=source_ref,
            relationship=transition.relationship,
            predecessor_item=transition.predecessor_item,
        )

    @staticmethod
    def _reject_reinforcement_duplicate_growth(
        before: CandidateCVData | None,
        proposal: EditableCandidateStructuredData,
    ) -> None:
        """Reject added occurrences in same-fact groups without requiring legacy cleanup."""
        base = before or CandidateCVData()
        comparator = StructuredProfileComparisonService()
        for section in StructuredProfileSection:
            old_items = getattr(base, section.value)
            proposed_items = getattr(proposal, section.value)
            all_items = [(False, item) for item in old_items] + [(True, item) for item in proposed_items]
            parents = list(range(len(all_items)))
            ranks = [0] * len(all_items)

            def find(value: int) -> int:
                while parents[value] != value:
                    parents[value] = parents[parents[value]]
                    value = parents[value]
                return value

            def union(left: int, right: int) -> None:
                root_left, root_right = find(left), find(right)
                if root_left == root_right:
                    return
                if ranks[root_left] < ranks[root_right]:
                    root_left, root_right = root_right, root_left
                parents[root_right] = root_left
                if ranks[root_left] == ranks[root_right]:
                    ranks[root_left] += 1

            for left in range(len(all_items)):
                for right in range(left + 1, len(all_items)):
                    if comparator.compare(
                        section, all_items[left][1], [all_items[right][1]]
                    ).relationship is StructuredItemRelationship.REINFORCEMENT:
                        union(left, right)

            totals: dict[int, list[int]] = {}
            for index, (is_proposed, _item) in enumerate(all_items):
                counts = totals.setdefault(find(index), [0, 0, 0])
                counts[1 if is_proposed else 0] += 1
                counts[2] += 1
            if any(
                group_size > 1 and proposed_count > old_count
                for old_count, proposed_count, group_size in totals.values()
            ):
                raise ProfileRevisionConflict(
                    "The Profile revision adds a same-fact duplicate. Remove the duplicate item before review or confirmation."
                )

    def _structured_comparison_projection(
        self, revision: CandidateProfileRevisionRecord, user_id: str
    ) -> list[StructuredProfileChangeComparison]:
        if revision.proposed_structured_json is None:
            return []
        current_record = self._structured(user_id, fresh=True)
        before = self._full_structured_data(current_record)
        editable = EditableCandidateStructuredData.model_validate_json(
            revision.proposed_structured_json
        )
        after = CandidateCVData(
            **editable.model_dump(mode="python"),
            evidence=before.evidence if before is not None else [],
        )
        transitions = StructuredProfileLineageTransitionAnalyzer().analyze_changed_resulting_items(
            before, after
        )
        comparator = StructuredProfileComparisonService()
        occurrences: dict[tuple[StructuredProfileSection, str], int] = {}
        result: list[StructuredProfileChangeComparison] = []
        for transition in transitions:
            fp = structured_profile_item_fingerprint(transition.section, transition.item)
            key = (transition.section, fp)
            occurrences[key] = occurrences.get(key, 0) + 1
            item_key = hashlib.sha256(_canonical_json({
                "section": transition.section.value,
                "incoming_fingerprint": fp,
                "occurrence": occurrences[key],
            }).encode("utf-8")).hexdigest()
            comparison = comparator.compare(
                transition.section, transition.item,
                getattr(before or CandidateCVData(), transition.section.value),
            )
            result.append(StructuredProfileChangeComparison(
                item_key=item_key, comparison=comparison
            ))
        return result

    def _read(
        self, revision: CandidateProfileRevisionRecord, user_id: str
    ) -> CandidateProfileRevisionRead:
        changed = self._changed_authorities(revision)
        adviser_dependency = self._has_linked_adviser_structured_dependency(revision, user_id)
        stale = (
            []
            if revision.state == CandidateProfileRevisionState.CONFIRMED
            else self._stale_authorities(
                revision, user_id, changed, structured_dependency=adviser_dependency
            )
        )
        return CandidateProfileRevisionRead(
            id=revision.id,
            state=revision.state,
            revision=revision.revision,
            proposed_profile=(
                EditableCandidateProfileData.model_validate_json(revision.proposed_profile_json)
                if revision.proposed_profile_json is not None
                else None
            ),
            proposed_structured=(
                EditableCandidateStructuredData.model_validate_json(revision.proposed_structured_json)
                if revision.proposed_structured_json is not None
                else None
            ),
            changed_authorities=changed,
            stale_authorities=stale,
            structured_comparisons=self._structured_comparison_projection(
                revision, user_id
            ),
            created_at=revision.created_at,
            updated_at=revision.updated_at,
            confirmed_at=revision.confirmed_at,
            discarded_at=revision.discarded_at,
        )

    def _changed_authorities(
        self, revision: CandidateProfileRevisionRecord
    ) -> list[RevisionAuthority]:
        changed: list[RevisionAuthority] = []
        if revision.proposed_profile_json is not None:
            proposal = EditableCandidateProfileData.model_validate_json(
                revision.proposed_profile_json
            )
            if profile_proposal_fingerprint(proposal) != revision.base_profile_fingerprint:
                changed.append("profile")
        if revision.proposed_structured_json is not None:
            proposal = EditableCandidateStructuredData.model_validate_json(
                revision.proposed_structured_json
            )
            if (
                editable_structured_fingerprint(proposal, present=True)
                != revision.base_editable_structured_fingerprint
            ):
                changed.append("structured")
        return changed

    def _stale_authorities(
        self,
        revision: CandidateProfileRevisionRecord,
        user_id: str,
        changed: list[RevisionAuthority],
        *,
        structured_dependency: bool = False,
    ) -> list[RevisionAuthority]:
        stale: list[RevisionAuthority] = []
        if "profile" in changed and profile_authority_fingerprint(
            self._profile(user_id, fresh=True)
        ) != revision.base_profile_fingerprint:
            stale.append("profile")
        if ("structured" in changed or structured_dependency) and structured_authority_fingerprint(
            self._full_structured_data(self._structured(user_id, fresh=True))
        ) != revision.base_structured_fingerprint:
            stale.append("structured")
        return stale

    def _has_linked_adviser_structured_dependency(
        self, revision: CandidateProfileRevisionRecord, user_id: str
    ) -> bool:
        if revision.proposed_structured_json is None:
            return False
        return self._session.scalar(
            select(CandidateAdviserProfileProposalRecord.id).where(
                CandidateAdviserProfileProposalRecord.user_id == user_id,
                CandidateAdviserProfileProposalRecord.transferred_profile_revision_id == revision.id,
                CandidateAdviserProfileProposalRecord.state == CandidateAdviserProfileProposalState.TRANSFERRED,
            )
        ) is not None

    def _active_row(
        self, user_id: str, *, fresh: bool = False, for_update: bool = False
    ) -> CandidateProfileRevisionRecord | None:
        statement = select(CandidateProfileRevisionRecord).where(
            CandidateProfileRevisionRecord.active_user_id == user_id,
            CandidateProfileRevisionRecord.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        if fresh or for_update:
            statement = statement.execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def _owned_active_row(
        self, user_id: str, revision_id: str
    ) -> CandidateProfileRevisionRecord:
        revision = self._session.scalar(
            select(CandidateProfileRevisionRecord).where(
                CandidateProfileRevisionRecord.id == revision_id,
                CandidateProfileRevisionRecord.user_id == user_id,
            )
        )
        if revision is None:
            raise ProfileRevisionNotFound("Profile revision not found.")
        if revision.active_user_id != user_id or revision.state not in {
            CandidateProfileRevisionState.DRAFT,
            CandidateProfileRevisionState.REVIEW_READY,
        }:
            raise ProfileRevisionConflict("This revision is no longer active.")
        return revision

    @staticmethod
    def _expect_revision(
        revision: CandidateProfileRevisionRecord, expected_revision: int
    ) -> None:
        if revision.revision != expected_revision:
            raise ProfileRevisionConflict(
                f"Revision version conflict: expected {expected_revision}, current version is {revision.revision}."
            )

    def _profile(
        self, user_id: str, *, for_update: bool = False, fresh: bool = False
    ) -> CandidateProfile | None:
        query = select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        elif fresh:
            query = query.execution_options(populate_existing=True)
        return self._session.scalar(query)

    def _structured(
        self, user_id: str, *, for_update: bool = False, fresh: bool = False
    ) -> CandidateStructuredProfile | None:
        query = select(CandidateStructuredProfile).where(
            CandidateStructuredProfile.user_id == user_id
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        elif fresh:
            query = query.execution_options(populate_existing=True)
        return self._session.scalar(query)

    @staticmethod
    def _profile_payload(profile: CandidateProfile) -> EditableCandidateProfileData:
        return EditableCandidateProfileData.model_validate(
            {field: getattr(profile, field) for field in _PROFILE_FIELDS}
        )

    @staticmethod
    def _full_structured_data(
        structured: CandidateStructuredProfile | None,
    ) -> CandidateCVData | None:
        if structured is None:
            return None
        return CandidateCVData.model_validate_json(structured.structured_json)

    @staticmethod
    def _editable_structured_data(
        data: CandidateCVData | None,
    ) -> EditableCandidateStructuredData:
        if data is None:
            return EditableCandidateStructuredData()
        return EditableCandidateStructuredData.model_validate(
            {field: getattr(data, field) for field in _STRUCTURED_FIELDS}
        )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _authority_fingerprint(*, present: bool, value: object | None) -> str:
    """Hash canonical JSON with an explicit authority-presence marker."""
    encoded = _canonical_json({"present": present, "value": value if present else None})
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def profile_authority_fingerprint(profile: CandidateProfile | None) -> str:
    payload = (
        {field: getattr(profile, field) for field in _PROFILE_FIELDS}
        if profile is not None
        else None
    )
    return _authority_fingerprint(present=profile is not None, value=payload)


def profile_proposal_fingerprint(profile: EditableCandidateProfileData) -> str:
    return _authority_fingerprint(present=True, value=profile.model_dump(mode="json"))


def structured_authority_fingerprint(data: CandidateCVData | None) -> str:
    payload = data.model_dump(mode="json") if data is not None else None
    return _authority_fingerprint(present=data is not None, value=payload)


def editable_structured_fingerprint(
    data: EditableCandidateStructuredData, *, present: bool
) -> str:
    return _authority_fingerprint(
        present=present,
        value=data.model_dump(mode="json") if present else None,
    )

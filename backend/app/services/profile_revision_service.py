"""Provider-free persisted manual Profile revision drafts and review gating."""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.orm import Session

from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.profile_revision import (
    CandidateProfileRevisionRead,
    CandidateProfileRevisionState,
    EditableCandidateProfileData,
    EditableCandidateStructuredData,
    RevisionAuthority,
)


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

    def create_or_resume(self, user_id: str) -> CandidateProfileRevisionRead:
        existing = self._active_row(user_id)
        if existing is not None:
            return self._read(existing, user_id)

        profile = self._profile(user_id)
        structured = self._structured(user_id)
        full_structured = self._full_structured_data(structured)
        editable_structured = self._editable_structured_data(full_structured)
        revision = CandidateProfileRevisionRecord(
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
                _canonical_json(editable_structured.model_dump(mode="json"))
                if structured is not None
                else None
            ),
        )
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
        stale = self._stale_authorities(revision, user_id, changed)
        if stale:
            raise ProfileRevisionStale(
                "The current authority changed after this revision was created: "
                + ", ".join(stale)
                + ". Discard this revision and start again from current information."
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
        # Lock and fingerprint only the authorities this proposal changes.
        # The full structured fingerprint includes semantic evidence, which is
        # preserved verbatim when constructing a changed structured authority.
        profile = self._profile(user_id, for_update="profile" in changed)
        structured = self._structured(user_id, for_update="structured" in changed)
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
        if stale:
            raise ProfileRevisionStale(
                "The current authority changed after this revision was created: "
                + ", ".join(stale)
                + ". Discard this revision and start again from current information."
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

            if "structured" in changed:
                proposal = EditableCandidateStructuredData.model_validate_json(
                    revision.proposed_structured_json
                )
                data = CandidateCVData(
                    **proposal.model_dump(mode="python"),
                    evidence=full_structured.evidence if full_structured is not None else [],
                )
                encoded = _canonical_json(data.model_dump(mode="json"))
                if structured is None:
                    structured = CandidateStructuredProfile(
                        user_id=user_id, structured_json=encoded
                    )
                    self._session.add(structured)
                else:
                    structured.structured_json = encoded
                ActiveCandidateEvidenceResolver(self._session).resolve(user_id, data)

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

    def _read(
        self, revision: CandidateProfileRevisionRecord, user_id: str
    ) -> CandidateProfileRevisionRead:
        changed = self._changed_authorities(revision)
        stale = (
            []
            if revision.state == CandidateProfileRevisionState.CONFIRMED
            else self._stale_authorities(revision, user_id, changed)
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
    ) -> list[RevisionAuthority]:
        stale: list[RevisionAuthority] = []
        if "profile" in changed and profile_authority_fingerprint(
            self._profile(user_id)
        ) != revision.base_profile_fingerprint:
            stale.append("profile")
        if "structured" in changed and structured_authority_fingerprint(
            self._full_structured_data(self._structured(user_id))
        ) != revision.base_structured_fingerprint:
            stale.append("structured")
        return stale

    def _active_row(self, user_id: str) -> CandidateProfileRevisionRecord | None:
        return self._session.scalar(
            select(CandidateProfileRevisionRecord).where(
                CandidateProfileRevisionRecord.active_user_id == user_id,
                CandidateProfileRevisionRecord.user_id == user_id,
            )
        )

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
        self, user_id: str, *, for_update: bool = False
    ) -> CandidateProfile | None:
        query = select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(query)

    def _structured(
        self, user_id: str, *, for_update: bool = False
    ) -> CandidateStructuredProfile | None:
        query = select(CandidateStructuredProfile).where(
            CandidateStructuredProfile.user_id == user_id
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
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

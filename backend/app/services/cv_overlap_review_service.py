"""Provider-free persisted choices for CV/current structured-item overlap."""

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateStructuredProfile
from app.models.candidate_cv_overlap_review import CandidateCVOverlapReviewRecord
from app.schemas.cv_overlap_review import (
    CVOverlapIncomingDuplicate,
    CVOverlapResolution,
    CVOverlapResolutionAction,
    CVOverlapReviewItem,
    CVOverlapReviewPatch,
    CVOverlapReviewRead,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredItemSourceKind,
    StructuredProfileItem,
    StructuredProfileItemLineageInput,
    StructuredProfileSection,
    StructuredProfileItemMatch,
)
from app.services.structured_profile_comparison import StructuredProfileComparisonService
from app.services.structured_profile_identity import structured_profile_item_fingerprint
from app.services.structured_profile_lineage_transitions import StructuredItemLineageTransition


class CVOverlapReviewConflict(ValueError):
    """Overlap choices or draft lifecycle changed unexpectedly."""


class CVOverlapReviewRequired(CVOverlapReviewConflict):
    """The reviewed CV has overlaps that need explicit user choices."""


class CVOverlapReviewStale(CVOverlapReviewRequired):
    """Persisted choices no longer match the reviewed CV/current authority."""


class CVOverlapInvalidResolution(CVOverlapReviewConflict):
    """An overlap choice is not valid for its current deterministic comparison."""


@dataclass(frozen=True)
class _Incoming:
    item_key: str
    section: StructuredProfileSection
    item: StructuredProfileItem
    comparison: object


class CVOverlapReviewService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._comparator = StructuredProfileComparisonService()

    def read(self, user_id: str, draft_id: str) -> CVOverlapReviewRead:
        draft = self._draft(user_id, draft_id)
        profile = self._profile(user_id)
        data = self._draft_data(draft)
        base = self._profile_data(profile)
        row = self._review_row(user_id, draft_id)
        base_fp, draft_fp = self._fingerprints(profile is not None, base, data)
        stale = row is not None and (
            row.base_structured_fingerprint != base_fp or row.draft_fingerprint != draft_fp
        )
        choices: list[CVOverlapResolution] = []
        if row is not None and not stale:
            try:
                choices = [CVOverlapResolution.model_validate(value) for value in json.loads(row.resolutions_json)]
                analysis, _, duplicates = self._analyse(base, data)
                by_key = {value.item_key: value for value in analysis}
                for choice in choices:
                    incoming = by_key.get(choice.item_key)
                    if incoming is None:
                        raise CVOverlapInvalidResolution("A saved CV overlap choice no longer identifies an incoming item.")
                    self._validate_resolution(choice, incoming, base)
                if duplicates:
                    raise CVOverlapInvalidResolution("The reviewed CV contains reinforcement-equivalent duplicate items.")
            except (ValueError, TypeError, json.JSONDecodeError):
                stale = True
                choices = []
        return self._read_projection(
            draft, profile is not None, base, data, row.revision if row is not None else 0,
            base_fp, draft_fp, stale, choices,
        )

    def update(
        self, user_id: str, draft_id: str, patch: CVOverlapReviewPatch
    ) -> CVOverlapReviewRead:
        draft = self._draft(user_id, draft_id, for_update=True)
        profile = self._profile(user_id)
        data = self._draft_data(draft)
        base = self._profile_data(profile)
        base_fp, draft_fp = self._fingerprints(profile is not None, base, data)
        if (
            patch.expected_base_structured_fingerprint != base_fp
            or patch.expected_draft_fingerprint != draft_fp
        ):
            raise CVOverlapReviewStale(
                "CV overlap review is stale. Refresh the review and resolve overlaps against current information."
            )
        analysis, _, duplicates = self._analyse(base, data)
        if duplicates:
            raise CVOverlapInvalidResolution("Edit the reviewed CV to remove same-fact duplicate items before resolving overlaps.")
        row = self._review_row(user_id, draft_id, for_update=True)
        expected = row.revision if row is not None else 0
        if patch.expected_review_revision != expected:
            raise CVOverlapReviewConflict(
                f"Overlap review version conflict: expected {patch.expected_review_revision}, current is {expected}."
            )
        choices: dict[str, CVOverlapResolution] = {}
        if row is not None and row.base_structured_fingerprint == base_fp and row.draft_fingerprint == draft_fp:
            for value in json.loads(row.resolutions_json):
                saved = CVOverlapResolution.model_validate(value)
                old_analysis = next((item for item in analysis if item.item_key == saved.item_key), None)
                if old_analysis is not None:
                    try:
                        self._validate_resolution(saved, old_analysis, base)
                    except CVOverlapInvalidResolution:
                        continue
                    choices[saved.item_key] = saved
        by_key = {value.item_key: value for value in analysis}
        for choice in patch.resolutions:
            incoming = by_key.get(choice.item_key)
            if incoming is None:
                raise CVOverlapInvalidResolution("The choice does not identify an item in this reviewed CV.")
            self._validate_resolution(choice, incoming, base)
            choices[choice.item_key] = choice

        encoded = json.dumps(
            [value.model_dump(mode="json") for value in choices.values()],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        try:
            if row is None:
                self._session.add(CandidateCVOverlapReviewRecord(
                    user_id=user_id, draft_id=draft_id, revision=1,
                    base_structured_fingerprint=base_fp, draft_fingerprint=draft_fp,
                    resolutions_json=encoded,
                ))
            else:
                row.base_structured_fingerprint = base_fp
                row.draft_fingerprint = draft_fp
                row.resolutions_json = encoded
                row.revision += 1
            self._session.commit()
        except (IntegrityError, StaleDataError) as exc:
            self._session.rollback()
            raise CVOverlapReviewConflict("The overlap review changed concurrently; reload it and retry.") from exc
        except Exception:
            self._session.rollback()
            raise
        return self.read(user_id, draft_id)

    def resolve_for_confirmation(
        self,
        user_id: str,
        draft: CandidateCVIngestionDraft,
        profile: CandidateStructuredProfile | None,
        data: CandidateCVData,
    ) -> tuple[CandidateCVData, list[StructuredItemLineageTransition]]:
        """Recompute, validate, and resolve overlaps against locked fresh rows."""
        base = self._profile_data(profile)
        analysis, incoming_by_key, duplicates = self._analyse(base, data)
        if duplicates:
            raise CVOverlapReviewRequired(
                "The reviewed CV contains same-fact duplicate items. Edit the CV draft to remove duplicates."
            )
        row = self._review_row(user_id, draft.id, for_update=True)
        base_fp, draft_fp = self._fingerprints(profile is not None, base, data)
        choices: dict[str, CVOverlapResolution] = {}
        if row is not None:
            stored = [CVOverlapResolution.model_validate(value) for value in json.loads(row.resolutions_json)]
            if stored and (
                row.base_structured_fingerprint != base_fp or row.draft_fingerprint != draft_fp
            ):
                raise CVOverlapReviewStale("CV overlap choices are stale. Refresh the overlap review before confirming.")
            for choice in stored:
                incoming = incoming_by_key.get(choice.item_key)
                if incoming is None:
                    raise CVOverlapReviewStale("CV overlap choices no longer match the reviewed CV. Refresh the overlap review.")
                try:
                    self._validate_resolution(choice, incoming, base)
                except CVOverlapInvalidResolution as exc:
                    raise CVOverlapReviewStale("CV overlap choices are stale. Refresh the overlap review.") from exc
                choices[choice.item_key] = choice

        unresolved = [
            value for value in analysis
            if value.comparison.relationship in {
                StructuredItemRelationship.REFINEMENT,
                StructuredItemRelationship.CONFLICT,
                StructuredItemRelationship.AMBIGUOUS,
            }
            and value.item_key not in choices
        ]
        if unresolved:
            raise CVOverlapReviewRequired(
                "Resolve all CV refinements, conflicts, and ambiguous overlaps before confirming the CV."
            )

        final_items: dict[StructuredProfileSection, list[StructuredProfileItem]] = {
            section: [] for section in StructuredProfileSection
        }
        transitions: list[StructuredItemLineageTransition] = []
        for value in analysis:
            comparison = value.comparison
            relationship = comparison.relationship
            choice = choices.get(value.item_key)
            if relationship in {StructuredItemRelationship.NEW, StructuredItemRelationship.REINFORCEMENT}:
                final_items[value.section].append(value.item)
                transitions.append(StructuredItemLineageTransition(
                    value.section, value.item, relationship, comparison.current_item
                ))
                continue
            assert choice is not None
            if choice.action is CVOverlapResolutionAction.KEEP_CURRENT:
                selected = comparison.current_item
                assert selected is not None
                if not any(
                    structured_profile_item_fingerprint(value.section, item)
                    == structured_profile_item_fingerprint(value.section, selected)
                    for item in final_items[value.section]
                ):
                    final_items[value.section].append(selected)
                continue
            if choice.action is CVOverlapResolutionAction.SKIP_INCOMING:
                continue
            if choice.action in {CVOverlapResolutionAction.REPLACE_CURRENT, CVOverlapResolutionAction.ADD_AS_NEW}:
                final_items[value.section].append(value.item)
                predecessor = None
                if choice.action is CVOverlapResolutionAction.REPLACE_CURRENT:
                    predecessor = next(
                        match.item for match in comparison.candidate_matches
                        if match.fingerprint == choice.target_fingerprint
                    )
                transitions.append(StructuredItemLineageTransition(
                    value.section, value.item, relationship, predecessor
                ))

        resolved = CandidateCVData(
            **{section.value: items for section, items in final_items.items()},
            evidence=data.evidence,
        )
        return resolved, transitions

    def _analyse(self, before: CandidateCVData | None, after: CandidateCVData):
        current = before or CandidateCVData()
        occurrence: dict[tuple[StructuredProfileSection, str], int] = {}
        analysis: list[_Incoming] = []
        duplicates: list[CVOverlapIncomingDuplicate] = []
        seen: dict[StructuredProfileSection, list[_Incoming]] = {section: [] for section in StructuredProfileSection}
        for section in StructuredProfileSection:
            for item in getattr(after, section.value):
                fingerprint = structured_profile_item_fingerprint(section, item)
                key = (section, fingerprint)
                occurrence[key] = occurrence.get(key, 0) + 1
                item_key = self._item_key(section, fingerprint, occurrence[key])
                comparison = self._comparator.compare(section, item, getattr(current, section.value))
                value = _Incoming(item_key, section, item, comparison)
                for previous in seen[section]:
                    repeated = self._comparator.compare(section, item, [previous.item])
                    if repeated.relationship is StructuredItemRelationship.REINFORCEMENT:
                        duplicates.append(CVOverlapIncomingDuplicate(
                            section=section, first_item_key=previous.item_key,
                            duplicate_item_key=item_key, first_item=previous.item,
                            duplicate_item=item,
                        ))
                        break
                seen[section].append(value)
                analysis.append(value)
        return analysis, {item.item_key: item for item in analysis}, duplicates

    def _read_projection(
        self, draft, present, base, data, revision, base_fp, draft_fp, stale, choices
    ) -> CVOverlapReviewRead:
        analysis, _, duplicates = self._analyse(base, data)
        choices_by_key = {value.item_key: value for value in choices}
        items = []
        for value in analysis:
            comparison = value.comparison
            relationship = comparison.relationship
            choice = choices_by_key.get(value.item_key)
            needs_choice = relationship in {
                StructuredItemRelationship.REFINEMENT,
                StructuredItemRelationship.CONFLICT,
                StructuredItemRelationship.AMBIGUOUS,
            }
            items.append(CVOverlapReviewItem(
                item_key=value.item_key,
                section=value.section,
                incoming_item=value.item,
                incoming_fingerprint=comparison.incoming_fingerprint,
                relationship=relationship,
                candidate_matches=comparison.candidate_matches,
                target_fingerprint=comparison.target_fingerprint,
                current_item=comparison.current_item,
                saved_resolution=choice,
                resolution_required=needs_choice and choice is None,
            ))
        return CVOverlapReviewRead(
            draft_id=draft.id, revision=revision, base_structured_fingerprint=base_fp,
            draft_fingerprint=draft_fp, stale=stale, items=items,
            incoming_duplicates=duplicates,
        )

    def _validate_resolution(self, choice: CVOverlapResolution, incoming: _Incoming, base):
        relation = incoming.comparison.relationship
        candidates = {value.fingerprint: value.item for value in incoming.comparison.candidate_matches}
        if relation not in {
            StructuredItemRelationship.REFINEMENT,
            StructuredItemRelationship.CONFLICT,
            StructuredItemRelationship.AMBIGUOUS,
        }:
            raise CVOverlapInvalidResolution("Only refinement, conflict, or ambiguous items accept a resolution.")
        if choice.action is CVOverlapResolutionAction.REPLACE_CURRENT:
            target = choice.target_fingerprint
            if target not in candidates:
                raise CVOverlapInvalidResolution("Choose one exact candidate target from this overlap.")
            exact_targets = [
                item for item in getattr(base or CandidateCVData(), incoming.section.value)
                if structured_profile_item_fingerprint(incoming.section, item) == target
            ]
            if len(exact_targets) != 1:
                raise CVOverlapInvalidResolution("The selected current target is no longer present exactly once.")
            if relation is not StructuredItemRelationship.AMBIGUOUS and target != incoming.comparison.target_fingerprint:
                raise CVOverlapInvalidResolution("This unique overlap must resolve against its unique comparator target.")
        elif choice.action is CVOverlapResolutionAction.KEEP_CURRENT:
            if relation not in {StructuredItemRelationship.REFINEMENT, StructuredItemRelationship.CONFLICT}:
                raise CVOverlapInvalidResolution("keep_current is available only for a unique refinement or conflict.")
            if choice.target_fingerprint is not None:
                raise CVOverlapInvalidResolution("keep_current does not take a target fingerprint.")
        elif choice.action in {CVOverlapResolutionAction.ADD_AS_NEW, CVOverlapResolutionAction.SKIP_INCOMING}:
            if relation is not StructuredItemRelationship.AMBIGUOUS:
                raise CVOverlapInvalidResolution("add_as_new and skip_incoming are available only for ambiguous overlaps.")
        return candidates.get(choice.target_fingerprint) if choice.target_fingerprint else None

    @staticmethod
    def _item_key(section, fingerprint, occurrence):
        material = json.dumps(
            {"section": section.value, "incoming_fingerprint": fingerprint, "occurrence": occurrence},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _fingerprints(present, base, draft):
        base_payload = base.model_dump(mode="json") if base is not None else None
        base_encoded = json.dumps(
            {"present": present, "value": base_payload}, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False,
        )
        draft_encoded = json.dumps(
            draft.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        return (
            hashlib.sha256(base_encoded.encode("utf-8")).hexdigest(),
            hashlib.sha256(draft_encoded.encode("utf-8")).hexdigest(),
        )

    def _draft(self, user_id, draft_id, *, for_update=False):
        query = select(CandidateCVIngestionDraft).where(
            CandidateCVIngestionDraft.id == draft_id,
            CandidateCVIngestionDraft.user_id == user_id,
        ).execution_options(populate_existing=True)
        if for_update:
            query = query.with_for_update()
        draft = self._session.scalar(query)
        if draft is None:
            raise LookupError("CV ingestion draft not found.")
        if draft.state != "review_ready" or not draft.merged_json:
            raise CVOverlapReviewConflict("Only a review-ready CV draft has an overlap review.")
        return draft

    def _profile(self, user_id):
        return self._session.scalar(
            select(CandidateStructuredProfile)
            .where(CandidateStructuredProfile.user_id == user_id)
            .execution_options(populate_existing=True)
        )

    def _review_row(self, user_id, draft_id, *, for_update=False):
        query = select(CandidateCVOverlapReviewRecord).where(
            CandidateCVOverlapReviewRecord.user_id == user_id,
            CandidateCVOverlapReviewRecord.draft_id == draft_id,
        ).execution_options(populate_existing=True)
        if for_update:
            query = query.with_for_update()
        return self._session.scalar(query)

    @staticmethod
    def _profile_data(profile):
        return CandidateCVData.model_validate_json(profile.structured_json) if profile is not None else None

    @staticmethod
    def _draft_data(draft):
        return CandidateCVData.model_validate_json(draft.merged_json)

from dataclasses import dataclass

from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredProfileItem,
    StructuredProfileSection,
)
from app.services.structured_profile_comparison import StructuredProfileComparisonService
from app.services.structured_profile_identity import structured_profile_item_fingerprint


@dataclass(frozen=True)
class StructuredItemLineageTransition:
    section: StructuredProfileSection
    item: StructuredProfileItem
    relationship: StructuredItemRelationship
    predecessor_item: StructuredProfileItem | None


class StructuredProfileLineageTransitionAnalyzer:
    """Provider-free comparison of before/after structured authorities."""

    def __init__(self, comparator: StructuredProfileComparisonService | None = None) -> None:
        self._comparator = comparator or StructuredProfileComparisonService()

    def analyze_complete_source(
        self,
        before: CandidateCVData | None,
        after: CandidateCVData,
    ) -> list[StructuredItemLineageTransition]:
        """Every item in a reviewed complete source independently supports its content."""
        old = before or CandidateCVData()
        transitions: list[StructuredItemLineageTransition] = []
        for section in StructuredProfileSection:
            old_items = getattr(old, section.value)
            for item in getattr(after, section.value):
                result = self._comparator.compare(section, item, old_items)
                transitions.append(self._transition(section, result))
        return transitions

    def analyze_item(
        self,
        before: CandidateCVData | None,
        section: StructuredProfileSection,
        item: StructuredProfileItem,
    ) -> StructuredItemLineageTransition:
        old = before or CandidateCVData()
        result = self._comparator.compare(section, item, getattr(old, section.value))
        return self._transition(section, result)

    def analyze_changed_resulting_items(
        self,
        before: CandidateCVData | None,
        after: CandidateCVData,
    ) -> list[StructuredItemLineageTransition]:
        """Only newly resulting item occurrences are attributable to an edit proposal."""
        old = before or CandidateCVData()
        transitions: list[StructuredItemLineageTransition] = []
        for section in StructuredProfileSection:
            old_items = getattr(old, section.value)
            old_counts: dict[str, int] = {}
            for item in old_items:
                fingerprint = structured_profile_item_fingerprint(section, item)
                old_counts[fingerprint] = old_counts.get(fingerprint, 0) + 1
            after_seen: dict[str, int] = {}
            for item in getattr(after, section.value):
                fingerprint = structured_profile_item_fingerprint(section, item)
                after_seen[fingerprint] = after_seen.get(fingerprint, 0) + 1
                if after_seen[fingerprint] <= old_counts.get(fingerprint, 0):
                    continue
                result = self._comparator.compare(section, item, old_items)
                transitions.append(self._transition(section, result))
        return transitions

    @staticmethod
    def _transition(section, result) -> StructuredItemLineageTransition:
        predecessor = result.current_item
        # Ambiguity never chooses a target; new facts have no predecessor.
        if result.relationship in {StructuredItemRelationship.NEW, StructuredItemRelationship.AMBIGUOUS}:
            predecessor = None
        return StructuredItemLineageTransition(
            section=section,
            item=result.incoming_item,
            relationship=result.relationship,
            predecessor_item=predecessor,
        )

import re
import unicodedata

from pydantic import BaseModel

from app.schemas.structured_profile import (
    StructuredItemRelationship,
    StructuredProfileComparisonResult,
    StructuredProfileItemMatch,
    StructuredProfileSection,
    typed_structured_item,
)
from app.services.structured_profile_identity import structured_profile_item_fingerprint


def normalize_identity_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()


def _norm(value: object) -> str:
    return normalize_identity_text(value) if isinstance(value, str) else ""


class StructuredProfileComparisonService:
    """Pure deterministic classifier. It performs no persistence or provider work."""

    def compare(self, section: StructuredProfileSection | str, incoming: BaseModel, current_items_in_section: list[BaseModel]) -> StructuredProfileComparisonResult:
        section = StructuredProfileSection(section)
        incoming = typed_structured_item(section, incoming)
        current = [typed_structured_item(section, item) for item in current_items_in_section]
        incoming_fp = structured_profile_item_fingerprint(section, incoming)
        exact = [item for item in current if structured_profile_item_fingerprint(section, item) == incoming_fp]
        if len(exact) == 1:
            return self._result(section, StructuredItemRelationship.REINFORCEMENT, incoming, incoming_fp, [exact[0]], exact[0])
        if len(exact) > 1:
            return self._result(section, StructuredItemRelationship.AMBIGUOUS, incoming, incoming_fp, exact, None)

        candidates = self._candidates(section, incoming, current)
        if not candidates:
            return self._result(section, StructuredItemRelationship.NEW, incoming, incoming_fp, [], None)
        if len(candidates) > 1:
            return self._result(section, StructuredItemRelationship.AMBIGUOUS, incoming, incoming_fp, candidates, None)
        target = candidates[0]
        relationship = self._classify_fields(section, target, incoming)
        return self._result(section, relationship, incoming, incoming_fp, [target], target)

    @staticmethod
    def _candidates(section, incoming, current):
        if section is StructuredProfileSection.ACHIEVEMENTS:
            identity = _norm(incoming.text)
            return [item for item in current if identity and _norm(item.text) == identity]
        if section is StructuredProfileSection.SKILLS:
            identity = _norm(incoming.name)
            return [item for item in current if identity and _norm(item.name) == identity]
        if section is StructuredProfileSection.PROJECTS:
            identity = _norm(incoming.name)
            return [item for item in current if identity and _norm(item.name) == identity]
        if section is StructuredProfileSection.EMPLOYMENT:
            matches = []
            for item in current:
                same_employer = _norm(item.employer) == _norm(incoming.employer)
                same_title = _norm(item.title) == _norm(incoming.title)
                date_hint = _strong_employment_date_hint(item, incoming)
                if same_employer and _norm(item.employer) and (same_title or date_hint):
                    matches.append(item)
            return matches
        if section is StructuredProfileSection.EDUCATION:
            matches = [item for item in current if _norm(incoming.institution) and _norm(incoming.qualification) and _norm(item.institution) == _norm(incoming.institution) and _norm(item.qualification) == _norm(incoming.qualification)]
            if len(matches) > 1 and incoming.field_of_study:
                disambiguated = [item for item in matches if _norm(item.field_of_study) == _norm(incoming.field_of_study)]
                if disambiguated:
                    return disambiguated
            return matches
        if section is StructuredProfileSection.CREDENTIALS:
            matches = [item for item in current if _norm(incoming.name) and _norm(item.name) == _norm(incoming.name) and item.credential_type == incoming.credential_type]
            if len(matches) > 1 and incoming.issuer:
                disambiguated = [item for item in matches if _norm(item.issuer) == _norm(incoming.issuer)]
                if disambiguated:
                    return disambiguated
            return matches
        return []

    @staticmethod
    def _classify_fields(section, current, incoming):
        identity_fields = {
            StructuredProfileSection.EMPLOYMENT: {"employer"},
            StructuredProfileSection.EDUCATION: {"institution", "qualification"},
            StructuredProfileSection.CREDENTIALS: {"name", "credential_type"},
            StructuredProfileSection.SKILLS: {"name"},
            StructuredProfileSection.PROJECTS: {"name"},
            StructuredProfileSection.ACHIEVEMENTS: {"text"},
        }[section]
        precision = False
        before = current.model_dump(mode="json")
        after = incoming.model_dump(mode="json")
        for field, old in before.items():
            if field in identity_fields:
                continue
            new = after[field]
            if old == new or (isinstance(old, str) and isinstance(new, str) and _norm(old) == _norm(new)):
                continue
            if field in {"start_date", "end_date", "issued_date", "expiry_date"} and _date_adds_precision(old, new):
                precision = True
                continue
            if isinstance(old, list) and isinstance(new, list):
                old_set = {_norm(value) for value in old if _norm(value)}
                new_set = {_norm(value) for value in new if _norm(value)}
                if old_set == new_set:
                    continue
                if old_set <= new_set and new_set - old_set:
                    precision = True
                    continue
                return StructuredItemRelationship.CONFLICT
            if _empty(old) and not _empty(new):
                precision = True
                continue
            # Omission/removal and any incompatible populated values are material.
            return StructuredItemRelationship.CONFLICT
        return StructuredItemRelationship.REFINEMENT if precision else StructuredItemRelationship.REINFORCEMENT

    @staticmethod
    def _result(section, relationship, incoming, incoming_fp, candidates, target):
        matches = [StructuredProfileItemMatch(fingerprint=structured_profile_item_fingerprint(section, item), item=item) for item in candidates]
        target_fp = structured_profile_item_fingerprint(section, target) if target is not None else None
        return StructuredProfileComparisonResult(
            section=section, relationship=relationship, incoming_item=incoming,
            incoming_fingerprint=incoming_fp, candidate_matches=matches,
            target_fingerprint=target_fp, current_item=target,
        )


def _strong_employment_date_hint(left, right) -> bool:
    left_start = _years(left.start_date)
    right_start = _years(right.start_date)
    return len(left_start) == 1 and left_start == right_start


def _years(*values) -> set[str]:
    return {year for value in values if isinstance(value, str) for year in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", value)}


def _date_adds_precision(old, new) -> bool:
    if not isinstance(old, str) or not isinstance(new, str) or not old.strip() or not new.strip():
        return False
    old_years, new_years = _years(old), _years(new)
    return len(old_years) == 1 and old_years == new_years and len(new.strip()) > len(old.strip())


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, list) and not value)

from dataclasses import dataclass
import re

from app.schemas.assessment import (
    FitAssessment,
    Gap,
    GapSeverity,
    GapType,
)
from app.schemas.job import RequirementCategory, RequirementImportance
from app.schemas.matching import MatchType, RequirementMatch


@dataclass(frozen=True)
class _ScoringUnit:
    """One deterministic contribution to aggregate fit scoring."""

    importance: RequirementImportance
    score: float


class FitAssessmentService:
    ESSENTIAL_WEIGHT = 3.0
    DESIRABLE_WEIGHT = 1.0
    UNSPECIFIED_WEIGHT = 2.0
    _ELIGIBILITY_CATEGORIES = {
        RequirementCategory.LOCATION,
        RequirementCategory.WORK_AUTHORIZATION,
    }
    _MANDATORY_CONSTRAINT = re.compile(
        r"\b(must|mandatory|required|only\s+candidates|right\s+to\s+work|"
        r"eligible\s+to\s+work|no\s+sponsorship)\b",
        re.IGNORECASE,
    )
    _LICENCE_OR_REGISTRATION = re.compile(
        r"\b(licen[cs](?:e|ed|ure)|registration|registered\s+professional|"
        r"professional\s+certification|certif(?:ication|ied))\b",
        re.IGNORECASE,
    )
    _SECURITY_OR_NATIONALITY_CONSTRAINT = re.compile(
        r"\b((?:security|sc|dv)\s+clearance|developed\s+vetting|security\s+check|"
        r"citizen(?:ship)?|nationality)\b",
        re.IGNORECASE,
    )

    def assess(self, matches: list[RequirementMatch]) -> FitAssessment:
        scoring_units = self._scoring_units(matches)
        fit_score = self._weighted_score(scoring_units)

        essential_units = [
            unit
            for unit in scoring_units
            if unit.importance == RequirementImportance.ESSENTIAL
        ]

        desirable_units = [
            unit
            for unit in scoring_units
            if unit.importance == RequirementImportance.DESIRABLE
        ]

        essential_score = (
            self._simple_score(essential_units)
            if essential_units
            else None
        )
        desirable_score = (
            self._simple_score(desirable_units)
            if desirable_units
            else None
        )

        strengths = [
            match.requirement_index
            for match in matches
            if match.score >= 0.8
            and match.match_type in {
                MatchType.DEMONSTRATED,
                MatchType.TRANSFERABLE,
            }
        ]

        gaps = [
            gap
            for match in matches
            if (gap := self._classify_gap(match)) is not None
        ]

        hard_blockers = [
            gap.requirement_index
            for gap in gaps
            if gap.gap_type == GapType.HARD_BLOCKER
        ]

        return FitAssessment(
            fit_score=round(fit_score, 1),
            essential_score=(
                round(essential_score, 1)
                if essential_score is not None
                else None
            ),
            desirable_score=(
                round(desirable_score, 1)
                if desirable_score is not None
                else None
            ),
            strengths=strengths,
            gaps=gaps,
            hard_blockers=hard_blockers,
        )

    def _weighted_score(self, units: list[_ScoringUnit]) -> float:
        if not units:
            return 0.0

        weighted_total = 0.0
        weight_total = 0.0

        for unit in units:
            weight = self._weight(unit.importance)
            weighted_total += unit.score * weight
            weight_total += weight

        return (weighted_total / weight_total) * 100.0

    @staticmethod
    def _simple_score(units: list[_ScoringUnit]) -> float:
        if not units:
            return 0.0

        return (
            sum(unit.score for unit in units)
            / len(units)
            * 100.0
        )

    def _scoring_units(self, matches: list[RequirementMatch]) -> list[_ScoringUnit]:
        """Aggregate ordinary atomic criteria by one unambiguous source statement.

        A shared non-empty ``source_text`` is established extraction provenance.
        When it identifies one employer statement and all matches agree on its
        importance, that statement receives one importance weight and its atomic
        scores are averaged.  Missing/merged provenance and importance conflicts
        cannot be resolved safely by deterministic code, so they retain the
        historical per-requirement contribution.  Confirmed blockers are likewise
        kept explicit so they cannot be averaged into an ordinary capability score.
        """
        grouped: dict[str, list[RequirementMatch]] = {}
        singletons: list[RequirementMatch] = []

        for match in matches:
            source_key = self._source_group_key(match)
            if source_key is None or self._is_confirmed_hard_blocker(match):
                singletons.append(match)
            else:
                grouped.setdefault(source_key, []).append(match)

        units = [
            _ScoringUnit(
                importance=match.requirement.importance,
                score=match.score,
            )
            for match in singletons
        ]
        for source_matches in grouped.values():
            importance = source_matches[0].requirement.importance
            if any(match.requirement.importance != importance for match in source_matches):
                # PR #113 intentionally preserves conflicting source-grounded
                # labels.  Retaining distinct units avoids inventing a stronger
                # or weaker importance for the employer statement.
                units.extend(
                    _ScoringUnit(
                        importance=match.requirement.importance,
                        score=match.score,
                    )
                    for match in source_matches
                )
                continue
            units.append(
                _ScoringUnit(
                    importance=importance,
                    score=sum(match.score for match in source_matches) / len(source_matches),
                )
            )
        return units

    @staticmethod
    def _source_group_key(match: RequirementMatch) -> str | None:
        source_text = match.requirement.source_text
        if not source_text:
            return None
        normalized = " ".join(source_text.split())
        if not normalized or " / " in normalized:
            # PR #113 uses this delimiter only when duplicate extraction records
            # carried multiple supporting statements.  It is not one reliable
            # employer-source scoring unit.
            return None
        return normalized.casefold()

    def _classify_gap(
        self,
        match: RequirementMatch,
    ) -> Gap | None:
        importance = match.requirement.importance

        if self._is_confirmed_hard_blocker(match):
            return Gap(
                requirement_index=match.requirement_index,
                requirement=match.requirement,
                gap_type=GapType.HARD_BLOCKER,
                severity=GapSeverity.HIGH,
                reason=(
                    "A clearly mandatory eligibility or professional constraint "
                    "conflicts with confirmed candidate evidence."
                ),
            )

        if match.match_type == MatchType.UNKNOWN:
            return Gap(
                requirement_index=match.requirement_index,
                requirement=match.requirement,
                gap_type=GapType.EVIDENCE_GAP,
                severity=GapSeverity.MEDIUM,
                reason="Candidate eligibility is not confirmed for this requirement.",
            )

        if (
            importance == RequirementImportance.ESSENTIAL
            and match.score < 0.5
        ):
            return Gap(
                requirement_index=match.requirement_index,
                requirement=match.requirement,
                gap_type=GapType.MEANINGFUL_CAPABILITY_GAP,
                severity=GapSeverity.HIGH,
                reason="Evidence for this essential requirement is weak.",
            )

        if (
            match.match_type == MatchType.INFERRED
            and match.score < 0.6
        ):
            return Gap(
                requirement_index=match.requirement_index,
                requirement=match.requirement,
                gap_type=GapType.EVIDENCE_GAP,
                severity=(
                    GapSeverity.MEDIUM
                    if importance == RequirementImportance.ESSENTIAL
                    else GapSeverity.LOW
                ),
                reason="The capability is plausible but not sufficiently evidenced.",
            )

        if (
            importance == RequirementImportance.DESIRABLE
            and match.score < 0.5
        ):
            return Gap(
                requirement_index=match.requirement_index,
                requirement=match.requirement,
                gap_type=GapType.LEARNABLE_GAP,
                severity=GapSeverity.LOW,
                reason="This desirable capability is currently weak or developing.",
            )

        return None

    def _is_confirmed_hard_blocker(self, match: RequirementMatch) -> bool:
        """Reserve blockers for mandatory constraints with confirmed incompatibility.

        Essential describes importance to the role; it does not prove that a
        skill or experience gap makes application objectively infeasible.
        """
        if match.match_type != MatchType.INCOMPATIBLE:
            return False
        text = "\n".join(
            value for value in (match.requirement.text, match.requirement.source_text) if value
        )
        if not self._MANDATORY_CONSTRAINT.search(text):
            return False
        return (
            match.requirement.category in self._ELIGIBILITY_CATEGORIES
            or bool(self._SECURITY_OR_NATIONALITY_CONSTRAINT.search(text))
            or bool(self._LICENCE_OR_REGISTRATION.search(text))
        )

    def _weight(
        self,
        importance: RequirementImportance,
    ) -> float:
        if importance == RequirementImportance.ESSENTIAL:
            return self.ESSENTIAL_WEIGHT

        if importance == RequirementImportance.DESIRABLE:
            return self.DESIRABLE_WEIGHT

        return self.UNSPECIFIED_WEIGHT

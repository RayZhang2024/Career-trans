import re

from app.schemas.assessment import (
    FitAssessment,
    Gap,
    GapSeverity,
    GapType,
)
from app.schemas.job import RequirementCategory, RequirementImportance
from app.schemas.matching import MatchType, RequirementMatch


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
        fit_score = self._weighted_score(matches)

        essential_matches = [
            match
            for match in matches
            if match.requirement.importance == RequirementImportance.ESSENTIAL
        ]

        desirable_matches = [
            match
            for match in matches
            if match.requirement.importance == RequirementImportance.DESIRABLE
        ]

        essential_score = (
            self._simple_score(essential_matches)
            if essential_matches
            else None
        )
        desirable_score = (
            self._simple_score(desirable_matches)
            if desirable_matches
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

    def _weighted_score(self, matches: list[RequirementMatch]) -> float:
        if not matches:
            return 0.0

        weighted_total = 0.0
        weight_total = 0.0

        for match in matches:
            weight = self._weight(match.requirement.importance)
            weighted_total += match.score * weight
            weight_total += weight

        return (weighted_total / weight_total) * 100.0

    @staticmethod
    def _simple_score(matches: list[RequirementMatch]) -> float:
        if not matches:
            return 0.0

        return (
            sum(match.score for match in matches)
            / len(matches)
            * 100.0
        )

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

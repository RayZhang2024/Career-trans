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

        essential_score = self._simple_score(essential_matches)
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
            essential_score=round(essential_score, 1),
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

        if (
            importance == RequirementImportance.ESSENTIAL
            and match.match_type == MatchType.INCOMPATIBLE
        ):
            return Gap(
                requirement_index=match.requirement_index,
                requirement=match.requirement,
                gap_type=GapType.HARD_BLOCKER,
                severity=GapSeverity.HIGH,
                reason="Structured candidate eligibility conflicts with this essential requirement.",
            )

        if (
            importance == RequirementImportance.ESSENTIAL
            and match.match_type == MatchType.MISSING
            and match.score < 0.2
            and match.requirement.category
            not in {RequirementCategory.LOCATION, RequirementCategory.WORK_AUTHORIZATION}
        ):
            return Gap(
                requirement_index=match.requirement_index,
                requirement=match.requirement,
                gap_type=GapType.HARD_BLOCKER,
                severity=GapSeverity.HIGH,
                reason="An essential requirement has no supporting evidence.",
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

    def _weight(
        self,
        importance: RequirementImportance,
    ) -> float:
        if importance == RequirementImportance.ESSENTIAL:
            return self.ESSENTIAL_WEIGHT

        if importance == RequirementImportance.DESIRABLE:
            return self.DESIRABLE_WEIGHT

        return self.UNSPECIFIED_WEIGHT

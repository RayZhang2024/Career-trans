import re

from app.schemas.candidate import CandidateContext
from app.schemas.job import JobRequirement, RequirementCategory
from app.schemas.matching import (
    EvidenceRef,
    EvidenceSourceType,
    MatchType,
    RequirementMatch,
)


class FactualRequirementService:
    def can_handle(self, requirement: JobRequirement) -> bool:
        if requirement.category == RequirementCategory.SECURITY:
            return self._is_clearance_requirement(requirement)
        return requirement.category in {
            RequirementCategory.LOCATION,
            RequirementCategory.WORK_AUTHORIZATION,
        }

    def match(
        self,
        requirement_index: int,
        requirement: JobRequirement,
        candidate_context: CandidateContext,
    ) -> RequirementMatch:
        if requirement.category == RequirementCategory.WORK_AUTHORIZATION:
            return self._match_work_authorisation(
                requirement_index,
                requirement,
                candidate_context,
            )
        if requirement.category == RequirementCategory.LOCATION:
            return self._match_location(
                requirement_index,
                requirement,
                candidate_context,
            )
        if requirement.category == RequirementCategory.SECURITY:
            return self._match_security_clearance(
                requirement_index,
                requirement,
                candidate_context,
            )

        raise ValueError(
            f"Unsupported factual requirement category: {requirement.category}"
        )

    def _match_work_authorisation(
        self,
        requirement_index: int,
        requirement: JobRequirement,
        candidate_context: CandidateContext,
    ) -> RequirementMatch:
        return self._match_eligibility_values(
            requirement_index=requirement_index,
            requirement=requirement,
            candidate_values=candidate_context.eligibility.work_authorisation,
            source_ref="work_authorisation",
            label="work authorisation",
        )

    def _match_location(
        self,
        requirement_index: int,
        requirement: JobRequirement,
        candidate_context: CandidateContext,
    ) -> RequirementMatch:
        return self._match_eligibility_values(
            requirement_index=requirement_index,
            requirement=requirement,
            candidate_values=candidate_context.eligibility.locations,
            source_ref="locations",
            label="location eligibility",
        )

    def _match_security_clearance(
        self,
        requirement_index: int,
        requirement: JobRequirement,
        candidate_context: CandidateContext,
    ) -> RequirementMatch:
        candidate_values = candidate_context.eligibility.security_clearances
        if not candidate_values:
            return RequirementMatch(
                requirement_index=requirement_index,
                requirement=requirement,
                match_type=MatchType.UNKNOWN,
                score=0.0,
                evidence_ids=[],
                reasoning="Structured candidate security-clearance eligibility is insufficient to confirm this requirement.",
            )

        required_clearances = self._clearance_terms(requirement.text)
        candidate_clearances = self._clearance_terms(" ".join(candidate_values))
        if not required_clearances:
            return RequirementMatch(
                requirement_index=requirement_index,
                requirement=requirement,
                match_type=MatchType.UNKNOWN,
                score=0.0,
                evidence_ids=[],
                reasoning=(
                    "Structured candidate security-clearance eligibility cannot "
                    "confirm the named clearance requirement."
                ),
            )

        if required_clearances & candidate_clearances:
            return RequirementMatch(
                requirement_index=requirement_index,
                requirement=requirement,
                match_type=MatchType.DEMONSTRATED,
                score=1.0,
                evidence_ids=[],
                evidence_refs=[
                    EvidenceRef(
                        source_type=EvidenceSourceType.CANDIDATE_ELIGIBILITY,
                        source_ref="security_clearances",
                    )
                ],
                reasoning="Structured candidate eligibility confirms a security clearance.",
            )

        return RequirementMatch(
            requirement_index=requirement_index,
            requirement=requirement,
            match_type=MatchType.INCOMPATIBLE,
            score=0.0,
            evidence_ids=[],
            reasoning="Structured candidate security-clearance eligibility conflicts with the stated requirement.",
        )

    def _match_eligibility_values(
        self,
        *,
        requirement_index: int,
        requirement: JobRequirement,
        candidate_values: list[str],
        source_ref: str,
        label: str,
    ) -> RequirementMatch:
        required_locations = self._countries_in(requirement.text)
        candidate_locations = self._countries_in(" ".join(candidate_values))

        if required_locations and candidate_locations & required_locations:
            matched_location = sorted(candidate_locations & required_locations)[0]
            return RequirementMatch(
                requirement_index=requirement_index,
                requirement=requirement,
                match_type=MatchType.DEMONSTRATED,
                score=1.0,
                evidence_ids=[],
                evidence_refs=[
                    EvidenceRef(
                        source_type=EvidenceSourceType.CANDIDATE_ELIGIBILITY,
                        source_ref=source_ref,
                        value=self._display_country(matched_location),
                    )
                ],
                reasoning=(
                    "Structured candidate eligibility confirms "
                    f"{label} for {self._display_country(matched_location)}."
                ),
            )

        if not candidate_values or not required_locations:
            return RequirementMatch(
                requirement_index=requirement_index,
                requirement=requirement,
                match_type=MatchType.UNKNOWN,
                score=0.0,
                evidence_ids=[],
                reasoning=f"Structured candidate {label} is insufficient to confirm this requirement.",
            )

        return RequirementMatch(
            requirement_index=requirement_index,
            requirement=requirement,
            match_type=MatchType.INCOMPATIBLE,
            score=0.0,
            evidence_ids=[],
            reasoning=f"Structured candidate {label} conflicts with the stated requirement.",
        )

    @staticmethod
    def _countries_in(value: str) -> set[str]:
        normalized = value.casefold()
        aliases = {
            "united kingdom": ("united kingdom", "uk", "london", "england"),
            "united states": ("united states", "usa", "u.s.", "us"),
            "canada": ("canada",),
        }
        return {
            country
            for country, terms in aliases.items()
            if any(
                re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized)
                for term in terms
            )
        }

    @staticmethod
    def _display_country(country: str) -> str:
        return {
            "united kingdom": "United Kingdom",
            "united states": "United States",
            "canada": "Canada",
        }[country]

    @staticmethod
    def _clearance_terms(value: str) -> set[str]:
        normalized = value.casefold()
        terms = {
            "dv": ("dv", "developed vetting"),
            "sc": ("sc", "security check"),
            "top_secret": ("top secret",),
            "secret": ("secret clearance",),
        }
        return {
            clearance
            for clearance, aliases in terms.items()
            if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized) for alias in aliases)
        }

    @staticmethod
    def _is_clearance_requirement(requirement: JobRequirement) -> bool:
        text = "\n".join(
            value for value in (requirement.text, requirement.source_text) if value
        )
        return bool(re.search(r"\b(clearance|developed vetting|security check)\b", text, re.IGNORECASE))

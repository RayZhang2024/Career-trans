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
        return requirement.category in {
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

        raise ValueError(
            f"Unsupported factual requirement category: {requirement.category}"
        )

    def _match_work_authorisation(
        self,
        requirement_index: int,
        requirement: JobRequirement,
        candidate_context: CandidateContext,
    ) -> RequirementMatch:
        requirement_text = requirement.text.lower()

        authorised_countries = {
            value.lower()
            for value in candidate_context.eligibility.work_authorisation
        }

        uk_terms = {
            "uk",
            "united kingdom",
        }

        requires_uk = any(term in requirement_text for term in uk_terms)

        if requires_uk and "united kingdom" in authorised_countries:
            return RequirementMatch(
                requirement_index=requirement_index,
                requirement=requirement,
                match_type=MatchType.DEMONSTRATED,
                score=1.0,
                evidence_ids=[],
                evidence_refs=[
                    EvidenceRef(
                        source_type=EvidenceSourceType.CANDIDATE_ELIGIBILITY,
                        source_ref="work_authorisation",
                        value="United Kingdom",
                    )
                ],
                reasoning=(
                    "Structured candidate eligibility confirms work "
                    "authorisation for the United Kingdom."
                ),
            )

        return RequirementMatch(
            requirement_index=requirement_index,
            requirement=requirement,
            match_type=MatchType.MISSING,
            score=0.0,
            evidence_ids=[],
            reasoning=(
                "The structured candidate eligibility data does not confirm "
                "the required work authorisation."
            ),
        )
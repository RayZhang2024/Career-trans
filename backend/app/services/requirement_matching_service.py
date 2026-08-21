from app.agents.requirement_matching import RequirementMatcher
from app.schemas.candidate import CandidateContext
from app.schemas.job import JobProfile
from app.schemas.matching import RequirementMatchSet
from app.services.factual_requirement_service import FactualRequirementService


class RequirementMatchingService:
    def __init__(
        self,
        matcher: RequirementMatcher,
        factual_service: FactualRequirementService | None = None,
    ) -> None:
        self._matcher = matcher
        self._factual_service = factual_service or FactualRequirementService()

    def match(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateContext,
    ) -> RequirementMatchSet:
        factual_matches = []
        semantic_requirements = []
        semantic_indexes = []

        for index, requirement in enumerate(job_profile.requirements):
            if self._factual_service.can_handle(requirement):
                factual_matches.append(
                    self._factual_service.match(
                        index,
                        requirement,
                        candidate_context,
                    )
                )
            else:
                semantic_requirements.append(requirement)
                semantic_indexes.append(index)

        semantic_matches = []

        if semantic_requirements:
            semantic_job_profile = job_profile.model_copy(
                update={"requirements": semantic_requirements}
            )

            semantic_result = self._matcher.match(
                semantic_job_profile,
                candidate_context,
            )

            for local_match, original_index in zip(
                semantic_result.matches,
                semantic_indexes,
                strict=True,
            ):
                local_match.requirement_index = original_index
                local_match.requirement = job_profile.requirements[original_index]
                semantic_matches.append(local_match)

        combined = factual_matches + semantic_matches
        combined.sort(key=lambda item: item.requirement_index)

        return RequirementMatchSet(matches=combined)
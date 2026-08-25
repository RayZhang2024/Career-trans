from app.agents.requirement_matching import RequirementMatcher
from app.schemas.candidate import CandidateContext
from app.schemas.job import JobProfile
from app.schemas.matching import (
    EvidenceRef,
    EvidenceSourceType,
    RequirementMatch,
    RequirementMatchSet,
)
from app.services.factual_requirement_service import FactualRequirementService
from app.services.candidate_profile_compaction import candidate_matching_profile


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
        factual_matches: list[RequirementMatch] = []
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

        semantic_matches: list[RequirementMatch] = []

        if semantic_requirements:
            semantic_job_profile = job_profile.model_copy(
                update={"requirements": semantic_requirements}
            )

            semantic_result = self._matcher.match(
                semantic_job_profile,
                candidate_matching_profile(candidate_context, semantic_job_profile),
            )

            for local_match in semantic_result.matches:
                original_index = semantic_indexes[local_match.requirement_index]
                local_match.requirement_index = original_index
                local_match.requirement = job_profile.requirements[original_index]
                local_match.evidence_refs = self._career_evidence_refs(local_match)
                semantic_matches.append(local_match)

        combined = factual_matches + semantic_matches
        combined.sort(key=lambda item: item.requirement_index)

        return RequirementMatchSet(matches=combined)

    @staticmethod
    def _career_evidence_refs(match: RequirementMatch) -> list[EvidenceRef]:
        """Convert validated career evidence IDs into typed provenance references.

        The LLM remains responsible for selecting relevant evidence IDs. Provenance
        objects are generated deterministically from those IDs so the API does not
        depend on the model reproducing the same information in two formats.
        """
        return [
            EvidenceRef(
                source_type=EvidenceSourceType.CAREER_EVIDENCE,
                source_ref=evidence_id,
            )
            for evidence_id in match.evidence_ids
        ]

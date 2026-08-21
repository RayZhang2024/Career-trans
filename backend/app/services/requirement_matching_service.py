from app.agents.requirement_matching import RequirementMatcher
from app.schemas.candidate import CandidateContext
from app.schemas.job import JobProfile
from app.schemas.matching import RequirementMatchSet


class RequirementMatchingService:
    def __init__(self, matcher: RequirementMatcher) -> None:
        self._matcher = matcher

    def match(
        self,
        job_profile: JobProfile,
        candidate_context: CandidateContext,
    ) -> RequirementMatchSet:
        return self._matcher.match(job_profile, candidate_context)

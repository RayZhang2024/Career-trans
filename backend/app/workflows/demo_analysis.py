from pathlib import Path

from app.schemas.candidate import CandidateContext
from app.schemas.demo import DemoAnalyseAndMatchResponse
from app.services.career_assessment_service import CareerAssessmentService
from app.services.candidate_context_loader import MarkdownCandidateContextLoader
from app.services.fit_assessment_service import FitAssessmentService
from app.services.job_analysis_service import JobAnalysisService
from app.services.requirement_matching_service import RequirementMatchingService
from app.services.recommendation_service import RecommendationService


def default_demo_profile_dir() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "resources"
        / "examples"
        / "ray_demo"
    )


class DemoAnalysisWorkflow:
    """Development-only end-to-end workflow using a repository demo candidate."""

    def __init__(
        self,
        *,
        job_analysis_service: JobAnalysisService,
        requirement_matching_service: RequirementMatchingService,
        fit_assessment_service: FitAssessmentService,
        career_assessment_service: CareerAssessmentService,
        recommendation_service: RecommendationService,
        candidate_loader: MarkdownCandidateContextLoader | None = None,
        profile_dir: Path | None = None,
    ) -> None:
        self._job_analysis_service = job_analysis_service
        self._requirement_matching_service = requirement_matching_service
        self._fit_assessment_service = fit_assessment_service
        self._career_assessment_service = career_assessment_service
        self._recommendation_service = recommendation_service
        self._candidate_loader = candidate_loader or MarkdownCandidateContextLoader()
        self._profile_dir = profile_dir or default_demo_profile_dir()

    def run(self, job_text: str) -> DemoAnalyseAndMatchResponse:
        candidate_context = self._load_candidate()
        job_profile = self._job_analysis_service.analyse_text(job_text)
        match_set = self._requirement_matching_service.match(
            job_profile,
            candidate_context,
        )
        fit_assessment = self._fit_assessment_service.assess(
            match_set.matches
        )
        career_assessment = self._career_assessment_service.assess(
            job_profile,
            candidate_context,
            fit_assessment,
        )
        recommendation_assessment = self._recommendation_service.assess(
            fit_assessment,
            career_assessment,
        )

        return DemoAnalyseAndMatchResponse(
            candidate_source=candidate_context.source_name or self._profile_dir.name,
            evidence_count=len(candidate_context.evidence),
            job_profile=job_profile,
            matches=match_set.matches,
            fit_assessment=fit_assessment,
            career_assessment=career_assessment,
            recommendation_assessment=recommendation_assessment,
        )

    def _load_candidate(self) -> CandidateContext:
        return self._candidate_loader.load(self._profile_dir)

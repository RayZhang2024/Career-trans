from pathlib import Path

from app.schemas.candidate import CandidateContext
from app.schemas.demo import DemoAnalyseAndMatchResponse
from app.services.candidate_context_loader import MarkdownCandidateContextLoader
from app.workflows.career_analysis_graph import CareerAnalysisGraph


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
        career_analysis_graph: CareerAnalysisGraph,
        candidate_loader: MarkdownCandidateContextLoader | None = None,
        profile_dir: Path | None = None,
    ) -> None:
        self._career_analysis_graph = career_analysis_graph
        self._candidate_loader = candidate_loader or MarkdownCandidateContextLoader()
        self._profile_dir = profile_dir or default_demo_profile_dir()

    def run(self, job_text: str) -> DemoAnalyseAndMatchResponse:
        candidate_context = self._load_candidate()
        state = self._career_analysis_graph.invoke(
            job_text=job_text,
            candidate_context=candidate_context,
        )

        return DemoAnalyseAndMatchResponse(
            candidate_source=candidate_context.source_name or self._profile_dir.name,
            evidence_count=len(candidate_context.evidence),
            job_profile=state["job_profile"],
            matches=state["requirement_matches"],
            fit_assessment=state["fit_assessment"],
            career_assessment=state["career_assessment"],
            recommendation_assessment=state["recommendation_assessment"],
        )

    def _load_candidate(self) -> CandidateContext:
        return self._candidate_loader.load(self._profile_dir)

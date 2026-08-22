import logging

from app.agents.job_archetype import JobArchetypeAgent
from app.agents.job_relevance import JobRelevanceAgent
from app.schemas.discovery import JobListing
from app.schemas.job_ranking import (
    JobRankingFailure,
    JobRankingRequest,
    JobRankingResponse,
    RankedJobOpportunity,
    SemanticScreeningDiagnostic,
)
from app.schemas.recommendation import Recommendation
from app.services.job_ranking_gate_service import JobRankingGateService
from app.services.posting_legitimacy_service import PostingLegitimacyService
from app.workflows.career_analysis_graph import CareerAnalysisGraph

logger = logging.getLogger(__name__)


class JobRankingService:
    def __init__(self, *, relevance_agent: JobRelevanceAgent, archetype_agent: JobArchetypeAgent, career_analysis_graph: CareerAnalysisGraph, gate_service: JobRankingGateService | None = None, legitimacy_service: PostingLegitimacyService | None = None) -> None:
        self._relevance_agent = relevance_agent
        self._archetype_agent = archetype_agent
        self._career_analysis_graph = career_analysis_graph
        self._gate_service = gate_service or JobRankingGateService()
        self._legitimacy_service = legitimacy_service or PostingLegitimacyService()

    def rank(self, request: JobRankingRequest) -> JobRankingResponse:
        gated, gated_out_count = self._gate_service.gate(request.jobs)
        failures: list[JobRankingFailure] = []
        semantic_screening: list[SemanticScreeningDiagnostic] = []
        screened = []
        semantic_candidates = self._select_semantic_candidates(
            gated,
            request.max_semantic_candidates,
        )
        for index, job in semantic_candidates:
            try:
                relevance = self._relevance_agent.assess(job, request.candidate_context)
            except Exception:
                failures.append(JobRankingFailure(job=job, stage="semantic_relevance", error="Relevance screening failed."))
                semantic_screening.append(
                    SemanticScreeningDiagnostic(
                        job=job,
                        failure_stage="semantic_relevance",
                        error="Relevance screening failed.",
                    )
                )
                continue
            if relevance.relevant and relevance.score >= request.min_relevance_score:
                try:
                    archetype = self._archetype_agent.classify(job)
                except Exception:
                    failures.append(JobRankingFailure(job=job, stage="role_archetype", error="Role archetype classification failed."))
                    semantic_screening.append(
                        SemanticScreeningDiagnostic(
                            job=job,
                            relevance=relevance,
                            failure_stage="role_archetype",
                            error="Role archetype classification failed.",
                        )
                    )
                    continue
                semantic_screening.append(
                    SemanticScreeningDiagnostic(
                        job=job,
                        relevance=relevance,
                        archetype=archetype,
                    )
                )
                screened.append((index, job, relevance, archetype))
            else:
                semantic_screening.append(
                    SemanticScreeningDiagnostic(job=job, relevance=relevance)
                )

        finalists = sorted(screened, key=lambda item: (-item[2].score, item[0]))[:request.max_full_analyses]
        opportunities: list[tuple[int, RankedJobOpportunity]] = []
        for index, job, relevance, archetype in finalists:
            try:
                state = self._career_analysis_graph.invoke(job_text=job.description or "", candidate_context=request.candidate_context)
                opportunity = RankedJobOpportunity(job=job, relevance=relevance, archetype=archetype, fit_assessment=state["fit_assessment"], career_assessment=state["career_assessment"], recommendation_assessment=state["recommendation_assessment"], legitimacy=self._legitimacy_service.assess(job), rank=0)
            except Exception as exc:
                logger.exception("Career analysis failed for a public job listing.")
                failures.append(
                    JobRankingFailure(
                        job=job,
                        stage="career_analysis",
                        error=self._failure_error("Career analysis failed", exc),
                    )
                )
                continue
            opportunities.append((index, opportunity))

        priority = {Recommendation.APPLY: 0, Recommendation.CONSIDER: 1, Recommendation.SKIP: 2}
        opportunities.sort(key=lambda item: (priority[item[1].recommendation_assessment.recommendation], -item[1].fit_assessment.fit_score, -item[1].career_assessment.career_alignment_score, -item[1].relevance.score, item[0]))
        results = [opportunity.model_copy(update={"rank": rank}) for rank, (_, opportunity) in enumerate(opportunities, start=1)]
        return JobRankingResponse(discovered_count=len(request.jobs), gated_out_count=gated_out_count, relevance_screened_count=len(semantic_candidates), finalist_count=len(finalists), analysed_count=len(results), semantic_screening=semantic_screening, results=results, failures=failures)

    @staticmethod
    def _select_semantic_candidates(
        jobs: list[tuple[int, JobListing]],
        limit: int,
    ) -> list[tuple[int, JobListing]]:
        """Apply the semantic cost cap fairly across companies in input order."""
        groups: dict[str, list[tuple[int, JobListing]]] = {}
        for index, job in jobs:
            key = job.company or job.source
            groups.setdefault(key, []).append((index, job))

        selected: list[tuple[int, JobListing]] = []
        while groups and len(selected) < limit:
            for key in list(groups):
                selected.append(groups[key].pop(0))
                if not groups[key]:
                    del groups[key]
                if len(selected) == limit:
                    break
        return selected

    @staticmethod
    def _failure_error(prefix: str, exc: Exception) -> str:
        """Preserve a safe exception class chain without returning private inputs."""
        root = exc
        while root.__cause__ is not None:
            root = root.__cause__
        if root is exc:
            return f"{prefix}: {type(exc).__name__}."
        return f"{prefix}: {type(exc).__name__} (caused by {type(root).__name__})."
import logging

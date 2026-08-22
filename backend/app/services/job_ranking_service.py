from app.agents.job_archetype import JobArchetypeAgent
from app.agents.job_relevance import JobRelevanceAgent
from app.schemas.job_ranking import (
    JobRankingFailure,
    JobRankingRequest,
    JobRankingResponse,
    RankedJobOpportunity,
)
from app.schemas.recommendation import Recommendation
from app.services.job_ranking_gate_service import JobRankingGateService
from app.services.posting_legitimacy_service import PostingLegitimacyService
from app.workflows.career_analysis_graph import CareerAnalysisGraph


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
        screened = []
        for index, job in gated[:request.max_semantic_candidates]:
            try:
                relevance = self._relevance_agent.assess(job, request.candidate_context)
            except Exception:
                failures.append(JobRankingFailure(job=job, stage="semantic_relevance", error="Relevance screening failed."))
                continue
            if relevance.relevant and relevance.score >= request.min_relevance_score:
                try:
                    archetype = self._archetype_agent.classify(job)
                except Exception:
                    failures.append(JobRankingFailure(job=job, stage="role_archetype", error="Role archetype classification failed."))
                    continue
                screened.append((index, job, relevance, archetype))

        finalists = sorted(screened, key=lambda item: (-item[2].score, item[0]))[:request.max_full_analyses]
        opportunities: list[tuple[int, RankedJobOpportunity]] = []
        for index, job, relevance, archetype in finalists:
            try:
                state = self._career_analysis_graph.invoke(job_text=job.description or "", candidate_context=request.candidate_context)
                opportunity = RankedJobOpportunity(job=job, relevance=relevance, archetype=archetype, fit_assessment=state["fit_assessment"], career_assessment=state["career_assessment"], recommendation_assessment=state["recommendation_assessment"], legitimacy=self._legitimacy_service.assess(job), rank=0)
            except Exception:
                failures.append(JobRankingFailure(job=job, stage="career_analysis", error="Career analysis failed."))
                continue
            opportunities.append((index, opportunity))

        priority = {Recommendation.APPLY: 0, Recommendation.CONSIDER: 1, Recommendation.SKIP: 2}
        opportunities.sort(key=lambda item: (priority[item[1].recommendation_assessment.recommendation], -item[1].fit_assessment.fit_score, -item[1].career_assessment.career_alignment_score, -item[1].relevance.score, item[0]))
        results = [opportunity.model_copy(update={"rank": rank}) for rank, (_, opportunity) in enumerate(opportunities, start=1)]
        return JobRankingResponse(discovered_count=len(request.jobs), gated_out_count=gated_out_count, relevance_screened_count=min(len(gated), request.max_semantic_candidates), finalist_count=len(finalists), analysed_count=len(results), results=results, failures=failures)

import logging

from app.agents.requirement_matching import RequirementMatchingError
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

        screened.sort(key=lambda item: (-item[2].score, item[0]))
        finalist_count = 0
        opportunities: list[tuple[int, RankedJobOpportunity]] = []
        for index, job, relevance, archetype in screened:
            if not job.description or not job.description.strip():
                failures.append(self._insufficient_detail_failure(job, "missing or blank job description"))
                continue
            if finalist_count >= request.max_full_analyses:
                continue
            # The deep-analysis cap bounds graph invocation attempts, including
            # malformed or zero-requirement extraction outcomes. A description
            # known to be blank is handled above without a graph call.
            finalist_count += 1
            try:
                state = self._career_analysis_graph.invoke(
                    job_text=job.description,
                    candidate_context=request.candidate_context,
                    job_listing=job,
                )
                job_profile = state.get("job_profile")
                if job_profile is not None and not job_profile.requirements:
                    failures.append(self._insufficient_detail_failure(job, "no extractable requirements"))
                    continue
                opportunity = RankedJobOpportunity(
                    job=job,
                    relevance=relevance,
                    archetype=archetype,
                    fit_assessment=state["fit_assessment"],
                    career_assessment=state["career_assessment"],
                    recommendation_assessment=state["recommendation_assessment"],
                    legitimacy=self._legitimacy_service.assess(job),
                    rank=0,
                    job_profile=state.get("job_profile"),
                    requirement_matches=state.get("requirement_matches", []),
                )
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
        return JobRankingResponse(discovered_count=len(request.jobs), gated_out_count=gated_out_count, relevance_screened_count=len(semantic_candidates), finalist_count=finalist_count, analysed_count=len(results), semantic_screening=semantic_screening, results=results, failures=failures)

    @staticmethod
    def _select_semantic_candidates(
        jobs: list[tuple[int, JobListing]],
        limit: int,
    ) -> list[tuple[int, JobListing]]:
        """Apply the semantic cost cap fairly across companies and source fronts.

        Source/provider (and existing discovery provenance) are deterministic
        allocation keys only. They never judge a job's semantic relevance.
        """
        groups: dict[str, dict[str, list[tuple[int, JobListing]]]] = {}
        for index, job in jobs:
            company_key = (job.company or job.source).casefold()
            source_key = JobRankingService._semantic_source_key(job)
            groups.setdefault(company_key, {}).setdefault(source_key, []).append((index, job))

        selected: list[tuple[int, JobListing]] = []
        while groups and len(selected) < limit:
            for company_key in list(groups):
                source_groups = groups[company_key]
                source_key = next(iter(source_groups))
                selected.append(source_groups[source_key].pop(0))
                if not source_groups[source_key]:
                    del source_groups[source_key]
                else:
                    source_groups[source_key] = source_groups.pop(source_key)
                if not source_groups:
                    del groups[company_key]
                if len(selected) == limit:
                    break
        return selected

    @staticmethod
    def _semantic_source_key(job: JobListing) -> str:
        if job.provenance is not None:
            if job.provenance.discovered_via:
                return f"provenance:{job.provenance.discovered_via.casefold()}"
            if job.provenance.source_ref:
                return f"provenance:{job.provenance.source_ref.casefold()}"
        return ":".join(
            value.casefold()
            for value in (job.source, job.source_token or "")
        )

    @staticmethod
    def _failure_error(prefix: str, exc: Exception) -> str:
        """Preserve a safe exception class chain without returning private inputs."""
        matching_kind = JobRankingService._requirement_matching_failure_kind(exc)
        if matching_kind is not None:
            return f"{prefix}: requirement_matching: {matching_kind}."
        root = exc
        while root.__cause__ is not None:
            root = root.__cause__
        if root is exc:
            return f"{prefix}: {type(exc).__name__}."
        return f"{prefix}: {type(exc).__name__} (caused by {type(root).__name__})."

    @staticmethod
    def _requirement_matching_failure_kind(exc: Exception) -> str | None:
        """Find a safe matching category without surfacing exception messages."""
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, RequirementMatchingError):
                return current.safe_kind
            current = current.__cause__ or current.__context__
        return None

    @staticmethod
    def _insufficient_detail_failure(job: JobListing, reason: str) -> JobRankingFailure:
        return JobRankingFailure(
            job=job,
            stage="insufficient_job_detail",
            error=f"Job detail is insufficient for deep fit assessment: {reason}.",
        )
import logging

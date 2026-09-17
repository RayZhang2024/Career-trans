from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.agents.job_extraction import JobExtractionError
from app.agents.requirement_matching import RequirementMatchingError
from app.api.deps import (
    CurrentUser,
    PersistedCandidateContext,
    get_job_analysis_service,
    get_job_ranking_service,
    get_ats_resolver_service,
    get_company_source_discovery_service,
    get_employer_universe_service,
    get_discover_and_rank_service,
    get_agentic_job_discovery_service,
    get_job_discovery_service,
    get_external_discovery_import_service,
    get_structured_ats_discovery_service,
    get_opportunity_inbox_service,
    get_job_detail_enrichment_service,
    get_requirement_matching_service,
    get_user_job_discovery_service,
)
from app.schemas.discovery import (
    JobDiscoveryResponse,
    JobSearchQuery,
)
from app.schemas.discovery_pipeline import DiscoverAndRankRequest, DiscoverAndRankResponse
from app.schemas.structured_ats_discovery import (
    StructuredAtsDiscoveryRequest,
    StructuredAtsDiscoveryResponse,
)
from app.schemas.external_discovery import (
    ExternalDiscoveryImportRequest,
    ExternalDiscoveryImportResponse,
    ExternalDiscoverySearchContextRequest,
    ExternalDiscoverySearchContextResponse,
)
from app.schemas.agentic_discovery import (
    AgenticDiscoveryMeRequest,
    AgenticDiscoveryRequest,
    AgenticDiscoveryResponse,
)
from app.schemas.employer_universe import EmployerUniverseRequest, EmployerUniverseResponse
from app.schemas.job_sources import (
    AtsResolutionRequest,
    AtsResolutionResponse,
    CompanySourceDiscoveryRequest,
    CompanySourceDiscoveryResponse,
)
from app.schemas.job_ranking import JobRankingMeRequest, JobRankingRequest, JobRankingResponse
from app.schemas.opportunity_inbox import OpportunityInboxResponse
from app.schemas.job_enrichment import JobEnrichmentRequest, JobEnrichmentResponse
from app.schemas.user_job_discovery import DiscoveryRunCreateRequest, DiscoveryRunRead, UserOpportunityResponse
from app.schemas.job import JobAnalysisRequest, JobAnalysisResponse
from app.schemas.matching import JobMatchMeRequest, JobMatchRequest, JobMatchResponse
from app.services.job_analysis_service import JobAnalysisService
from app.services.ats_resolver_service import AtsResolverService
from app.services.company_source_discovery_service import CompanySourceDiscoveryService
from app.services.employer_universe_service import EmployerUniverseService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.agentic_job_discovery_service import AgenticJobDiscoveryService
from app.services.discover_and_rank_service import DiscoverAndRankService
from app.services.external_discovery_import_service import ExternalDiscoveryImportService
from app.services.structured_ats_discovery_service import StructuredAtsDiscoveryService
from app.services.job_ranking_service import JobRankingService
from app.services.opportunity_inbox_service import OpportunityInboxService
from app.services.job_detail_enrichment_service import JobDetailEnrichmentService
from app.services.requirement_matching_service import RequirementMatchingService
from app.services.user_job_discovery_service import UserJobDiscoveryService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/discovery-runs", response_model=DiscoveryRunRead, status_code=status.HTTP_201_CREATED)
def create_discovery_run(
    payload: DiscoveryRunCreateRequest,
    current_user: CurrentUser,
    service: UserJobDiscoveryService = Depends(get_user_job_discovery_service),
) -> DiscoveryRunRead:
    """Evaluate canonical persisted jobs for one user, reusing only current complete results."""
    try:
        return service.start(current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/discovery-runs", response_model=list[DiscoveryRunRead])
def list_discovery_runs(
    current_user: CurrentUser,
    service: UserJobDiscoveryService = Depends(get_user_job_discovery_service),
) -> list[DiscoveryRunRead]:
    return service.list_runs(current_user.id)


@router.get("/discovery-runs/{run_id}", response_model=DiscoveryRunRead)
def get_discovery_run(
    run_id: str,
    current_user: CurrentUser,
    service: UserJobDiscoveryService = Depends(get_user_job_discovery_service),
) -> DiscoveryRunRead:
    try:
        return service.get_run(current_user.id, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Discovery run not found.") from exc


@router.get("/opportunities", response_model=UserOpportunityResponse)
def list_current_opportunities(
    current_user: CurrentUser,
    service: UserJobDiscoveryService = Depends(get_user_job_discovery_service),
) -> UserOpportunityResponse:
    return service.current_opportunities(current_user.id)


@router.post(
    "/external-discovery/search-context",
    response_model=ExternalDiscoverySearchContextResponse,
    status_code=status.HTTP_200_OK,
)
def external_discovery_search_context(
    payload: ExternalDiscoverySearchContextRequest,
    candidate_context: PersistedCandidateContext,
    service: ExternalDiscoveryImportService = Depends(get_external_discovery_import_service),
) -> ExternalDiscoverySearchContextResponse:
    """Return only the authenticated user's compact context needed by an external runtime."""
    return service.search_context(candidate_context, payload)


@router.post(
    "/import-discovered",
    response_model=ExternalDiscoveryImportResponse,
    status_code=status.HTTP_200_OK,
)
def import_external_discoveries(
    payload: ExternalDiscoveryImportRequest,
    current_user: CurrentUser,
    service: ExternalDiscoveryImportService = Depends(get_external_discovery_import_service),
) -> ExternalDiscoveryImportResponse:
    """Persist bounded external-agent evidence without executing a runtime or web search."""
    del current_user  # Authentication gates the endpoint; imported public jobs remain shared records.
    return service.import_jobs(payload)


@router.post(
    "/discover-ats",
    response_model=StructuredAtsDiscoveryResponse,
    status_code=status.HTTP_200_OK,
)
def discover_known_ats_sources(
    payload: StructuredAtsDiscoveryRequest,
    current_user: CurrentUser,
    service: StructuredAtsDiscoveryService = Depends(get_structured_ats_discovery_service),
) -> StructuredAtsDiscoveryResponse:
    """Scan persisted resolved ATS sources without web search, enrichment, or ranking."""
    del current_user
    return service.discover(payload)


@router.get("/inbox", response_model=OpportunityInboxResponse, status_code=status.HTTP_200_OK)
def list_opportunity_inbox(
    current_user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    service: OpportunityInboxService = Depends(get_opportunity_inbox_service),
) -> OpportunityInboxResponse:
    """Inspect recent shared external discoveries without starting a runtime or ranking job."""
    del current_user  # Authentication gates public-job inspection; candidate data is never returned.
    return service.list_recent(limit=limit)


@router.post("/enrich-imported", response_model=JobEnrichmentResponse, status_code=status.HTTP_200_OK)
def enrich_imported_jobs(
    payload: JobEnrichmentRequest,
    current_user: CurrentUser,
    service: JobDetailEnrichmentService = Depends(get_job_detail_enrichment_service),
) -> JobEnrichmentResponse:
    """Explicitly enrich recent persisted external vacancies from their known public URLs."""
    del current_user
    return service.enrich_recent(limit=payload.limit)


@router.post(
    "/sources/resolve",
    response_model=AtsResolutionResponse,
    status_code=status.HTTP_200_OK,
)
def resolve_job_sources(
    payload: AtsResolutionRequest,
    service: AtsResolverService = Depends(get_ats_resolver_service),
) -> AtsResolutionResponse:
    """Resolve company names to verified public ATS sources without scanning websites."""
    return service.resolve(payload.companies)


@router.post(
    "/companies/resolve-sources",
    response_model=CompanySourceDiscoveryResponse,
    status_code=status.HTTP_200_OK,
)
def resolve_company_sources(
    payload: CompanySourceDiscoveryRequest,
    service: CompanySourceDiscoveryService = Depends(get_company_source_discovery_service),
) -> CompanySourceDiscoveryResponse:
    """Reuse known public career sources or resolve and persist new mappings."""
    return service.resolve_sources(payload)


@router.post(
    "/companies/build-universe",
    response_model=EmployerUniverseResponse,
    status_code=status.HTTP_200_OK,
)
def build_employer_universe(
    payload: EmployerUniverseRequest,
    service: EmployerUniverseService = Depends(get_employer_universe_service),
) -> EmployerUniverseResponse:
    """Build a deterministic, source-provenance-aware company universe."""
    return service.build(payload)


@router.post("/rank", response_model=JobRankingResponse, status_code=status.HTTP_200_OK)
def rank_jobs(
    payload: JobRankingRequest,
    service: JobRankingService = Depends(get_job_ranking_service),
) -> JobRankingResponse:
    """Rank already-discovered jobs after an economical semantic funnel."""
    return service.rank(payload)


@router.post("/rank-me", response_model=JobRankingResponse, status_code=status.HTTP_200_OK)
def rank_jobs_for_current_user(
    payload: JobRankingMeRequest,
    candidate_context: PersistedCandidateContext,
    service: JobRankingService = Depends(get_job_ranking_service),
) -> JobRankingResponse:
    """Rank jobs against only the authenticated user's confirmed CV-derived context."""
    return service.rank(
        JobRankingRequest(
            jobs=payload.jobs,
            candidate_context=candidate_context,
            max_semantic_candidates=payload.max_semantic_candidates,
            max_full_analyses=payload.max_full_analyses,
            min_relevance_score=payload.min_relevance_score,
        )
    )


@router.post(
    "/discover",
    response_model=JobDiscoveryResponse,
    status_code=status.HTTP_200_OK,
)
def discover_jobs(
    payload: JobSearchQuery,
    service: JobDiscoveryService = Depends(get_job_discovery_service),
) -> JobDiscoveryResponse:
    """Discover public ATS listings without performing full career analysis."""
    return service.discover(payload)


@router.post(
    "/discover-agentic",
    response_model=AgenticDiscoveryResponse,
    status_code=status.HTTP_200_OK,
)
def discover_agentic_jobs(
    payload: AgenticDiscoveryRequest,
    service: AgenticJobDiscoveryService = Depends(get_agentic_job_discovery_service),
) -> AgenticDiscoveryResponse:
    """Search a bounded public-web frontier, then persist non-authoritative vacancies."""
    return service.discover(payload)


@router.post(
    "/discover-agentic-me",
    response_model=AgenticDiscoveryResponse,
    status_code=status.HTTP_200_OK,
)
def discover_agentic_jobs_for_current_user(
    payload: AgenticDiscoveryMeRequest,
    candidate_context: PersistedCandidateContext,
    service: AgenticJobDiscoveryService = Depends(get_agentic_job_discovery_service),
) -> AgenticDiscoveryResponse:
    """Discover bounded public vacancies using only the caller's confirmed context."""
    return service.discover(
        AgenticDiscoveryRequest(
            candidate_context=candidate_context,
            **payload.model_dump(),
        )
    )


@router.post(
    "/discover-and-rank",
    response_model=DiscoverAndRankResponse,
    status_code=status.HTTP_200_OK,
)
def discover_and_rank_jobs(
    payload: DiscoverAndRankRequest,
    service: DiscoverAndRankService = Depends(get_discover_and_rank_service),
) -> DiscoverAndRankResponse:
    """Resolve structured ATS sources, persist their lifecycle, then rank the cheap-filtered jobs."""
    return service.discover_and_rank(payload)


@router.post(
    "/analyse",
    response_model=JobAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
def analyse_job(
    payload: JobAnalysisRequest,
    service: JobAnalysisService = Depends(get_job_analysis_service),
) -> JobAnalysisResponse:
    """Convert raw job-description text into a structured JobProfile."""
    try:
        profile = service.analyse_text(payload.job_text)
    except JobExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return JobAnalysisResponse(job_profile=profile)


@router.post(
    "/match",
    response_model=JobMatchResponse,
    status_code=status.HTTP_200_OK,
)
def match_job(
    payload: JobMatchRequest,
    service: RequirementMatchingService = Depends(get_requirement_matching_service),
) -> JobMatchResponse:
    """Match a structured job against a supplied candidate context.

    This endpoint is intentionally user-agnostic. During development the context may
    come from demo resources; later it will be assembled from authenticated user data.
    """
    try:
        result = service.match(payload.job_profile, payload.candidate_context)
    except RequirementMatchingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return JobMatchResponse(matches=result.matches)


@router.post("/match-me", response_model=JobMatchResponse, status_code=status.HTTP_200_OK)
def match_job_for_current_user(
    payload: JobMatchMeRequest,
    candidate_context: PersistedCandidateContext,
    service: RequirementMatchingService = Depends(get_requirement_matching_service),
) -> JobMatchResponse:
    """Match a job using confirmed persisted evidence for the authenticated user."""
    try:
        result = service.match(payload.job_profile, candidate_context)
    except RequirementMatchingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return JobMatchResponse(matches=result.matches)

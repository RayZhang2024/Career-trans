from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.agents.career_alignment import OpenAICareerAlignmentAgent
from app.agents.job_archetype import OpenAIJobArchetypeAgent
from app.agents.job_extraction import OpenAIJobExtractor
from app.agents.job_relevance import OpenAIJobRelevanceAgent
from app.agents.requirement_matching import OpenAIRequirementMatcher
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User
from app.services.auth_service import get_user_by_id
from app.services.career_assessment_service import CareerAssessmentService
from app.services.fit_assessment_service import FitAssessmentService
from app.services.job_analysis_service import JobAnalysisService
from app.services.ats_resolver_service import AtsResolverService
from app.services.company_source_discovery_service import CompanySourceDiscoveryService
from app.services.employer_universe_service import EmployerUniverseService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.broad_job_discovery_service import BroadJobDiscoveryService
from app.services.discover_and_rank_service import DiscoverAndRankService
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.job_ranking_service import JobRankingService
from app.services.requirement_matching_service import RequirementMatchingService
from app.providers.jobs.greenhouse import GreenhouseJobSource
from app.providers.jobs.ashby import AshbyJobSource
from app.providers.jobs.lever import LeverJobSource
from app.providers.jobs.recruitee import RecruiteeJobSource
from app.providers.jobs.smartrecruiters import SmartRecruitersJobSource
from app.providers.jobs.adzuna import AdzunaJobSource
from app.providers.jobs.probes.ashby import AshbyJobSourceProbe
from app.providers.jobs.probes.greenhouse import GreenhouseJobSourceProbe
from app.providers.jobs.probes.lever import LeverJobSourceProbe
from app.providers.jobs.probes.recruitee import RecruiteeJobSourceProbe
from app.providers.jobs.probes.smartrecruiters import SmartRecruitersJobSourceProbe
from app.services.recommendation_service import RecommendationService
from app.workflows.career_analysis_graph import CareerAnalysisGraph
from app.workflows.demo_analysis import DemoAnalysisWorkflow

settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")

DbSession = Annotated[Session, Depends(get_db)]



def get_current_user(
    db: DbSession,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    user_id = decode_access_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@lru_cache
def get_job_analysis_service() -> JobAnalysisService:
    settings = get_settings()

    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Job analysis is not configured. "
                "Set OPENAI_API_KEY in backend/.env."
            ),
        )

    extractor = OpenAIJobExtractor(
        api_key=settings.openai_api_key,
        model=settings.openai_job_extraction_model,
    )

    return JobAnalysisService(extractor=extractor)


@lru_cache
def get_requirement_matching_service() -> RequirementMatchingService:
    settings = get_settings()

    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Requirement matching is not configured. "
                "Set OPENAI_API_KEY in backend/.env."
            ),
        )

    matcher = OpenAIRequirementMatcher(
        api_key=settings.openai_api_key,
        model=settings.openai_requirement_matching_model,
    )
    return RequirementMatchingService(matcher=matcher)


def _configured_direct_job_sources(settings):
    providers = []
    greenhouse_tokens = settings.configured_tokens(settings.greenhouse_board_tokens)
    lever_tokens = settings.configured_tokens(settings.lever_site_tokens)
    smartrecruiters_ids = settings.configured_tokens(settings.smartrecruiters_company_ids)
    recruitee_tokens = settings.configured_tokens(settings.recruitee_company_tokens)
    if greenhouse_tokens:
        providers.append(GreenhouseJobSource(greenhouse_tokens))
    ashby_tokens = settings.configured_tokens(settings.ashby_board_tokens)
    if ashby_tokens:
        providers.append(AshbyJobSource(ashby_tokens))
    if lever_tokens:
        providers.append(LeverJobSource(lever_tokens))
    if smartrecruiters_ids:
        providers.append(SmartRecruitersJobSource(smartrecruiters_ids))
    if recruitee_tokens:
        providers.append(RecruiteeJobSource(recruitee_tokens))
    return providers


@lru_cache
def get_job_discovery_service() -> JobDiscoveryService:
    settings = get_settings()
    return JobDiscoveryService(providers=_configured_direct_job_sources(settings))


def get_broad_job_discovery_service(db: DbSession) -> BroadJobDiscoveryService:
    settings = get_settings()
    if not settings.adzuna_app_id or not settings.adzuna_app_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Broad job discovery is not configured. Set ADZUNA_APP_ID and ADZUNA_APP_KEY in backend/.env.",
        )
    # Direct configured boards remain in the same collection so the existing
    # deterministic deduplication retains one normalized listing per job.
    providers = _configured_direct_job_sources(settings)
    providers.append(
        AdzunaJobSource(app_id=settings.adzuna_app_id, app_key=settings.adzuna_app_key)
    )
    return BroadJobDiscoveryService(
        discovery_service=JobDiscoveryService(providers=providers),
        state_store=SqlAlchemyDiscoveredJobStateStore(db),
    )


@lru_cache
def get_ats_resolver_service() -> AtsResolverService:
    return AtsResolverService(
        probes=[
            GreenhouseJobSourceProbe(),
            AshbyJobSourceProbe(),
            LeverJobSourceProbe(),
            SmartRecruitersJobSourceProbe(),
            RecruiteeJobSourceProbe(),
        ]
    )


def get_company_source_discovery_service(
    db: DbSession,
    resolver: Annotated[AtsResolverService, Depends(get_ats_resolver_service)],
) -> CompanySourceDiscoveryService:
    return CompanySourceDiscoveryService(session=db, resolver=resolver)


def get_employer_universe_service(db: DbSession) -> EmployerUniverseService:
    return EmployerUniverseService(session=db)


@lru_cache
def get_job_relevance_agent() -> OpenAIJobRelevanceAgent:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job ranking is not configured. Set OPENAI_API_KEY in backend/.env.")
    return OpenAIJobRelevanceAgent(api_key=settings.openai_api_key, model=settings.openai_job_relevance_model)


@lru_cache
def get_job_archetype_agent() -> OpenAIJobArchetypeAgent:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job ranking is not configured. Set OPENAI_API_KEY in backend/.env.")
    return OpenAIJobArchetypeAgent(api_key=settings.openai_api_key, model=settings.openai_job_archetype_model)


@lru_cache
def get_career_assessment_service() -> CareerAssessmentService:
    settings = get_settings()

    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Career alignment is not configured. "
                "Set OPENAI_API_KEY in backend/.env."
            ),
        )

    agent = OpenAICareerAlignmentAgent(
        api_key=settings.openai_api_key,
        model=settings.openai_career_alignment_model,
    )
    return CareerAssessmentService(agent=agent)


def get_career_analysis_graph(
    job_analysis_service: Annotated[JobAnalysisService, Depends(get_job_analysis_service)],
    requirement_matching_service: Annotated[
        RequirementMatchingService,
        Depends(get_requirement_matching_service),
    ],
    career_assessment_service: Annotated[
        CareerAssessmentService,
        Depends(get_career_assessment_service),
    ],
) -> CareerAnalysisGraph:
    return CareerAnalysisGraph(
        job_analysis_service=job_analysis_service,
        requirement_matching_service=requirement_matching_service,
        fit_assessment_service=FitAssessmentService(),
        career_assessment_service=career_assessment_service,
        recommendation_service=RecommendationService(),
    )


def get_job_ranking_service(
    relevance_agent: Annotated[OpenAIJobRelevanceAgent, Depends(get_job_relevance_agent)],
    archetype_agent: Annotated[OpenAIJobArchetypeAgent, Depends(get_job_archetype_agent)],
    career_analysis_graph: Annotated[CareerAnalysisGraph, Depends(get_career_analysis_graph)],
) -> JobRankingService:
    return JobRankingService(relevance_agent=relevance_agent, archetype_agent=archetype_agent, career_analysis_graph=career_analysis_graph)


def get_discover_and_rank_service(
    db: DbSession,
    resolver: Annotated[AtsResolverService, Depends(get_ats_resolver_service)],
    ranking_service: Annotated[JobRankingService, Depends(get_job_ranking_service)],
) -> DiscoverAndRankService:
    return DiscoverAndRankService(
        resolver=resolver,
        ranking_service=ranking_service,
        state_store=SqlAlchemyDiscoveredJobStateStore(db),
    )


def get_demo_analysis_workflow(
    career_analysis_graph: Annotated[
        CareerAnalysisGraph,
        Depends(get_career_analysis_graph),
    ],
) -> DemoAnalysisWorkflow:
    return DemoAnalysisWorkflow(career_analysis_graph=career_analysis_graph)

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
from app.agents.agentic_discovery import OpenAIPageVacancyExtractor, OpenAISearchStrategyGenerator
from app.core.config import Settings, get_settings
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
from app.services.agentic_job_discovery_service import AgenticJobDiscoveryService
from app.services.discover_and_rank_service import DiscoverAndRankService
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.external_discovery_import_service import ExternalDiscoveryImportService
from app.services.opportunity_inbox_service import OpportunityInboxService
from app.services.cv_ingestion_service import CVIngestionService
from app.services.cv_ingestion_service import PersistedCandidateContextLoader
from app.schemas.candidate import CandidateContext
from app.services.cv_interpretation_service import SemanticCVInterpreter
from app.services.job_ranking_service import JobRankingService
from app.services.requirement_matching_service import RequirementMatchingService
from app.providers.jobs.greenhouse import GreenhouseJobSource
from app.providers.jobs.ashby import AshbyJobSource
from app.providers.jobs.lever import LeverJobSource
from app.providers.jobs.recruitee import RecruiteeJobSource
from app.providers.jobs.smartrecruiters import SmartRecruitersJobSource
from app.providers.page_fetch import PublicHttpPageFetcher
from app.providers.llm import (
    EnvironmentCredentialResolver,
    LLMProviderConfig,
    LLMProviderConfigurationError,
    LLMProviderFactory,
    SemanticResponseClient,
    SemanticProviderConfigurationError,
)
from app.providers.web_search import BraveWebSearchProvider, OpenAIWebSearchProvider, WebSearchProvider
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


def get_semantic_response_client(
    settings: Settings,
    *,
    model: str,
    operation: str,
) -> SemanticResponseClient:
    """Resolve semantic LLMs independently of the configured web-search provider."""
    provider = settings.default_llm_provider.casefold().strip()
    base_url = settings.effective_llm_base_url
    try:
        llm = LLMProviderFactory(
            EnvironmentCredentialResolver(openai_api_key=settings.openai_api_key)
        ).create(
            LLMProviderConfig(
                provider=provider,
                model=model,
                base_url=base_url,
            )
        )
    except LLMProviderConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return SemanticResponseClient(llm, operation=operation)


def validate_semantic_configuration(settings: Settings) -> None:
    """Validate credentials/provider/model without making a provider network call."""
    provider = settings.default_llm_provider.casefold().strip()
    models = {
        "CV_SEMANTIC_EXTRACTION_MODEL": settings.cv_semantic_extraction_model,
        "JOB_EXTRACTION_MODEL": settings.job_extraction_model,
        "REQUIREMENT_MATCHING_MODEL": settings.requirement_matching_model,
        "CAREER_ALIGNMENT_MODEL": settings.career_alignment_model,
        "JOB_RELEVANCE_MODEL": settings.job_relevance_model,
        "JOB_ARCHETYPE_MODEL": settings.job_archetype_model,
        "AGENTIC_DISCOVERY_MODEL": settings.agentic_discovery_model,
    }
    for name, model in models.items():
        if not model.strip():
            raise SemanticProviderConfigurationError(f"{name} must be configured.")
    try:
        LLMProviderFactory(
            EnvironmentCredentialResolver(openai_api_key=settings.openai_api_key)
        ).create(
            LLMProviderConfig(
                provider=provider,
                model=settings.cv_semantic_extraction_model,
                base_url=settings.effective_llm_base_url,
            )
        )
    except LLMProviderConfigurationError as exc:
        raise SemanticProviderConfigurationError(str(exc)) from exc


def get_cv_ingestion_service(db: DbSession) -> CVIngestionService:
    def build_interpreter() -> SemanticCVInterpreter:
        current_settings = get_settings()
        return SemanticCVInterpreter(
            get_semantic_response_client(
                current_settings,
                model=current_settings.cv_semantic_extraction_model,
                operation="cv_evidence_extraction",
            ),
            current_settings.cv_semantic_extraction_model,
        )

    return CVIngestionService(
        db,
        interpreter_factory=build_interpreter,
    )


def get_persisted_candidate_context_loader(db: DbSession) -> PersistedCandidateContextLoader:
    """Request-scoped loader; user-specific contexts must never be globally cached."""
    return PersistedCandidateContextLoader(db)


def get_confirmed_candidate_context(
    current_user: CurrentUser,
    loader: Annotated[PersistedCandidateContextLoader, Depends(get_persisted_candidate_context_loader)],
) -> CandidateContext:
    context = loader.load_confirmed(current_user.id)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate profile is not ready. Upload, review and confirm a CV first.",
        )
    return context


PersistedCandidateContext = Annotated[CandidateContext, Depends(get_confirmed_candidate_context)]


@lru_cache
def get_job_analysis_service() -> JobAnalysisService:
    settings = get_settings()
    extractor = OpenAIJobExtractor(
        api_key="",
        model=settings.job_extraction_model,
        client=get_semantic_response_client(
            settings,
            model=settings.job_extraction_model,
            operation="job_extraction",
        ),
    )

    return JobAnalysisService(extractor=extractor)


@lru_cache
def get_requirement_matching_service() -> RequirementMatchingService:
    settings = get_settings()
    matcher = OpenAIRequirementMatcher(
        api_key="",
        model=settings.requirement_matching_model,
        client=get_semantic_response_client(
            settings,
            model=settings.requirement_matching_model,
            operation="requirement_matching",
        ),
    )
    return RequirementMatchingService(matcher=matcher)


@lru_cache
def get_job_discovery_service() -> JobDiscoveryService:
    settings = get_settings()
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
    return JobDiscoveryService(providers=providers)


def get_external_discovery_import_service(db: DbSession) -> ExternalDiscoveryImportService:
    """External runtime imports use shared state but never authoritative source evidence."""
    return ExternalDiscoveryImportService(
        session=db,
        state_store=SqlAlchemyDiscoveredJobStateStore(db),
    )


def get_opportunity_inbox_service(db: DbSession) -> OpportunityInboxService:
    """Request-scoped read service for persisted external discoveries."""
    return OpportunityInboxService(db)


def get_agentic_web_search_provider(settings: Settings) -> WebSearchProvider:
    """Build only the configured search capability, independently of reasoning models."""
    provider = settings.agentic_search_provider.casefold().strip()
    if provider == "disabled":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Agentic web discovery is disabled. Use the external discovery runtime "
                "or explicitly configure a supported search provider."
            ),
        )
    if provider == "openai":
        if not settings.openai_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenAI web search is not configured. Set OPENAI_API_KEY in backend/.env.",
            )
        return OpenAIWebSearchProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_web_search_model,
        )
    if provider == "brave":
        if not settings.brave_search_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Brave web search is not configured. Set BRAVE_SEARCH_API_KEY in backend/.env.",
            )
        return BraveWebSearchProvider(api_key=settings.brave_search_api_key)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Unsupported AGENTIC_SEARCH_PROVIDER. Supported values: disabled, openai, brave.",
    )


def get_agentic_job_discovery_service(db: DbSession) -> AgenticJobDiscoveryService:
    settings = get_settings()
    # Resolve the search capability first so disabled mode fails before any in-process
    # semantic components are constructed.
    search_provider = get_agentic_web_search_provider(settings)
    return AgenticJobDiscoveryService(
        strategy_generator=OpenAISearchStrategyGenerator(
            api_key="",
            model=settings.agentic_discovery_model,
            client=get_semantic_response_client(
                settings,
                model=settings.agentic_discovery_model,
                operation="search_strategy_generation",
            ),
        ),
        search_provider=search_provider,
        page_fetcher=PublicHttpPageFetcher(),
        vacancy_extractor=OpenAIPageVacancyExtractor(
            api_key="",
            model=settings.agentic_discovery_model,
            client=get_semantic_response_client(
                settings,
                model=settings.agentic_discovery_model,
                operation="web_vacancy_extraction",
            ),
        ),
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
    return OpenAIJobRelevanceAgent(
        api_key="",
        model=settings.job_relevance_model,
        client=get_semantic_response_client(
            settings,
            model=settings.job_relevance_model,
            operation="job_relevance",
        ),
    )


@lru_cache
def get_job_archetype_agent() -> OpenAIJobArchetypeAgent:
    settings = get_settings()
    return OpenAIJobArchetypeAgent(
        api_key="",
        model=settings.job_archetype_model,
        client=get_semantic_response_client(
            settings,
            model=settings.job_archetype_model,
            operation="job_archetype",
        ),
    )


@lru_cache
def get_career_assessment_service() -> CareerAssessmentService:
    settings = get_settings()
    agent = OpenAICareerAlignmentAgent(
        api_key="",
        model=settings.career_alignment_model,
        client=get_semantic_response_client(
            settings,
            model=settings.career_alignment_model,
            operation="career_alignment",
        ),
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

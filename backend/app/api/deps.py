from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.agents.career_alignment import OpenAICareerAlignmentAgent
from app.agents.candidate_adviser import SemanticCandidateAdviser
from app.agents.application_drafting import OpenAIApplicationDraftingAgent
from app.agents.candidate_adviser_clarification import SemanticCandidateAdviserClarificationInterpreter
from app.agents.candidate_adviser_profile_proposal import SemanticCandidateAdviserProfileProposalGenerator
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
from app.services.external_job_verification_service import ExternalJobVerificationService
from app.services.opportunity_inbox_service import OpportunityInboxService
from app.services.job_detail_enrichment_service import JobDetailEnrichmentService
from app.services.structured_ats_discovery_service import StructuredAtsDiscoveryService
from app.services.cv_ingestion_service import CVIngestionReadService, CVIngestionService
from app.services.canonical_candidate_read_service import (
    CandidateEvidenceMaterializationIncomplete,
    CanonicalCandidateReadService,
)
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_adviser_profile_proposal_generation import CandidateAdviserProfileProposalGenerationService
from app.schemas.candidate import CandidateContext
from app.services.cv_interpretation_service import SemanticCVInterpreter
from app.services.job_ranking_service import JobRankingService
from app.services.requirement_matching_service import RequirementMatchingService
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.services.user_job_discovery_service import UserJobDiscoveryHistoryReadService
from app.services.discovery_schedule_service import DiscoveryScheduleService
from app.services.application_preparation_service import ApplicationPreparationReadService, ApplicationPreparationService
from app.services.application_tracking_service import ApplicationTrackingService
from app.services.scheduled_discovery_execution_service import ScheduledDiscoveryExecutionService
from app.services.ai_settings_service import AiSettingsService
from app.services.llm_runtime import (
    JOB_EVALUATION_OPERATIONS,
    PREPARATION_OPERATIONS,
    RUNTIME_OPERATION_TO_SETTING,
    ResolvedRuntimeSnapshot,
    RuntimePreferenceError,
    deployment_operation_values,
    resolve_runtime_snapshot,
    validate_combination,
)
from app.providers.jobs.greenhouse import GreenhouseJobSource
from app.providers.jobs.ashby import AshbyJobSource
from app.providers.jobs.lever import LeverJobSource
from app.providers.jobs.recruitee import RecruiteeJobSource
from app.providers.jobs.smartrecruiters import SmartRecruitersJobSource
from app.providers.jobs.workday import WorkdayJobDetailExtractor
from app.providers.page_fetch import PublicHttpPageFetcher
from app.providers.llm import (
    EnvironmentCredentialResolver,
    LLMProviderConfig,
    LLMProviderConfigurationError,
    LLMProviderFactory,
    SemanticResponseClient,
    SemanticProviderConfigurationError,
    validate_openai_structured_output_model,
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
    runtime_snapshot: ResolvedRuntimeSnapshot | None = None,
) -> SemanticResponseClient:
    """Resolve semantic LLMs independently of the configured web-search provider."""
    provider = settings.default_llm_provider.casefold().strip()
    setting_operation = RUNTIME_OPERATION_TO_SETTING[operation]
    if runtime_snapshot is not None:
        selected = runtime_snapshot.operation(setting_operation)
        model = selected.model
        reasoning_effort = selected.reasoning_effort
    else:
        reasoning_effort = deployment_operation_values(settings)[setting_operation][1]
    try:
        validate_combination(provider, model, reasoning_effort)
    except RuntimePreferenceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
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
    return SemanticResponseClient(llm, operation=operation, reasoning_effort=reasoning_effort)


def validate_semantic_configuration(settings: Settings) -> None:
    """Validate credentials/provider/model without making a provider network call."""
    provider = settings.default_llm_provider.casefold().strip()
    models = {
        "CV_SEMANTIC_EXTRACTION_MODEL": settings.cv_semantic_extraction_model,
        "CANDIDATE_ADVISER_MODEL": settings.candidate_adviser_model,
        "JOB_EXTRACTION_MODEL": settings.job_extraction_model,
        "REQUIREMENT_MATCHING_MODEL": settings.requirement_matching_model,
        "CAREER_ALIGNMENT_MODEL": settings.career_alignment_model,
        "JOB_RELEVANCE_MODEL": settings.job_relevance_model,
        "JOB_ARCHETYPE_MODEL": settings.job_archetype_model,
        "AGENTIC_DISCOVERY_MODEL": settings.agentic_discovery_model,
        "APPLICATION_DRAFTING_MODEL": settings.application_drafting_model,
    }
    for name, model in models.items():
        if not model.strip():
            raise SemanticProviderConfigurationError(f"{name} must be configured.")
    for model, effort in deployment_operation_values(settings).values():
        try:
            validate_combination(provider, model, effort)
        except RuntimePreferenceError as exc:
            raise SemanticProviderConfigurationError(str(exc)) from exc
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
        if provider == "openai":
            validate_openai_structured_output_model(settings.requirement_matching_model)
            validate_openai_structured_output_model(settings.application_drafting_model)
    except LLMProviderConfigurationError as exc:
        raise SemanticProviderConfigurationError(str(exc)) from exc


def get_user_runtime_snapshot(
    db: DbSession,
    current_user: CurrentUser,
) -> ResolvedRuntimeSnapshot:
    """Resolve once per authenticated workflow; this performs no provider work."""
    return AiSettingsService(db).snapshot_for_user(current_user.id)


def _build_cv_ingestion_service(
    db: Session,
    runtime_snapshot: ResolvedRuntimeSnapshot | None = None,
) -> CVIngestionService:
    current_settings = get_settings()
    owner_snapshot = runtime_snapshot or resolve_runtime_snapshot(current_settings)

    def build_interpreter() -> SemanticCVInterpreter:
        return SemanticCVInterpreter(
            get_semantic_response_client(
                current_settings,
                model=current_settings.cv_semantic_extraction_model,
                operation="cv_evidence_extraction",
                runtime_snapshot=owner_snapshot,
            ),
            owner_snapshot.operation(RUNTIME_OPERATION_TO_SETTING["cv_evidence_extraction"]).model,
        )

    return CVIngestionService(
        db,
        interpreter_factory=build_interpreter,
        runtime_snapshot=owner_snapshot,
    )


def get_cv_ingestion_service(db: DbSession) -> CVIngestionService:
    """Deployment-default CV service for explicit system/development use."""
    return _build_cv_ingestion_service(db)


def get_user_cv_ingestion_service(
    db: DbSession,
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> CVIngestionService:
    return _build_cv_ingestion_service(db, runtime_snapshot)


def get_user_cv_ingestion_read_service(db: DbSession) -> CVIngestionReadService:
    """Historical CV reads must not resolve AI settings or provider runtime."""
    return CVIngestionReadService(db)


def get_canonical_candidate_read_service(db: DbSession) -> CanonicalCandidateReadService:
    """Request-scoped, authenticated-domain reader; it never caches user data."""
    return CanonicalCandidateReadService(db)


def _build_candidate_adviser_service(
    db: Session,
    runtime_snapshot: ResolvedRuntimeSnapshot | None = None,
) -> CandidateAdviserService:
    def build_agent() -> SemanticCandidateAdviser:
        current_settings = get_settings()
        return SemanticCandidateAdviser(
            get_semantic_response_client(
                current_settings,
                model=current_settings.candidate_adviser_model,
                operation="candidate_adviser",
                runtime_snapshot=runtime_snapshot,
            ),
            runtime_snapshot.operation("candidate_adviser").model if runtime_snapshot else current_settings.candidate_adviser_model,
        )

    def build_interpreter() -> SemanticCandidateAdviserClarificationInterpreter:
        current_settings = get_settings()
        return SemanticCandidateAdviserClarificationInterpreter(
            get_semantic_response_client(
                current_settings,
                model=current_settings.candidate_adviser_model,
                operation="candidate_adviser_clarification",
                runtime_snapshot=runtime_snapshot,
            ),
            runtime_snapshot.operation("candidate_adviser").model if runtime_snapshot else current_settings.candidate_adviser_model,
        )

    return CandidateAdviserService(
        db,
        agent_factory=build_agent,
        clarification_interpreter_factory=build_interpreter,
    )


def get_candidate_adviser_service(db: DbSession) -> CandidateAdviserService:
    return _build_candidate_adviser_service(db)


def get_user_candidate_adviser_service(
    db: DbSession,
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> CandidateAdviserService:
    return _build_candidate_adviser_service(db, runtime_snapshot)


def get_user_candidate_adviser_profile_proposal_generation_service(
    db: DbSession,
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> CandidateAdviserProfileProposalGenerationService:
    # The semantic client is deliberately resolved only inside this closure,
    # after generation has validated the owned confirmed source and evidence.
    def build_generator() -> SemanticCandidateAdviserProfileProposalGenerator:
        current_settings = get_settings()
        return SemanticCandidateAdviserProfileProposalGenerator(
            get_semantic_response_client(
                current_settings,
                model=current_settings.candidate_adviser_model,
                operation="candidate_adviser_profile_proposal",
                runtime_snapshot=runtime_snapshot,
            ),
            runtime_snapshot.operation("candidate_adviser").model,
        )

    return CandidateAdviserProfileProposalGenerationService(db, generator_factory=build_generator)


def get_confirmed_candidate_context(
    current_user: CurrentUser,
    reader: Annotated[CanonicalCandidateReadService, Depends(get_canonical_candidate_read_service)],
) -> CandidateContext:
    snapshot = reader.read(current_user.id)
    try:
        context = reader.candidate_context(
            snapshot,
            require_structured_profile=True,
            require_complete_evidence=True,
        )
    except CandidateEvidenceMaterializationIncomplete as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Current candidate evidence is incomplete. Candidate context is unavailable.",
        ) from exc
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate profile is not ready. Upload, review and confirm a CV first.",
        )
    return context


PersistedCandidateContext = Annotated[CandidateContext, Depends(get_confirmed_candidate_context)]


def get_job_analysis_service() -> JobAnalysisService:
    settings = get_settings()
    return _build_job_analysis_service(settings)


def _build_job_analysis_service(settings: Settings, runtime_snapshot: ResolvedRuntimeSnapshot | None = None) -> JobAnalysisService:
    model = runtime_snapshot.operation("job_extraction").model if runtime_snapshot else settings.job_extraction_model
    extractor = OpenAIJobExtractor(
        api_key="",
        model=model,
        client=get_semantic_response_client(
            settings,
            model=model,
            operation="job_extraction",
            runtime_snapshot=runtime_snapshot,
        ),
    )
    return JobAnalysisService(extractor=extractor)


def get_requirement_matching_service() -> RequirementMatchingService:
    settings = get_settings()
    return _build_requirement_matching_service(settings)


def get_user_requirement_matching_service(
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> RequirementMatchingService:
    """Build matching with the authenticated user's immutable workflow snapshot."""
    return _build_requirement_matching_service(get_settings(), runtime_snapshot)


def _build_requirement_matching_service(settings: Settings, runtime_snapshot: ResolvedRuntimeSnapshot | None = None) -> RequirementMatchingService:
    model = runtime_snapshot.operation("requirement_matching").model if runtime_snapshot else settings.requirement_matching_model
    matcher = OpenAIRequirementMatcher(
        api_key="",
        model=model,
        client=get_semantic_response_client(
            settings,
            model=model,
            operation="requirement_matching",
            runtime_snapshot=runtime_snapshot,
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
    page_fetcher = PublicHttpPageFetcher()
    return ExternalDiscoveryImportService(
        session=db,
        state_store=SqlAlchemyDiscoveredJobStateStore(db),
        verifier=ExternalJobVerificationService(
            page_fetcher=page_fetcher,
            workday_detail_extractor=WorkdayJobDetailExtractor(page_fetcher),
        ),
    )


def get_structured_ats_discovery_service(db: DbSession) -> StructuredAtsDiscoveryService:
    """Known-source ATS scans are deterministic and construct no semantic clients."""
    return StructuredAtsDiscoveryService(
        session=db,
        state_store=SqlAlchemyDiscoveredJobStateStore(db),
    )


def get_opportunity_inbox_service(db: DbSession) -> OpportunityInboxService:
    """Request-scoped read service for persisted external discoveries."""
    return OpportunityInboxService(db)


def get_job_detail_enrichment_service(db: DbSession) -> JobDetailEnrichmentService:
    settings = get_settings()
    page_fetcher = PublicHttpPageFetcher()
    return JobDetailEnrichmentService(
        session=db,
        page_fetcher=page_fetcher,
        vacancy_extractor=OpenAIPageVacancyExtractor(
            api_key="",
            model=settings.agentic_discovery_model,
            client=get_semantic_response_client(
                settings,
                model=settings.agentic_discovery_model,
                operation="web_vacancy_extraction",
            ),
        ),
        job_analysis_service=get_job_analysis_service(),
        state_store=SqlAlchemyDiscoveredJobStateStore(db),
        workday_detail_extractor=WorkdayJobDetailExtractor(page_fetcher),
    )


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
    return _build_agentic_job_discovery_service(db, settings)


def _build_agentic_job_discovery_service(
    db: Session,
    settings: Settings,
    runtime_snapshot: ResolvedRuntimeSnapshot | None = None,
) -> AgenticJobDiscoveryService:
    # Resolve the search capability first so disabled mode fails before any in-process
    # semantic components are constructed.
    search_provider = get_agentic_web_search_provider(settings)
    return AgenticJobDiscoveryService(
        strategy_generator=OpenAISearchStrategyGenerator(
            api_key="",
            model=runtime_snapshot.operation("agentic_discovery").model if runtime_snapshot else settings.agentic_discovery_model,
            client=get_semantic_response_client(
                settings,
                model=settings.agentic_discovery_model,
                operation="search_strategy_generation",
                runtime_snapshot=runtime_snapshot,
            ),
        ),
        search_provider=search_provider,
        page_fetcher=PublicHttpPageFetcher(),
        vacancy_extractor=OpenAIPageVacancyExtractor(
            api_key="",
            model=runtime_snapshot.operation("agentic_discovery").model if runtime_snapshot else settings.agentic_discovery_model,
            client=get_semantic_response_client(
                settings,
                model=settings.agentic_discovery_model,
                operation="web_vacancy_extraction",
                runtime_snapshot=runtime_snapshot,
            ),
        ),
        state_store=SqlAlchemyDiscoveredJobStateStore(db),
    )


def get_user_agentic_job_discovery_service(
    db: DbSession,
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> AgenticJobDiscoveryService:
    return _build_agentic_job_discovery_service(db, get_settings(), runtime_snapshot)


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


def get_job_relevance_agent() -> OpenAIJobRelevanceAgent:
    settings = get_settings()
    return _build_job_relevance_agent(settings)


def _build_job_relevance_agent(settings: Settings, runtime_snapshot: ResolvedRuntimeSnapshot | None = None) -> OpenAIJobRelevanceAgent:
    model = runtime_snapshot.operation("job_relevance").model if runtime_snapshot else settings.job_relevance_model
    return OpenAIJobRelevanceAgent(
        api_key="",
        model=model,
        client=get_semantic_response_client(
            settings,
            model=model,
            operation="job_relevance",
            runtime_snapshot=runtime_snapshot,
        ),
    )


def get_job_archetype_agent() -> OpenAIJobArchetypeAgent:
    settings = get_settings()
    return _build_job_archetype_agent(settings)


def _build_job_archetype_agent(settings: Settings, runtime_snapshot: ResolvedRuntimeSnapshot | None = None) -> OpenAIJobArchetypeAgent:
    model = runtime_snapshot.operation("job_archetype").model if runtime_snapshot else settings.job_archetype_model
    return OpenAIJobArchetypeAgent(
        api_key="",
        model=model,
        client=get_semantic_response_client(
            settings,
            model=model,
            operation="job_archetype",
            runtime_snapshot=runtime_snapshot,
        ),
    )


def get_career_assessment_service() -> CareerAssessmentService:
    settings = get_settings()
    return _build_career_assessment_service(settings)


def _build_career_assessment_service(settings: Settings, runtime_snapshot: ResolvedRuntimeSnapshot | None = None) -> CareerAssessmentService:
    model = runtime_snapshot.operation("career_alignment").model if runtime_snapshot else settings.career_alignment_model
    agent = OpenAICareerAlignmentAgent(
        api_key="",
        model=model,
        client=get_semantic_response_client(
            settings,
            model=model,
            operation="career_alignment",
            runtime_snapshot=runtime_snapshot,
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


def _build_user_career_analysis_graph(runtime_snapshot: ResolvedRuntimeSnapshot) -> CareerAnalysisGraph:
    settings = get_settings()
    return CareerAnalysisGraph(
        job_analysis_service=_build_job_analysis_service(settings, runtime_snapshot),
        requirement_matching_service=_build_requirement_matching_service(settings, runtime_snapshot),
        fit_assessment_service=FitAssessmentService(),
        career_assessment_service=_build_career_assessment_service(settings, runtime_snapshot),
        recommendation_service=RecommendationService(),
    )


def get_user_career_analysis_graph(
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> CareerAnalysisGraph:
    return _build_user_career_analysis_graph(runtime_snapshot)


def get_job_ranking_service(
    relevance_agent: Annotated[OpenAIJobRelevanceAgent, Depends(get_job_relevance_agent)],
    archetype_agent: Annotated[OpenAIJobArchetypeAgent, Depends(get_job_archetype_agent)],
    career_analysis_graph: Annotated[CareerAnalysisGraph, Depends(get_career_analysis_graph)],
) -> JobRankingService:
    return JobRankingService(relevance_agent=relevance_agent, archetype_agent=archetype_agent, career_analysis_graph=career_analysis_graph)


def get_user_job_ranking_service(
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> JobRankingService:
    settings = get_settings()
    return _build_user_job_ranking_service(settings, runtime_snapshot)


def _build_user_job_ranking_service(
    settings: Settings,
    runtime_snapshot: ResolvedRuntimeSnapshot,
) -> JobRankingService:
    return JobRankingService(
        relevance_agent=_build_job_relevance_agent(settings, runtime_snapshot),
        archetype_agent=_build_job_archetype_agent(settings, runtime_snapshot),
        career_analysis_graph=_build_user_career_analysis_graph(runtime_snapshot),
    )


def get_user_job_discovery_service(
    db: DbSession,
    ranking_service: Annotated[JobRankingService, Depends(get_user_job_ranking_service)],
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> UserJobDiscoveryService:
    """Server-owned personal run/reuse policy over canonical shared job IDs."""
    return UserJobDiscoveryService(db, ranking_service=ranking_service, runtime_snapshot=runtime_snapshot)


def get_user_job_discovery_read_service(
    db: DbSession,
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> UserJobDiscoveryService:
    """Provider-free dependency for persisted Jobs GET projections."""
    return UserJobDiscoveryService(db, runtime_snapshot=runtime_snapshot)


def get_user_job_discovery_history_read_service(db: DbSession) -> UserJobDiscoveryHistoryReadService:
    """Historical run reads are independent of current runtime configuration."""
    return UserJobDiscoveryHistoryReadService(db)


def get_application_preparation_service(
    db: DbSession,
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> ApplicationPreparationService:
    settings = get_settings()

    def build_drafting_agent() -> OpenAIApplicationDraftingAgent:
        return OpenAIApplicationDraftingAgent(
            cv_client=get_semantic_response_client(settings, model=settings.application_drafting_model, operation="application_cv_drafting", runtime_snapshot=runtime_snapshot),
            cover_letter_client=get_semantic_response_client(settings, model=settings.application_drafting_model, operation="application_cover_letter", runtime_snapshot=runtime_snapshot),
            answer_client=get_semantic_response_client(settings, model=settings.application_drafting_model, operation="application_answer_drafting", runtime_snapshot=runtime_snapshot),
            model=runtime_snapshot.operation("application_drafting").model,
        )

    return ApplicationPreparationService(
        db,
        user_discovery=UserJobDiscoveryService(db, runtime_snapshot=runtime_snapshot),
        graph_factory=lambda: _build_user_career_analysis_graph(runtime_snapshot),
        drafting_agent_factory=build_drafting_agent,
        page_fetcher=PublicHttpPageFetcher(), settings=settings, runtime_snapshot=runtime_snapshot,
    )


def get_application_preparation_read_service(db: DbSession) -> ApplicationPreparationReadService:
    """Provider-free dependency for immutable application-preparation reads."""
    return ApplicationPreparationReadService(db)


def get_application_tracking_service(db: DbSession) -> ApplicationTrackingService:
    """Provider-free service for persisted user-recorded application tracking."""
    return ApplicationTrackingService(db)


def get_discovery_schedule_service(db: DbSession) -> DiscoveryScheduleService:
    return DiscoveryScheduleService(db)


def get_scheduled_discovery_execution_service(
    db: DbSession,
    structured_ats: Annotated[StructuredAtsDiscoveryService, Depends(get_structured_ats_discovery_service)],
) -> ScheduledDiscoveryExecutionService:
    """The agentic service is deliberately built only if a schedule enables it."""
    return ScheduledDiscoveryExecutionService(
        db,
        structured_ats=structured_ats,
        agentic_web_factory=lambda snapshot: _build_agentic_job_discovery_service(db, get_settings(), snapshot),
        user_runs_factory=lambda snapshot: UserJobDiscoveryService(
            db,
            ranking_service=_build_user_job_ranking_service(get_settings(), snapshot),
            runtime_snapshot=snapshot,
        ),
        runtime_snapshot_resolver=lambda user_id: AiSettingsService(db).snapshot_for_user(user_id),
    )


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

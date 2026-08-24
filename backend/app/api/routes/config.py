from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, validate_semantic_configuration
from app.core.config import Settings, get_settings
from app.providers.llm import SemanticProviderConfigurationError
from app.schemas.llm_config import LLMConfigurationCheck, LLMConfigurationRead

router = APIRouter(prefix="/config", tags=["configuration"])


def _safe_configuration(settings: Settings) -> dict[str, object]:
    return {
        "default_llm_provider": settings.default_llm_provider,
        "effective_llm_base_url": settings.effective_llm_base_url,
        "ollama_base_url": settings.ollama_base_url,
        "cv_semantic_extraction_model": settings.cv_semantic_extraction_model,
        "job_extraction_model": settings.job_extraction_model,
        "requirement_matching_model": settings.requirement_matching_model,
        "career_alignment_model": settings.career_alignment_model,
        "job_relevance_model": settings.job_relevance_model,
        "job_archetype_model": settings.job_archetype_model,
        "agentic_discovery_model": settings.agentic_discovery_model,
        "openai_web_search_model": settings.openai_web_search_model,
        "openai_api_key_configured": bool(settings.openai_api_key),
    }


@router.get("/llm", response_model=LLMConfigurationRead)
def read_llm_configuration(_: CurrentUser) -> LLMConfigurationRead:
    return LLMConfigurationRead.model_validate(_safe_configuration(get_settings()))


@router.get("/llm/check", response_model=LLMConfigurationCheck)
def check_llm_configuration(_: CurrentUser) -> LLMConfigurationCheck:
    settings = get_settings()
    try:
        validate_semantic_configuration(settings)
    except SemanticProviderConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return LLMConfigurationCheck.model_validate(_safe_configuration(settings) | {"ready": True})

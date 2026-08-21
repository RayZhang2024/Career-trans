# Merge these imports and functions into backend/app/api/deps.py

from functools import lru_cache

from fastapi import HTTPException, status

from app.agents.job_extraction import OpenAIJobExtractor
from app.core.config import get_settings
from app.services.job_analysis_service import JobAnalysisService


@lru_cache
def get_job_analysis_service() -> JobAnalysisService:
    settings = get_settings()

    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Job analysis is not configured. Set OPENAI_API_KEY "
                "in backend/.env."
            ),
        )

    extractor = OpenAIJobExtractor(
        api_key=settings.openai_api_key,
        model=settings.openai_job_extraction_model,
    )
    return JobAnalysisService(extractor=extractor)

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.agents.job_extraction import OpenAIJobExtractor
from app.agents.requirement_matching import OpenAIRequirementMatcher
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User
from app.services.auth_service import get_user_by_id
from app.services.job_analysis_service import JobAnalysisService
from app.services.requirement_matching_service import RequirementMatchingService
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


def get_demo_analysis_workflow(
    job_analysis_service: Annotated[JobAnalysisService, Depends(get_job_analysis_service)],
    requirement_matching_service: Annotated[
        RequirementMatchingService,
        Depends(get_requirement_matching_service),
    ],
) -> DemoAnalysisWorkflow:
    return DemoAnalysisWorkflow(
        job_analysis_service=job_analysis_service,
        requirement_matching_service=requirement_matching_service,
    )

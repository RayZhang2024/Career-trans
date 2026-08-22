from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.job_extraction import JobExtractionError
from app.agents.requirement_matching import RequirementMatchingError
from app.api.deps import (
    get_job_analysis_service,
    get_job_discovery_service,
    get_requirement_matching_service,
)
from app.schemas.discovery import JobDiscoveryResponse, JobSearchQuery
from app.schemas.job import JobAnalysisRequest, JobAnalysisResponse
from app.schemas.matching import JobMatchRequest, JobMatchResponse
from app.services.job_analysis_service import JobAnalysisService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.requirement_matching_service import RequirementMatchingService

router = APIRouter(prefix="/jobs", tags=["jobs"])


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

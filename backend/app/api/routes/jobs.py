from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.job_extraction import JobExtractionError
from app.api.deps import get_job_analysis_service
from app.schemas.job import JobAnalysisRequest, JobAnalysisResponse
from app.services.job_analysis_service import JobAnalysisService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "/analyse",
    response_model=JobAnalysisResponse,
    status_code=status.HTTP_200_OK,
)
def analyse_job(
    payload: JobAnalysisRequest,
    service: JobAnalysisService = Depends(get_job_analysis_service),
) -> JobAnalysisResponse:
    """Convert raw job-description text into a structured JobProfile.

    V1 note:
    This endpoint is deliberately usable without candidate-profile data. It performs
    job extraction only. Candidate matching and persistence are separate later stages.
    """
    try:
        profile = service.analyse_text(payload.job_text)
    except JobExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return JobAnalysisResponse(job_profile=profile)

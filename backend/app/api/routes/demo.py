from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.career_alignment import CareerAlignmentError
from app.agents.job_extraction import JobExtractionError
from app.agents.requirement_matching import RequirementMatchingError
from app.api.deps import get_demo_analysis_workflow
from app.schemas.demo import DemoAnalyseAndMatchRequest, DemoAnalyseAndMatchResponse
from app.services.candidate_context_loader import CandidateContextLoadError
from app.workflows.demo_analysis import DemoAnalysisWorkflow

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post(
    "/analyse-and-match",
    response_model=DemoAnalyseAndMatchResponse,
    status_code=status.HTTP_200_OK,
)
def analyse_and_match_demo(
    payload: DemoAnalyseAndMatchRequest,
    workflow: DemoAnalysisWorkflow = Depends(get_demo_analysis_workflow),
) -> DemoAnalyseAndMatchResponse:
    """Run job, fit, and career analysis against the repository demo profile.

    This endpoint exists for development/evaluation only. Production candidate data
    will later be loaded from the authenticated user's private data store.
    """
    try:
        return workflow.run(payload.job_text)
    except CandidateContextLoadError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except (
        JobExtractionError,
        RequirementMatchingError,
        CareerAlignmentError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, get_candidate_adviser_service
from app.providers.llm import (
    SemanticOutputError,
    SemanticProviderConfigurationError,
    SemanticProviderRequestError,
    SemanticProviderUnavailableError,
)
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessment,
    CandidateAdviserAssessmentRead,
    CandidateIntakeProfileData,
    CandidateIntakeRead,
)
from app.services.candidate_adviser_service import CandidateAdviserService


router = APIRouter(prefix="/career-adviser", tags=["career-adviser"])


@router.get("/intake", response_model=CandidateIntakeRead)
def read_intake(
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_candidate_adviser_service),
) -> CandidateIntakeRead:
    return service.read_intake(current_user.id)


@router.put("/intake", response_model=CandidateIntakeRead)
def save_intake(
    payload: CandidateIntakeProfileData,
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_candidate_adviser_service),
) -> CandidateIntakeRead:
    return service.save_intake(current_user.id, payload)


@router.post("/intake/confirm", response_model=CandidateIntakeRead)
def confirm_intake(
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_candidate_adviser_service),
) -> CandidateIntakeRead:
    try:
        return service.confirm_intake(current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/assess", response_model=CandidateAdviserAssessmentRead)
def assess_candidate(
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_candidate_adviser_service),
) -> CandidateAdviserAssessmentRead:
    try:
        return service.assess(current_user.id)
    except SemanticProviderConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except SemanticProviderUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (SemanticProviderRequestError, SemanticOutputError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/assessment", response_model=CandidateAdviserAssessmentRead)
def read_assessment(
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_candidate_adviser_service),
) -> CandidateAdviserAssessmentRead:
    try:
        return service.read_assessment(current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/assessment", response_model=CandidateAdviserAssessmentRead)
def edit_assessment(
    payload: CandidateAdviserAssessment,
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_candidate_adviser_service),
) -> CandidateAdviserAssessmentRead:
    try:
        return service.edit_assessment(current_user.id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/assessment/confirm", response_model=CandidateAdviserAssessmentRead)
def confirm_assessment(
    current_user: CurrentUser,
    service: CandidateAdviserService = Depends(get_candidate_adviser_service),
) -> CandidateAdviserAssessmentRead:
    try:
        return service.confirm_assessment(current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

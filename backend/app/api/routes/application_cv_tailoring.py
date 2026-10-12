"""Authenticated endpoints for the profile-first CV tailoring lifecycle."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, get_application_cv_tailoring_service
from app.schemas.application_cv_tailoring import (
    TailoringApproveWrite, TailoringDraftGenerateRequest, TailoringDraftReviewWrite,
    TailoringPreflightRead, TailoringSessionRead, TailoringStartRequest,
    TailoringStrategyReviewWrite,
)
from app.services.application_cv_tailoring_service import (
    ApplicationCVTailoringError, ApplicationCVTailoringService,
)


router = APIRouter(prefix="/applications/tailoring", tags=["application CV tailoring"])


@router.get("/preflight", response_model=TailoringPreflightRead)
def preflight(
    discovered_job_id: str,
    current_user: CurrentUser,
    service: ApplicationCVTailoringService = Depends(get_application_cv_tailoring_service),
) -> TailoringPreflightRead:
    return service.preflight(current_user.id, discovered_job_id)


@router.post("", response_model=TailoringSessionRead, status_code=status.HTTP_201_CREATED)
def start(
    payload: TailoringStartRequest,
    current_user: CurrentUser,
    service: ApplicationCVTailoringService = Depends(get_application_cv_tailoring_service),
) -> TailoringSessionRead:
    try:
        return service.start(current_user.id, payload)
    except ApplicationCVTailoringError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="The current job is not available for CV tailoring.") from exc


@router.get("/by-request/{client_request_id}", response_model=TailoringSessionRead)
def by_request(
    client_request_id: str,
    current_user: CurrentUser,
    service: ApplicationCVTailoringService = Depends(get_application_cv_tailoring_service),
) -> TailoringSessionRead:
    try:
        return service.get_by_request(current_user.id, client_request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Tailored CV request not found.") from exc


@router.get("/{tailoring_id}", response_model=TailoringSessionRead)
def get_session(
    tailoring_id: str,
    current_user: CurrentUser,
    service: ApplicationCVTailoringService = Depends(get_application_cv_tailoring_service),
) -> TailoringSessionRead:
    try:
        return service.get(current_user.id, tailoring_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Tailored CV request not found.") from exc


@router.post("/{tailoring_id}/strategy", response_model=TailoringSessionRead)
def review_strategy(
    tailoring_id: str,
    payload: TailoringStrategyReviewWrite,
    current_user: CurrentUser,
    service: ApplicationCVTailoringService = Depends(get_application_cv_tailoring_service),
) -> TailoringSessionRead:
    try:
        return service.review_strategy(current_user.id, tailoring_id, payload)
    except ApplicationCVTailoringError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Tailored CV request not found.") from exc


@router.post("/{tailoring_id}/draft", response_model=TailoringSessionRead)
def generate_draft(
    tailoring_id: str,
    payload: TailoringDraftGenerateRequest,
    current_user: CurrentUser,
    service: ApplicationCVTailoringService = Depends(get_application_cv_tailoring_service),
) -> TailoringSessionRead:
    try:
        return service.generate_draft(current_user.id, tailoring_id, payload)
    except ApplicationCVTailoringError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Tailored CV request not found.") from exc


@router.patch("/{tailoring_id}/draft", response_model=TailoringSessionRead)
def save_draft(
    tailoring_id: str,
    payload: TailoringDraftReviewWrite,
    current_user: CurrentUser,
    service: ApplicationCVTailoringService = Depends(get_application_cv_tailoring_service),
) -> TailoringSessionRead:
    try:
        return service.review_draft(current_user.id, tailoring_id, payload)
    except ApplicationCVTailoringError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Tailored CV request not found.") from exc


@router.post("/{tailoring_id}/approve", response_model=TailoringSessionRead)
def approve(
    tailoring_id: str,
    payload: TailoringApproveWrite,
    current_user: CurrentUser,
    service: ApplicationCVTailoringService = Depends(get_application_cv_tailoring_service),
) -> TailoringSessionRead:
    try:
        return service.approve(current_user.id, tailoring_id, payload)
    except ApplicationCVTailoringError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Tailored CV request not found.") from exc

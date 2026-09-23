from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, get_application_tracking_service
from app.schemas.application_tracking import (
    ApplicationTrackingCreate,
    ApplicationTrackingListItem,
    ApplicationTrackingRead,
    ApplicationTrackingStatusEventCreate,
)
from app.services.application_tracking_service import ApplicationTrackingConflictError, ApplicationTrackingService


router = APIRouter(prefix="/application-tracking", tags=["application-tracking"])


@router.post("", response_model=ApplicationTrackingRead, status_code=status.HTTP_201_CREATED)
def create_tracking(
    payload: ApplicationTrackingCreate,
    current_user: CurrentUser,
    service: ApplicationTrackingService = Depends(get_application_tracking_service),
) -> ApplicationTrackingRead:
    try:
        return service.create(current_user.id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application preparation not found.") from exc
    except ApplicationTrackingConflictError as exc:
        raise HTTPException(status_code=409, detail="This preparation is already being tracked.") from exc


@router.get("", response_model=list[ApplicationTrackingListItem])
def list_tracking(
    current_user: CurrentUser,
    service: ApplicationTrackingService = Depends(get_application_tracking_service),
) -> list[ApplicationTrackingListItem]:
    return service.list_for_user(current_user.id)


@router.get("/by-preparation/{preparation_id}", response_model=ApplicationTrackingRead)
def tracking_by_preparation(
    preparation_id: str,
    current_user: CurrentUser,
    service: ApplicationTrackingService = Depends(get_application_tracking_service),
) -> ApplicationTrackingRead:
    try:
        return service.get_by_preparation(current_user.id, preparation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application tracking not found.") from exc


@router.get("/{tracking_id}", response_model=ApplicationTrackingRead)
def tracking_detail(
    tracking_id: str,
    current_user: CurrentUser,
    service: ApplicationTrackingService = Depends(get_application_tracking_service),
) -> ApplicationTrackingRead:
    try:
        return service.get_detail(current_user.id, tracking_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application tracking not found.") from exc


@router.post("/{tracking_id}/status-events", response_model=ApplicationTrackingRead)
def create_status_event(
    tracking_id: str,
    payload: ApplicationTrackingStatusEventCreate,
    current_user: CurrentUser,
    service: ApplicationTrackingService = Depends(get_application_tracking_service),
) -> ApplicationTrackingRead:
    try:
        return service.append_status_event(current_user.id, tracking_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application tracking not found.") from exc
    except ApplicationTrackingConflictError as exc:
        raise HTTPException(status_code=409, detail="The tracking record changed before this update.") from exc

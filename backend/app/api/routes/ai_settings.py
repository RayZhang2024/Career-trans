"""Authenticated, credential-free user AI settings and catalog endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.ai_settings import AiModelCatalogRead, AiSettingsRead, UserAiSettingsReplace
from app.services.ai_settings_service import (
    AiSettingsConflictError,
    AiSettingsService,
    AiSettingsValidationError,
)


router = APIRouter(prefix="/ai", tags=["ai-settings"])


def _service(db: DbSession) -> AiSettingsService:
    return AiSettingsService(db)


@router.get("/models", response_model=AiModelCatalogRead)
def list_models(_: CurrentUser, service: AiSettingsService = Depends(_service)) -> AiModelCatalogRead:
    return service.catalog()


@router.get("/settings", response_model=AiSettingsRead)
def read_settings(current_user: CurrentUser, service: AiSettingsService = Depends(_service)) -> AiSettingsRead:
    try:
        return service.read(current_user.id)
    except AiSettingsValidationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/settings", response_model=AiSettingsRead)
def replace_settings(
    payload: UserAiSettingsReplace,
    current_user: CurrentUser,
    service: AiSettingsService = Depends(_service),
) -> AiSettingsRead:
    try:
        return service.replace(current_user.id, payload)
    except AiSettingsConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AiSettingsValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

"""Authenticated AI preference and write-only semantic credential endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.api.deps import CurrentUser, DbSession
from app.schemas.ai_settings import AiModelCatalogRead, AiSettingsRead, SemanticCredentialRead, SemanticCredentialWrite, UserAiSettingsReplace
from app.services.ai_settings_service import (
    AiSettingsConflictError,
    AiSettingsService,
    AiSettingsValidationError,
)
from app.services.semantic_credential_service import (
    SemanticCredentialConfigurationError,
    SemanticCredentialConflictError,
    SemanticCredentialService,
)


router = APIRouter(prefix="/ai", tags=["ai-settings"])


def _service(db: DbSession) -> AiSettingsService:
    return AiSettingsService(db)


def _credential_service(db: DbSession) -> SemanticCredentialService:
    return SemanticCredentialService(db)


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


@router.get("/credentials", response_model=SemanticCredentialRead)
def read_semantic_credential(current_user: CurrentUser, service: SemanticCredentialService = Depends(_credential_service)) -> SemanticCredentialRead:
    return SemanticCredentialRead.model_validate(service.status(current_user.id).__dict__)


@router.put("/credentials/openai", response_model=SemanticCredentialRead)
async def save_openai_credential(request: Request, current_user: CurrentUser, service: SemanticCredentialService = Depends(_credential_service)) -> SemanticCredentialRead:
    # Parse manually so validation errors can never reflect a submitted key.
    try:
        payload = SemanticCredentialWrite.model_validate(await request.json())
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Enter a valid OpenAI credential request.") from exc
    try:
        result = service.save(current_user.id, payload.expected_revision, payload.api_key)
    except SemanticCredentialConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SemanticCredentialConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SemanticCredentialRead.model_validate(result.__dict__)


@router.delete("/credentials/openai", response_model=SemanticCredentialRead)
def remove_openai_credential(expected_revision: int, current_user: CurrentUser, service: SemanticCredentialService = Depends(_credential_service)) -> SemanticCredentialRead:
    try:
        result = service.remove(current_user.id, expected_revision)
    except SemanticCredentialConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SemanticCredentialRead.model_validate(result.__dict__)

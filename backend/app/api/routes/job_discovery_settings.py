"""Authenticated settings for browser-based Job Discovery."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.api.deps import CurrentUser, DbSession
from app.schemas.job_discovery_settings import (
    LocalCodexStatusRead,
    LocalCodexTestRead,
    JobDiscoverySettingsRead,
    JobDiscoverySettingsReplace,
    TavilyConnectionTestRead,
    TavilyCredentialWrite,
)
from app.services.job_discovery_settings_service import (
    JobDiscoverySettingsConflictError,
    JobDiscoverySettingsError,
    JobDiscoverySettingsService,
)

router = APIRouter(prefix="/job-discovery", tags=["job-discovery-settings"])


def _service(db: DbSession) -> JobDiscoverySettingsService:
    return JobDiscoverySettingsService(db)


@router.get("/settings", response_model=JobDiscoverySettingsRead)
def read_settings(
    current_user: CurrentUser,
    service: JobDiscoverySettingsService = Depends(_service),
) -> JobDiscoverySettingsRead:
    return service.read(current_user.id)


@router.get("/local-codex/status", response_model=LocalCodexStatusRead)
def read_local_codex_status(
    current_user: CurrentUser,
    service: JobDiscoverySettingsService = Depends(_service),
) -> LocalCodexStatusRead:
    """Return safe backend-host readiness without creating discovery state."""
    return service.local_codex_status()


@router.post("/local-codex/test", response_model=LocalCodexTestRead)
def test_local_codex(
    current_user: CurrentUser,
    service: JobDiscoverySettingsService = Depends(_service),
) -> LocalCodexTestRead:
    try:
        return service.test_local_codex()
    except JobDiscoverySettingsError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.put("/settings", response_model=JobDiscoverySettingsRead)
def replace_settings(
    payload: JobDiscoverySettingsReplace,
    current_user: CurrentUser,
    service: JobDiscoverySettingsService = Depends(_service),
) -> JobDiscoverySettingsRead:
    try:
        return service.replace(current_user.id, payload)
    except JobDiscoverySettingsConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.put("/tavily-credential", response_model=JobDiscoverySettingsRead)
async def save_tavily_credential(
    request: Request,
    current_user: CurrentUser,
    service: JobDiscoverySettingsService = Depends(_service),
) -> JobDiscoverySettingsRead:
    # Validate manually so framework validation errors cannot echo the submitted
    # credential back in their `input` field.
    try:
        payload = TavilyCredentialWrite.model_validate(await request.json())
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a valid Tavily credential request.",
        ) from exc
    try:
        return service.save_tavily_credential(
            current_user.id, payload.expected_revision, payload.api_key
        )
    except JobDiscoverySettingsConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except JobDiscoverySettingsError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.delete("/tavily-credential", response_model=JobDiscoverySettingsRead)
def remove_tavily_credential(
    current_user: CurrentUser,
    expected_revision: int,
    service: JobDiscoverySettingsService = Depends(_service),
) -> JobDiscoverySettingsRead:
    try:
        return service.remove_tavily_credential(current_user.id, expected_revision)
    except JobDiscoverySettingsConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/tavily-connection-test", response_model=TavilyConnectionTestRead)
def test_tavily_connection(
    current_user: CurrentUser,
    service: JobDiscoverySettingsService = Depends(_service),
) -> TavilyConnectionTestRead:
    try:
        return service.test_tavily_connection(current_user.id)
    except JobDiscoverySettingsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

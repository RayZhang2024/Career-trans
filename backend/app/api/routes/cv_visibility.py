from fastapi import APIRouter, Depends, HTTPException, status
from typing import Annotated

from app.api.deps import (
    CurrentUser,
    DbSession,
    get_semantic_response_client,
    get_user_runtime_snapshot,
    get_user_semantic_credential_resolver,
)
from app.core.config import Settings, get_settings
from app.schemas.ai_settings import SemanticOperation
from app.schemas.cv_visibility import CVVisibilityAssessmentCreate, CVVisibilityAssessmentRead, CVVisibilityPreflightRead
from app.services.ai_settings_service import AiSettingsService
from app.services.cv_visibility_assessment_service import CVVisibilityAssessmentError, CVVisibilityAssessmentService, OPERATION
from app.services.llm_runtime import ResolvedRuntimeSnapshot

router = APIRouter(prefix="/jobs/workspaces/{discovered_job_id}/cv-visibility", tags=["cv-visibility"])


def _service(db, runtime_snapshot: ResolvedRuntimeSnapshot, user_id: str) -> CVVisibilityAssessmentService:
    return CVVisibilityAssessmentService(
        db,
        runtime_snapshot,
        current_runtime_snapshot=lambda: _fresh_runtime(db, runtime_snapshot, user_id),
    )


def _fresh_runtime(db, fallback: ResolvedRuntimeSnapshot, user_id: str) -> ResolvedRuntimeSnapshot:
    # Clear the request identity map so a setting changed while the provider call
    # was in flight is observed before a result is marked current.
    db.expire_all()
    return AiSettingsService(db).snapshot_for_user(user_id) if user_id else fallback


@router.get("", response_model=CVVisibilityPreflightRead)
def read_cv_visibility(
    discovered_job_id: str,
    current_user: CurrentUser,
    db: DbSession,
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
) -> CVVisibilityPreflightRead:
    try:
        return _service(db, runtime_snapshot, current_user.id).read(current_user.id, discovered_job_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc


@router.post("/assessments", response_model=CVVisibilityAssessmentRead, status_code=status.HTTP_200_OK)
def create_cv_visibility_assessment(
    discovered_job_id: str,
    request: CVVisibilityAssessmentCreate,
    current_user: CurrentUser,
    db: DbSession,
    runtime_snapshot: Annotated[ResolvedRuntimeSnapshot, Depends(get_user_runtime_snapshot)],
    credential_resolver=Depends(get_user_semantic_credential_resolver),
    settings: Settings = Depends(get_settings),
) -> CVVisibilityAssessmentRead:
    selected = runtime_snapshot.operation(SemanticOperation.CV_VISIBILITY_ASSESSMENT)
    factory = lambda: get_semantic_response_client(
        settings,
        model=selected.model,
        operation=OPERATION,
        runtime_snapshot=runtime_snapshot,
        credential_resolver=credential_resolver,
    )
    try:
        return _service(db, runtime_snapshot, current_user.id).assess(current_user.id, discovered_job_id, request, factory)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
    except CVVisibilityAssessmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

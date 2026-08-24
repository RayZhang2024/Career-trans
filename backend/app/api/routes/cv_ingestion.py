from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.deps import CurrentUser, DbSession, get_cv_ingestion_service
from app.schemas.cv_ingestion import CandidateCVData, CVIngestionConfirmResponse, CVIngestionDraftRead
from app.services.cv_ingestion_service import CVIngestionService

router = APIRouter(prefix="/cv-ingestion", tags=["cv-ingestion"])


@router.post("/upload", response_model=CVIngestionDraftRead, status_code=status.HTTP_201_CREATED)
async def upload_cv_documents(
    current_user: CurrentUser,
    files: list[UploadFile] = File(...),
    service: CVIngestionService = Depends(get_cv_ingestion_service),
) -> CVIngestionDraftRead:
    if not files:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one CV file is required.")
    try:
        payload = [(file.filename or "upload", file.content_type, await file.read()) for file in files]
        return service.upload(current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.patch("/{draft_id}", response_model=CVIngestionDraftRead)
def edit_cv_draft(draft_id: str, corrected: CandidateCVData, current_user: CurrentUser, service: CVIngestionService = Depends(get_cv_ingestion_service)) -> CVIngestionDraftRead:
    try:
        return service.edit_review(current_user.id, draft_id, corrected)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV ingestion draft not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{draft_id}/interpret", response_model=CVIngestionDraftRead)
def interpret_cv_draft(draft_id: str, current_user: CurrentUser, service: CVIngestionService = Depends(get_cv_ingestion_service)) -> CVIngestionDraftRead:
    try:
        return service.interpret(current_user.id, draft_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV ingestion draft not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="CV semantic extraction failed.") from exc


@router.get("/{draft_id}", response_model=CVIngestionDraftRead)
def read_cv_draft(draft_id: str, current_user: CurrentUser, service: CVIngestionService = Depends(get_cv_ingestion_service)) -> CVIngestionDraftRead:
    try:
        return service.read(current_user.id, draft_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV ingestion draft not found.") from exc


@router.post("/{draft_id}/confirm", response_model=CVIngestionConfirmResponse)
def confirm_cv_draft(draft_id: str, current_user: CurrentUser, service: CVIngestionService = Depends(get_cv_ingestion_service)) -> CVIngestionConfirmResponse:
    try:
        return CVIngestionConfirmResponse(draft_id=draft_id, confirmed_evidence_count=service.confirm(current_user.id, draft_id))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV ingestion draft not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

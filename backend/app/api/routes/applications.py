from io import BytesIO
import re

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import CurrentUser, get_application_preparation_read_service, get_application_preparation_service
from app.schemas.application_preparation import (
    ApplicationInsufficientDetailError, ApplicationPreparationRead, ApplicationPreparationReviewRead,
    ApplicationPrepareRequest,
)
from app.services.application_document_renderer import ApplicationDocumentRenderer
from app.services.application_preparation_service import ApplicationPreparationReadService, ApplicationPreparationService


router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("/prepare", response_model=ApplicationPreparationRead, status_code=status.HTTP_201_CREATED)
def prepare(payload: ApplicationPrepareRequest, current_user: CurrentUser, service: ApplicationPreparationService = Depends(get_application_preparation_service)) -> ApplicationPreparationRead:
    try:
        return service.prepare(current_user.id, payload)
    except ApplicationInsufficientDetailError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Job detail is insufficient; provide job text.") from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application target not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("", response_model=list[ApplicationPreparationRead])
def list_preparations(current_user: CurrentUser, service: ApplicationPreparationReadService = Depends(get_application_preparation_read_service)) -> list[ApplicationPreparationRead]:
    return service.list_preparations(current_user.id)


@router.get("/{preparation_id}", response_model=ApplicationPreparationRead)
def get_preparation(preparation_id: str, current_user: CurrentUser, service: ApplicationPreparationReadService = Depends(get_application_preparation_read_service)) -> ApplicationPreparationRead:
    return _get(service, current_user.id, preparation_id)


@router.get("/{preparation_id}/review", response_model=ApplicationPreparationReviewRead)
def review_preparation(preparation_id: str, current_user: CurrentUser, service: ApplicationPreparationReadService = Depends(get_application_preparation_read_service)) -> ApplicationPreparationReviewRead:
    try:
        return service.get_review(current_user.id, preparation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application preparation not found.") from exc


@router.get("/{preparation_id}/cv.docx")
def cv_docx(preparation_id: str, current_user: CurrentUser, service: ApplicationPreparationReadService = Depends(get_application_preparation_read_service)) -> Response:
    value = _get(service, current_user.id, preparation_id); payload = ApplicationDocumentRenderer().render_cv_docx(value.identity, value.target, value.result)
    return _download(payload, _filename(value, "CV.docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.get("/{preparation_id}/cv.pdf")
def cv_pdf(preparation_id: str, current_user: CurrentUser, service: ApplicationPreparationReadService = Depends(get_application_preparation_read_service)) -> Response:
    value = _get(service, current_user.id, preparation_id); payload, _, _ = ApplicationDocumentRenderer().render_cv_pdf(value.identity, value.target, value.result)
    return _download(payload, _filename(value, "CV.pdf"), "application/pdf")


@router.get("/{preparation_id}/cover-letter.docx")
def cover_docx(preparation_id: str, current_user: CurrentUser, service: ApplicationPreparationReadService = Depends(get_application_preparation_read_service)) -> Response:
    value = _get(service, current_user.id, preparation_id)
    if value.result.cover_letter is None: raise HTTPException(status_code=404, detail="Application preparation not found.")
    return _download(ApplicationDocumentRenderer().render_cover_letter_docx(value.identity, value.target, value.result), _filename(value, "Cover_Letter.docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.get("/{preparation_id}/cover-letter.pdf")
def cover_pdf(preparation_id: str, current_user: CurrentUser, service: ApplicationPreparationReadService = Depends(get_application_preparation_read_service)) -> Response:
    value = _get(service, current_user.id, preparation_id)
    if value.result.cover_letter is None: raise HTTPException(status_code=404, detail="Application preparation not found.")
    payload, _, _ = ApplicationDocumentRenderer().render_cover_letter_pdf(value.identity, value.target, value.result)
    return _download(payload, _filename(value, "Cover_Letter.pdf"), "application/pdf")


def _get(service: ApplicationPreparationReadService, user_id: str, preparation_id: str) -> ApplicationPreparationRead:
    try: return service.get(user_id, preparation_id)
    except LookupError as exc: raise HTTPException(status_code=404, detail="Application preparation not found.") from exc


def _filename(value: ApplicationPreparationRead, suffix: str) -> str:
    parts = [value.identity.display_name, value.target.company or "Company", value.target.title or "Role", suffix]
    return "_".join(re.sub(r"[^A-Za-z0-9._-]+", "_", item).strip("._") or "application" for item in parts)


def _download(payload: bytes, filename: str, media_type: str) -> Response:
    return Response(content=payload, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

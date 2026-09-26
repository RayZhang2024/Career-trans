from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    CurrentUser,
    DbSession,
    get_canonical_candidate_read_service,
)
from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.schemas.candidate import CandidateContextSummary
from app.schemas.candidate_read_snapshot import CanonicalCandidateReadSnapshot
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.structured_profile_provenance import StructuredProfileProvenanceRead
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.schemas.candidate_profile import (
    CandidateProfileRead,
)
from app.schemas.profile_revision import (
    CandidateProfileRevisionAction,
    CandidateProfileRevisionPatch,
    CandidateProfileRevisionRead,
)
from app.services.profile_service import (
    get_profile_for_user,
)
from app.services.structured_profile_provenance_service import StructuredProfileProvenanceService
from sqlalchemy import select
from app.services.profile_revision_service import (
    CandidateProfileRevisionService,
    ProfileRevisionConflict,
    ProfileRevisionNotFound,
)

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/structured-provenance", response_model=StructuredProfileProvenanceRead)
def read_structured_profile_provenance(
    db: DbSession, current_user: CurrentUser
) -> StructuredProfileProvenanceRead:
    with db.no_autoflush:
        row = db.scalar(select(CandidateStructuredProfile).where(
            CandidateStructuredProfile.user_id == current_user.id
        ))
        current = CandidateCVData.model_validate_json(row.structured_json) if row is not None else None
        return StructuredProfileProvenanceService(db).read_current(current_user.id, current)


@router.get("/revisions/active", response_model=CandidateProfileRevisionRead | None)
def read_active_profile_revision(
    db: DbSession, current_user: CurrentUser
) -> CandidateProfileRevisionRead | None:
    return CandidateProfileRevisionService(db).active(current_user.id)


@router.post("/revisions", response_model=CandidateProfileRevisionRead)
def create_profile_revision(
    db: DbSession, current_user: CurrentUser
) -> CandidateProfileRevisionRead:
    return CandidateProfileRevisionService(db).create_or_resume(current_user.id)


@router.patch("/revisions/{revision_id}", response_model=CandidateProfileRevisionRead)
def save_profile_revision(
    revision_id: str,
    payload: CandidateProfileRevisionPatch,
    db: DbSession,
    current_user: CurrentUser,
) -> CandidateProfileRevisionRead:
    try:
        patch_fields = payload.model_fields_set - {"expected_revision"}
        return CandidateProfileRevisionService(db).save(
            current_user.id,
            revision_id,
            expected_revision=payload.expected_revision,
            patch_fields=patch_fields,
            proposed_profile=payload.proposed_profile,
            proposed_structured=payload.proposed_structured,
        )
    except ProfileRevisionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile revision not found.") from exc
    except ProfileRevisionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/revisions/{revision_id}/review", response_model=CandidateProfileRevisionRead)
def review_profile_revision(
    revision_id: str,
    payload: CandidateProfileRevisionAction,
    db: DbSession,
    current_user: CurrentUser,
) -> CandidateProfileRevisionRead:
    try:
        return CandidateProfileRevisionService(db).review(
            current_user.id,
            revision_id,
            expected_revision=payload.expected_revision,
        )
    except ProfileRevisionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile revision not found.") from exc
    except ProfileRevisionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/revisions/{revision_id}/discard", response_model=CandidateProfileRevisionRead)
def discard_profile_revision(
    revision_id: str,
    payload: CandidateProfileRevisionAction,
    db: DbSession,
    current_user: CurrentUser,
) -> CandidateProfileRevisionRead:
    try:
        return CandidateProfileRevisionService(db).discard(
            current_user.id,
            revision_id,
            expected_revision=payload.expected_revision,
        )
    except ProfileRevisionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile revision not found.") from exc
    except ProfileRevisionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/revisions/{revision_id}/confirm", response_model=CandidateProfileRevisionRead)
def confirm_profile_revision(
    revision_id: str,
    payload: CandidateProfileRevisionAction,
    db: DbSession,
    current_user: CurrentUser,
) -> CandidateProfileRevisionRead:
    try:
        return CandidateProfileRevisionService(db).confirm(
            current_user.id,
            revision_id,
            expected_revision=payload.expected_revision,
        )
    except ProfileRevisionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile revision not found.") from exc
    except ProfileRevisionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/context-summary", response_model=CandidateContextSummary)
def context_summary(
    current_user: CurrentUser,
    reader: CanonicalCandidateReadService = Depends(get_canonical_candidate_read_service),
) -> CandidateContextSummary:
    return reader.summary(reader.read(current_user.id))


@router.get("/snapshot", response_model=CanonicalCandidateReadSnapshot)
def read_candidate_snapshot(
    current_user: CurrentUser,
    reader: CanonicalCandidateReadService = Depends(get_canonical_candidate_read_service),
) -> CanonicalCandidateReadSnapshot:
    """Return the authenticated user's typed, side-effect-free candidate snapshot."""
    return reader.read(current_user.id)


@router.get("", response_model=CandidateProfileRead)
def read_profile(db: DbSession, current_user: CurrentUser) -> CandidateProfileRead:
    profile = get_profile_for_user(db, current_user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found.")
    return profile


@router.post("")
def create_profile(_: CurrentUser) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Profile changes must use the Profile revision workflow.",
    )


@router.patch("")
def update_profile(_: CurrentUser) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Profile changes must use the Profile revision workflow.",
    )

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    CurrentUser,
    DbSession,
    get_canonical_candidate_read_service,
)
from app.schemas.candidate import CandidateContextSummary
from app.schemas.candidate_read_snapshot import CanonicalCandidateReadSnapshot
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.schemas.candidate_profile import (
    CandidateProfileCreate,
    CandidateProfileRead,
    CandidateProfileUpdate,
)
from app.services.profile_service import (
    create_profile_for_user,
    get_profile_for_user,
    update_profile_for_user,
)

router = APIRouter(prefix="/profile", tags=["profile"])


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


@router.post("", response_model=CandidateProfileRead, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: CandidateProfileCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> CandidateProfileRead:
    if get_profile_for_user(db, current_user.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A profile already exists for this user.",
        )
    return create_profile_for_user(db, current_user.id, payload)


@router.patch("", response_model=CandidateProfileRead)
def update_profile(
    payload: CandidateProfileUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> CandidateProfileRead:
    profile = get_profile_for_user(db, current_user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found.")
    return update_profile_for_user(db, profile, payload)

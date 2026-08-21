from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
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

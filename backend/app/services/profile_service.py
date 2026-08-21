from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_profile import CandidateProfile
from app.schemas.candidate_profile import CandidateProfileCreate, CandidateProfileUpdate


def get_profile_for_user(db: Session, user_id: str) -> CandidateProfile | None:
    return db.scalar(
        select(CandidateProfile).where(CandidateProfile.user_id == user_id)
    )


def create_profile_for_user(
    db: Session,
    user_id: str,
    profile_data: CandidateProfileCreate,
) -> CandidateProfile:
    profile = CandidateProfile(user_id=user_id, **profile_data.model_dump())
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def update_profile_for_user(
    db: Session,
    profile: CandidateProfile,
    profile_data: CandidateProfileUpdate,
) -> CandidateProfile:
    updates = profile_data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(profile, field, value)

    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile

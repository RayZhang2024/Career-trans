from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_profile import CandidateProfile


def get_profile_for_user(db: Session, user_id: str) -> CandidateProfile | None:
    return db.scalar(
        select(CandidateProfile).where(CandidateProfile.user_id == user_id)
    )

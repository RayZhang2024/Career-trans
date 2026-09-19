from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.onboarding import OnboardingStatusRead
from app.services.onboarding_status_service import OnboardingStatusService


router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/status", response_model=OnboardingStatusRead)
def status(db: DbSession, current_user: CurrentUser) -> OnboardingStatusRead:
    return OnboardingStatusService(db).read(current_user.id)

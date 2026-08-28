from sqlalchemy import select

from app.models.user import User
from app.schemas.candidate_adviser import CandidateIntakeProfileData
from app.services.candidate_adviser_service import CandidateAdviserService


def test_unchanged_intake_save_preserves_revision_and_confirmation(client, db_session) -> None:
    credentials = {"email": "adviser-idempotent@example.com", "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    user_id = db_session.scalar(select(User.id).where(User.email == credentials["email"]))
    assert user_id is not None

    service = CandidateAdviserService(db_session)
    intake = CandidateIntakeProfileData.model_validate(
        {"career_direction": {"short_term_goal": "Move into applied AI delivery."}}
    )

    first = service.save_intake(user_id, intake)
    assert first.revision == 1
    assert first.confirmed is False

    confirmed = service.confirm_intake(user_id)
    assert confirmed.revision == 1
    assert confirmed.confirmed is True

    unchanged = service.save_intake(user_id, intake.model_copy(deep=True))
    assert unchanged.revision == 1
    assert unchanged.confirmed is True

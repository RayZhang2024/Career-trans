from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.password_policy import PasswordPolicyError
from app.core.security import create_access_token
from app.schemas.auth import (
    ChangePasswordRead,
    ChangePasswordRequest,
    LoginRequest,
    PasswordPolicyRead,
    RegisterRequest,
    TokenResponse,
)
from app.schemas.user import UserRead
from app.services.auth_service import (
    CurrentPasswordIncorrect,
    NewPasswordUnchanged,
    PasswordChangeConflict,
    PasswordChangeStorageError,
    authenticate_user,
    change_password,
    create_user,
    get_user_by_email,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/password-policy", response_model=PasswordPolicyRead)
def password_policy() -> PasswordPolicyRead:
    return PasswordPolicyRead.current()


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> UserRead:
    if get_user_by_email(db, str(payload.email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    try:
        return create_user(db, str(payload.email), payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.reason) from exc


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    user = authenticate_user(db, str(payload.email), payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(access_token=create_access_token(user.id, auth_version=user.auth_version))


@router.post("/change-password", response_model=ChangePasswordRead)
def change_authenticated_password(
    payload: ChangePasswordRequest,
    user: CurrentUser,
    db: DbSession,
) -> None:
    try:
        change_password(db, user, payload.current_password, payload.new_password)
    except CurrentPasswordIncorrect as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="current_password_incorrect") from exc
    except NewPasswordUnchanged as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="new_password_must_differ") from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.reason) from exc
    except PasswordChangeConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="password_change_conflict") from exc
    except PasswordChangeStorageError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="password_change_unavailable") from exc
    return ChangePasswordRead(changed=True)

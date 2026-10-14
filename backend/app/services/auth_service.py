from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.password_policy import validate_new_password
from app.core.security import hash_password, verify_password
from app.models.user import User


class CurrentPasswordIncorrect(ValueError):
    pass


class NewPasswordUnchanged(ValueError):
    pass


class PasswordChangeConflict(ValueError):
    pass


class PasswordChangeStorageError(RuntimeError):
    pass


def get_user_by_email(db: Session, email: str) -> User | None:
    normalized_email = email.strip().lower()
    return db.scalar(select(User).where(User.email == normalized_email))


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def create_user(db: Session, email: str, password: str) -> User:
    validate_new_password(password)
    user = User(email=email.strip().lower(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    """Atomically replace a password and revoke all tokens issued at older generations."""
    if not verify_password(current_password, user.password_hash):
        raise CurrentPasswordIncorrect
    validate_new_password(new_password)
    if verify_password(new_password, user.password_hash):
        raise NewPasswordUnchanged

    replacement_hash = hash_password(new_password)
    statement = (
        update(User)
        .where(
            User.id == user.id,
            User.password_hash == user.password_hash,
            User.auth_version == user.auth_version,
        )
        .values(
            password_hash=replacement_hash,
            auth_version=User.auth_version + 1,
            updated_at=datetime.now(timezone.utc),
        )
    )
    try:
        result = db.execute(statement)
        if result.rowcount != 1:
            db.rollback()
            raise PasswordChangeConflict
        db.commit()
        db.expire(user)
    except PasswordChangeConflict:
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise PasswordChangeStorageError from exc

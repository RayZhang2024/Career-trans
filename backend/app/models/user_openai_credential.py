"""Encrypted per-user OpenAI semantic credential, separate from preferences."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserOpenAICredential(Base):
    __tablename__ = "user_openai_credentials"
    __table_args__ = (
        CheckConstraint("length(nonce) = 12", name="ck_user_openai_credential_nonce"),
        CheckConstraint("format_version = 1", name="ck_user_openai_credential_format_version"),
        CheckConstraint("revision >= 1", name="ck_user_openai_credential_revision"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    format_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    user: Mapped["User"] = relationship(back_populates="openai_credential")

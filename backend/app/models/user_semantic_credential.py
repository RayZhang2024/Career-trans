"""Encrypted provider-neutral per-user semantic credentials."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserSemanticCredential(Base):
    __tablename__ = "user_semantic_credentials"
    __table_args__ = (
        CheckConstraint("provider IN ('openai')", name="ck_user_semantic_credential_provider"),
        CheckConstraint("length(nonce) = 12", name="ck_user_semantic_credential_nonce"),
        CheckConstraint("format_version = 1", name="ck_user_semantic_credential_format_version"),
        CheckConstraint("revision >= 1", name="ck_user_semantic_credential_revision"),
        CheckConstraint("display_suffix IS NULL OR length(display_suffix) = 4", name="ck_user_semantic_credential_suffix"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    format_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    display_suffix: Mapped[str | None] = mapped_column(String(4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    user: Mapped["User"] = relationship(back_populates="semantic_credentials")

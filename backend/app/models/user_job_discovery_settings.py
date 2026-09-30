from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserJobDiscoverySettings(Base):
    """Provider override and revision, separate from semantic AI settings."""

    __tablename__ = "user_job_discovery_settings"
    __table_args__ = (
        CheckConstraint(
            "provider_override IS NULL OR provider_override IN ('tavily', 'openai', 'disabled')",
            name="ck_user_job_discovery_provider_override",
        ),
        CheckConstraint("revision >= 1", name="ck_user_job_discovery_revision"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider_override: Mapped[str | None] = mapped_column(String(24), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    user: Mapped["User"] = relationship(back_populates="job_discovery_settings")


class UserTavilyCredential(Base):
    """Encrypted per-user Tavily credential; plaintext is never persisted."""

    __tablename__ = "user_tavily_credentials"
    __table_args__ = (
        CheckConstraint("length(nonce) = 12", name="ck_user_tavily_credential_nonce"),
        CheckConstraint("format_version = 1", name="ck_user_tavily_credential_format_version"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    format_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    user: Mapped["User"] = relationship(back_populates="tavily_credential")

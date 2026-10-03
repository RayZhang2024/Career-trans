from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    profile = relationship(
        "CandidateProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    job_discovery_settings = relationship(
        "UserJobDiscoverySettings",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    tavily_credential = relationship(
        "UserTavilyCredential",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    openai_credential = relationship(
        "UserOpenAICredential",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

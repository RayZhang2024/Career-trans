from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CompanyCareerSource(Base):
    """Candidate-agnostic, reusable mapping from a company to a public career source."""

    __tablename__ = "company_career_sources"
    __table_args__ = (
        UniqueConstraint("canonical_company_key", name="uq_company_career_sources_company"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_company_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    careers_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    diagnostic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_successful_resolution_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_fetch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

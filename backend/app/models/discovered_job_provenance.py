from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DiscoveredJobProvenance(Base):
    """Bounded factual evidence origin attached to the shared discovered-job record."""

    __tablename__ = "discovered_job_provenance"
    __table_args__ = (
        UniqueConstraint("job_id", "fingerprint", name="uq_discovered_job_provenance_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("discovered_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    discovered_via: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

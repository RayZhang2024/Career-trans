from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ApplicationTrackingRecord(Base):
    """Current user-recorded lifecycle state for one owned preparation."""

    __tablename__ = "application_tracking"
    __table_args__ = (
        UniqueConstraint("preparation_id", name="uq_application_tracking_preparation"),
        CheckConstraint("revision >= 1", name="ck_application_tracking_revision_positive"),
        CheckConstraint(
            "current_status IN ('prepared', 'applied', 'interview', 'rejected', 'offer', 'withdrawn')",
            name="ck_application_tracking_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    preparation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("application_preparations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    current_status: Mapped[str] = mapped_column(String(20), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ApplicationTrackingEvent(Base):
    """Append-only record of an explicitly user-recorded status transition."""

    __tablename__ = "application_tracking_events"
    __table_args__ = (
        UniqueConstraint("tracking_id", "revision", name="uq_application_tracking_event_revision"),
        CheckConstraint(
            "(revision = 1 AND from_status IS NULL) OR (revision > 1 AND from_status IS NOT NULL)",
            name="ck_application_tracking_event_from_status",
        ),
        CheckConstraint("revision >= 1", name="ck_application_tracking_event_revision_positive"),
        CheckConstraint(
            "to_status IN ('prepared', 'applied', 'interview', 'rejected', 'offer', 'withdrawn')",
            name="ck_application_tracking_event_to_status",
        ),
        CheckConstraint(
            "from_status IS NULL OR from_status IN ('prepared', 'applied', 'interview', 'rejected', 'offer', 'withdrawn')",
            name="ck_application_tracking_event_from_status_value",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tracking_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("application_tracking.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

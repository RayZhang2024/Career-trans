from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserJobDecision(Base):
    __tablename__ = "user_job_decisions"
    __table_args__ = (
        UniqueConstraint("user_id", "discovered_job_id", name="uq_user_job_decision_user_job"),
        CheckConstraint("revision >= 1", name="ck_user_job_decision_revision_positive"),
        CheckConstraint("decision IN ('undecided', 'shortlisted', 'dismissed')", name="ck_user_job_decision_decision_valid"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    discovered_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("discovered_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

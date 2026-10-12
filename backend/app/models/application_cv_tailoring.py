from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class ApplicationCVTailoringSession(Base):
    """User-owned review lifecycle, separate from immutable final preparations."""
    __tablename__ = "application_cv_tailoring_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "client_request_id", name="uq_cv_tailoring_request_user"),
        UniqueConstraint("user_id", "draft_client_request_id", name="uq_cv_tailoring_draft_request_user"),
        Index("ix_cv_tailoring_user_job_created", "user_id", "discovered_job_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    discovered_job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_status: Mapped[str] = mapped_column(String(32), nullable=False)
    draft_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    target_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    identity_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    profile_outline_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_evaluation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_contract_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    runtime_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    target_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    strategy_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_review_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    strategy_history_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    strategy_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draft_client_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    draft_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_history_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_preparation_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("application_preparations.id", ondelete="SET NULL"), nullable=True)
    runtime_attribution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

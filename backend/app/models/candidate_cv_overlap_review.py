from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CandidateCVOverlapReviewRecord(Base):
    """User-owned choices for one exact reviewed CV/base-authority snapshot."""

    __tablename__ = "candidate_cv_overlap_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    draft_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("candidate_cv_ingestion_drafts.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": revision}
    base_structured_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resolutions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

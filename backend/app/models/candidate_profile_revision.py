from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CandidateProfileRevisionRecord(Base):
    """Persisted proposal based on separate current Profile authorities."""

    __tablename__ = "candidate_profile_revisions"
    __table_args__ = (
        UniqueConstraint("active_user_id", name="uq_candidate_profile_revisions_active_user"),
        CheckConstraint(
            "((state IN ('draft', 'review_ready') AND active_user_id = user_id) "
            "OR (state IN ('confirmed', 'discarded') AND active_user_id IS NULL))",
            name="ck_candidate_profile_revisions_active_slot",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A nullable unique slot is portable across SQLite and PostgreSQL, unlike a
    # filtered unique index. Terminal records release the per-user active slot.
    active_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": revision}
    base_profile_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    base_structured_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    base_editable_structured_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_profile_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_structured_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

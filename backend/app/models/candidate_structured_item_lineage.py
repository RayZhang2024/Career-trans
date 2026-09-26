from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import event

from app.core.database import Base


class CandidateStructuredItemLineageRecord(Base):
    """Prospective source/history metadata; never a source of current candidate truth."""

    __tablename__ = "candidate_structured_item_lineage"
    __table_args__ = (
        UniqueConstraint("user_id", "lineage_key", name="uq_structured_item_lineage_user_key"),
        CheckConstraint("source_kind IN ('cv', 'manual_profile', 'candidate_adviser')", name="ck_structured_item_lineage_source_kind"),
        CheckConstraint("relationship IN ('new', 'reinforcement', 'refinement', 'conflict', 'ambiguous')", name="ck_structured_item_lineage_relationship"),
        CheckConstraint("section IN ('employment', 'education', 'credentials', 'skills', 'projects', 'achievements')", name="ck_structured_item_lineage_section"),
        Index("ix_structured_item_lineage_user_item", "user_id", "section", "item_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    lineage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    item_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    item_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    relationship: Mapped[str] = mapped_column(String(32), nullable=False)
    predecessor_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    predecessor_item_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


@event.listens_for(CandidateStructuredItemLineageRecord, "before_update")
def _lineage_rows_are_immutable(mapper, connection, target) -> None:
    raise ValueError("Structured item lineage records are immutable history.")

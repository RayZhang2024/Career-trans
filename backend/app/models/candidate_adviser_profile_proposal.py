from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CandidateAdviserProfileProposalRecord(Base):
    """Noncanonical structured Profile proposal sourced from a confirmed clarification."""

    __tablename__ = "candidate_adviser_profile_proposals"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "proposal_key",
            name="uq_candidate_adviser_profile_proposals_user_key",
        ),
        CheckConstraint(
            "state IN ('pending', 'rejected', 'transferred')",
            name="ck_candidate_adviser_profile_proposals_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposal_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": revision}
    source_clarification_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_assessment_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    original_update_json: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_update_json: Mapped[str] = mapped_column(Text, nullable=False)
    overlap_resolution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    transferred_profile_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_profile_revisions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc), nullable=False,
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

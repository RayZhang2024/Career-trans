from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CandidateAdviserIntakeRecord(Base):
    __tablename__ = "candidate_adviser_intakes"
    __table_args__ = (UniqueConstraint("user_id", name="uq_candidate_adviser_intakes_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    intake_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

class CandidateAdviserAssessmentRecord(Base):
    __tablename__ = "candidate_adviser_assessments"
    __table_args__ = (UniqueConstraint("user_id", name="uq_candidate_adviser_assessments_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="review_ready")
    assessment_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class CandidateAdviserClarificationRecord(Base):
    __tablename__ = "candidate_adviser_clarifications"
    __table_args__ = (UniqueConstraint("user_id", "clarification_id", name="uq_candidate_adviser_clarifications_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    clarification_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    question_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    origin_assessment_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_source_references_json: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_answers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority_index: Mapped[int] = mapped_column(nullable=False)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    interpretation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unanswered")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CVVisibilityAssessment(Base):
    """Private, immutable assessment record for one explicit CV visibility request."""

    __tablename__ = "cv_visibility_assessments"
    __table_args__ = (UniqueConstraint("user_id", "client_request_id", name="uq_cv_visibility_request_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Keep the job identity as a snapshot key without a cascade so deleting a
    # shared public vacancy cannot erase a user's historical private report.
    discovered_job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_draft_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_segment_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    job_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_evaluation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="in_progress")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_attribution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

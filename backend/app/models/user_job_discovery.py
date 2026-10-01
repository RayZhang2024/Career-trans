from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DiscoveryRun(Base):
    """One authenticated user's explicit discovery/evaluation execution."""

    __tablename__ = "discovery_runs"
    __table_args__ = (UniqueConstraint("user_id", "client_request_id", name="uq_discovery_run_client_request"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    client_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    search_input_json: Mapped[str] = mapped_column(Text, nullable=False)
    search_input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_evaluation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evaluation_contract_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    funnel_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    failure_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserJobEvaluation(Base):
    """Immutable, successful user-specific interpretation of one shared job."""

    __tablename__ = "user_job_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "discovered_job_id", "job_content_hash",
            "candidate_evaluation_fingerprint", "evaluation_contract_fingerprint",
            name="uq_user_job_evaluation_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    discovered_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("discovered_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    job_content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_evaluation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evaluation_contract_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_json: Mapped[str] = mapped_column(Text, nullable=False)
    runtime_attribution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class DiscoveryRunJob(Base):
    """Immutable run participation and safe outcome for a canonical shared job."""

    __tablename__ = "discovery_run_jobs"
    __table_args__ = (UniqueConstraint("discovery_run_id", "discovered_job_id", name="uq_discovery_run_job"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    discovery_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("discovery_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    discovered_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("discovered_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("user_job_evaluations.id", ondelete="SET NULL"), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(48), nullable=False)
    failure_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

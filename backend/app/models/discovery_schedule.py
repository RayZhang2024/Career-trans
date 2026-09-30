from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DiscoverySchedule(Base):
    __tablename__ = "discovery_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, index=True)
    schedule_spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    query_json: Mapped[str] = mapped_column(Text, nullable=False)
    acquisition_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_execution_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class ScheduledDiscoveryExecution(Base):
    __tablename__ = "scheduled_discovery_executions"
    __table_args__ = (UniqueConstraint("schedule_id", "scheduled_for", name="uq_scheduled_discovery_slot"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    schedule_id: Mapped[str] = mapped_column(String(36), ForeignKey("discovery_schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    web_search_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovery_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("discovery_runs.id", ondelete="SET NULL"), nullable=True)
    acquisition_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    failure_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

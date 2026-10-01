from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OneOffDiscoveryExecution(Base):
    """Durable authority for one transient, manually launched discovery."""

    __tablename__ = "one_off_discovery_executions"
    __table_args__ = (UniqueConstraint("user_id", "client_request_id", name="uq_one_off_discovery_request"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    query_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    policy_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    provider_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    failure_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    discovery_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("discovery_runs.id", ondelete="SET NULL"), nullable=True)

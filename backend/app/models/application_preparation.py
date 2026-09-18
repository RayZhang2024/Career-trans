from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ApplicationPreparation(Base):
    """Immutable, user-owned source data for a prepared application package."""

    __tablename__ = "application_preparations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    identity_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    preparation_input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    preparation_contract_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    preparation_result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

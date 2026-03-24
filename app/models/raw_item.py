import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UUID, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawItem(Base):
    __tablename__ = "raw_items"
    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", name="uq_raw_items_source_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

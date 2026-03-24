import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DigestPublication(Base):
    __tablename__ = "digest_publications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    digest_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("digest_pages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Channel type: "telegram" (extensible for future channels)
    channel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Target identifier: Telegram chat_id
    target: Mapped[str] = mapped_column(String(256), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Message ID returned by the channel provider (e.g. Telegram message_id)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Status: "sent", "failed"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="sent")
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "digest_page_id", "channel_type", "target",
            name="uq_digest_publications_page_channel_target",
        ),
    )

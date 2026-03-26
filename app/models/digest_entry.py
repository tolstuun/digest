import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DigestEntry(Base):
    __tablename__ = "digest_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    digest_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("digest_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL preserves digest history if a cluster is later deleted.
    event_cluster_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_clusters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    final_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Materialized display fields — copied at assembly time so entries survive
    # later cluster changes or cluster deletion.
    title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    canonical_summary_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    canonical_summary_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why_it_matters_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why_it_matters_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Representative source link — populated at assembly time from the rep story
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # Final polished copy written by the digest-writer LLM stage
    final_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_why_it_matters: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventClusterAssessment(Base):
    __tablename__ = "event_cluster_assessments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # One current assessment per cluster.
    event_cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_clusters.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Section assignment and digest decision
    primary_section: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    include_in_digest: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Scores
    rule_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    llm_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    final_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Editorial content
    why_it_matters_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why_it_matters_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    editorial_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # LLM provenance
    model_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    raw_model_output: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    assessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

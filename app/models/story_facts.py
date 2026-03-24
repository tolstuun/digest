import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StoryFacts(Base):
    __tablename__ = "story_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    story_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)

    # LLM provenance
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_model_output: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    extraction_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extracted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Extracted facts
    source_language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    event_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    company_names: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    person_names: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    product_names: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    geography_names: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    amount_text: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    canonical_summary_en: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    canonical_summary_ru: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("story_id", name="uq_story_facts_story_id"),)

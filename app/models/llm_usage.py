import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LlmUsage(Base):
    __tablename__ = "llm_usages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Which pipeline stage produced this call (extract_facts, assess, write_digest)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_usd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=10, scale=6), nullable=True
    )
    # UUID of the object this call was performed for (story_id, cluster_id, entry_id)
    related_object_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

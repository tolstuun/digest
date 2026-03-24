import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DigestRun(Base):
    __tablename__ = "digest_runs"
    __table_args__ = (
        UniqueConstraint("digest_date", "section_name", name="uq_digest_run_date_section"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    digest_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    section_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Status values: "assembled" (normal), "empty" (no candidates found)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="assembled")
    total_candidate_clusters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_included_clusters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

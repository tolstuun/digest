"""
Normalization service: converts a raw_item into a structured story.

Deterministic only — no LLM usage.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.raw_item import RawItem
from app.models.story import Story
from app.normalization.urls import canonicalize_url

logger = logging.getLogger(__name__)


def normalize_raw_item(db: Session, raw_item: RawItem) -> tuple[Story, bool]:
    """
    Normalize *raw_item* into a Story.

    Returns ``(story, created)`` where *created* is ``True`` when a new story
    was inserted, ``False`` when the story already existed (idempotent).

    One raw_item produces at most one story, enforced both here and by the
    unique constraint on ``stories.raw_item_id``.
    """
    existing = db.query(Story).filter_by(raw_item_id=raw_item.id).first()
    if existing is not None:
        logger.debug("Story already exists for raw_item id=%s", raw_item.id)
        return existing, False

    canonical = canonicalize_url(raw_item.url) if raw_item.url else None

    story = Story(
        raw_item_id=raw_item.id,
        source_id=raw_item.source_id,
        title=raw_item.title,
        url=raw_item.url,
        canonical_url=canonical,
        published_at=raw_item.published_at,
        normalized_at=datetime.now(timezone.utc),
    )
    db.add(story)
    db.commit()
    db.refresh(story)

    logger.info(
        "Normalized raw_item id=%s -> story id=%s canonical_url=%s",
        raw_item.id,
        story.id,
        story.canonical_url,
    )
    return story, True

"""
Ingestion service: orchestrates fetching, parsing, and raw item persistence.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ingestion.rss import parse_feed
from app.models.raw_item import RawItem
from app.models.source import Source

logger = logging.getLogger(__name__)


def ingest_source(db: Session, source: Source) -> dict:
    """
    Ingest one RSS source: fetch, parse, persist new raw items.

    Returns a summary dict: {fetched, new, skipped, error}.
    Idempotent: items already stored (by content_hash) are skipped.
    Only RSS sources are supported in this phase.
    """
    now = datetime.now(timezone.utc)
    summary: dict = {"fetched": 0, "new": 0, "skipped": 0, "error": None}

    if not source.enabled:
        summary["error"] = "source is disabled"
        return summary

    if source.type != "rss":
        summary["error"] = f"unsupported type '{source.type}' for ingestion (only 'rss' supported)"
        return summary

    if not source.url:
        summary["error"] = "source has no url configured"
        return summary

    try:
        items = parse_feed(source.url)
        summary["fetched"] = len(items)

        for item in items:
            exists = (
                db.query(RawItem)
                .filter_by(source_id=source.id, content_hash=item.content_hash)
                .first()
                is not None
            )
            if exists:
                summary["skipped"] += 1
            else:
                db.add(
                    RawItem(
                        source_id=source.id,
                        external_id=item.external_id,
                        content_hash=item.content_hash,
                        title=item.title,
                        url=item.url,
                        published_at=item.published_at,
                        raw_payload=item.raw_payload,
                        fetched_at=now,
                    )
                )
                summary["new"] += 1

        source.last_polled_at = now
        source.last_success_at = now
        source.last_error = None
        db.commit()

    except Exception as exc:
        db.rollback()
        logger.error(
            "Ingestion failed for source id=%s: %s", source.id, exc, exc_info=True
        )
        summary["error"] = str(exc)

        # Best-effort: record the error timestamp on the source (new transaction).
        try:
            db.refresh(source)
            source.last_polled_at = now
            source.last_error = str(exc)[:2048]
            db.commit()
        except Exception:
            db.rollback()

    logger.info(
        "Ingestion done source=%s fetched=%d new=%d skipped=%d error=%s",
        source.id,
        summary["fetched"],
        summary["new"],
        summary["skipped"],
        summary["error"],
    )
    return summary

"""
RSS/Atom feed fetching and parsing.

Deterministic only — no LLM usage, no DB access.
"""
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import feedparser

logger = logging.getLogger(__name__)


@dataclass
class RawFeedItem:
    external_id: Optional[str]
    content_hash: str  # hex SHA-256, used for deduplication
    title: Optional[str]
    url: Optional[str]
    published_at: Optional[datetime]
    raw_payload: dict  # JSON-safe subset of the feedparser entry


def _content_hash(external_id: Optional[str], url: Optional[str], title: Optional[str]) -> str:
    key = external_id or url or title or ""
    return hashlib.sha256(key.encode()).hexdigest()


def _parse_published(entry) -> Optional[datetime]:
    if entry.get("published_parsed"):
        ts = time.mktime(entry.published_parsed)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _entry_to_payload(entry) -> dict:
    """Extract key string fields from a feedparser entry into a JSON-safe dict."""
    payload: dict = {}
    for field in ("id", "title", "link", "summary", "author", "published", "updated"):
        val = entry.get(field)
        if val is not None:
            payload[field] = str(val)
    return payload


def _parse(parsed) -> list[RawFeedItem]:
    if parsed.bozo and not parsed.entries:
        logger.warning("Feed parse error: %s", parsed.get("bozo_exception"))

    items: list[RawFeedItem] = []
    for entry in parsed.entries:
        external_id = entry.get("id") or entry.get("link")
        url = entry.get("link")
        title = entry.get("title")

        items.append(
            RawFeedItem(
                external_id=external_id,
                content_hash=_content_hash(external_id, url, title),
                title=title,
                url=url,
                published_at=_parse_published(entry),
                raw_payload=_entry_to_payload(entry),
            )
        )

    return items


def parse_feed(url: str) -> list[RawFeedItem]:
    """Fetch and parse a remote RSS/Atom feed by URL."""
    logger.info("Fetching RSS feed url=%s", url)
    parsed = feedparser.parse(url)
    items = _parse(parsed)
    logger.info("Parsed %d items from url=%s", len(items), url)
    return items


def parse_feed_string(content: str) -> list[RawFeedItem]:
    """Parse an RSS/Atom feed from a raw string. Intended for testing."""
    return _parse(feedparser.parse(content))

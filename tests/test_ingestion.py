"""
Tests for RSS ingestion: parsing, raw item persistence, and duplicate avoidance.
"""
from unittest.mock import patch

from app.ingestion.rss import parse_feed_string
from app.ingestion.service import ingest_source
from app.models.raw_item import RawItem
from app.models.source import Source

# ── RSS fixture ───────────────────────────────────────────────────────────────

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Security Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Vendor A Acquires Vendor B for $500M</title>
      <link>https://example.com/article-1</link>
      <guid>guid-article-1</guid>
      <pubDate>Mon, 24 Mar 2026 10:00:00 +0000</pubDate>
      <description>Details about the acquisition.</description>
    </item>
    <item>
      <title>Series C Funding Round for StartupX</title>
      <link>https://example.com/article-2</link>
      <guid>guid-article-2</guid>
      <pubDate>Mon, 24 Mar 2026 11:00:00 +0000</pubDate>
      <description>StartupX raises $40M Series C.</description>
    </item>
  </channel>
</rss>"""


# ── pure RSS parsing (no DB) ──────────────────────────────────────────────────

def test_parse_rss_returns_items():
    items = parse_feed_string(SAMPLE_RSS)
    assert len(items) == 2


def test_parse_rss_item_fields():
    items = parse_feed_string(SAMPLE_RSS)
    item = items[0]
    assert item.title == "Vendor A Acquires Vendor B for $500M"
    assert item.url == "https://example.com/article-1"
    assert item.external_id == "guid-article-1"
    assert item.content_hash  # non-empty SHA-256 hex string
    assert len(item.content_hash) == 64


def test_parse_rss_unique_hashes():
    items = parse_feed_string(SAMPLE_RSS)
    hashes = [i.content_hash for i in items]
    assert len(set(hashes)) == len(hashes)


def test_parse_rss_published_at():
    items = parse_feed_string(SAMPLE_RSS)
    assert items[0].published_at is not None


def test_parse_rss_raw_payload_is_dict():
    items = parse_feed_string(SAMPLE_RSS)
    assert isinstance(items[0].raw_payload, dict)
    assert items[0].raw_payload.get("title") == "Vendor A Acquires Vendor B for $500M"


def test_parse_rss_empty_feed():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Empty</title></channel></rss>"""
    items = parse_feed_string(xml)
    assert items == []


# ── ingestion service (DB-backed) ─────────────────────────────────────────────

def _make_rss_source(db, **kwargs) -> Source:
    defaults = dict(name="Test RSS Feed", type="rss", url="https://example.com/feed.xml", enabled=True)
    defaults.update(kwargs)
    source = Source(**defaults)
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _parsed_items():
    return parse_feed_string(SAMPLE_RSS)


def test_ingest_disabled_source_returns_error(db):
    source = _make_rss_source(db, enabled=False)
    result = ingest_source(db, source)
    assert result["error"] is not None
    assert "disabled" in result["error"]


def test_ingest_unsupported_type_returns_error(db):
    source = Source(name="HTML", type="html", url="https://example.com", enabled=True)
    db.add(source)
    db.commit()
    db.refresh(source)
    result = ingest_source(db, source)
    assert result["error"] is not None


def test_ingest_missing_url_returns_error(db):
    source = Source(name="No URL", type="rss", enabled=True)
    db.add(source)
    db.commit()
    db.refresh(source)
    result = ingest_source(db, source)
    assert result["error"] is not None
    assert "url" in result["error"]


def test_ingest_stores_raw_items(db):
    source = _make_rss_source(db)
    with patch("app.ingestion.service.parse_feed", return_value=_parsed_items()):
        result = ingest_source(db, source)

    assert result["error"] is None
    assert result["fetched"] == 2
    assert result["new"] == 2
    assert result["skipped"] == 0

    items = db.query(RawItem).filter_by(source_id=source.id).all()
    assert len(items) == 2


def test_ingest_raw_item_fields(db):
    source = _make_rss_source(db)
    with patch("app.ingestion.service.parse_feed", return_value=_parsed_items()):
        ingest_source(db, source)

    item = (
        db.query(RawItem)
        .filter_by(source_id=source.id)
        .order_by(RawItem.created_at)
        .first()
    )
    assert item.title == "Vendor A Acquires Vendor B for $500M"
    assert item.url == "https://example.com/article-1"
    assert item.external_id == "guid-article-1"
    assert item.content_hash
    assert item.raw_payload is not None
    assert item.fetched_at is not None
    assert item.source_id == source.id


def test_ingest_avoids_duplicates(db):
    source = _make_rss_source(db)
    items = _parsed_items()

    with patch("app.ingestion.service.parse_feed", return_value=items):
        result1 = ingest_source(db, source)

    with patch("app.ingestion.service.parse_feed", return_value=items):
        result2 = ingest_source(db, source)

    assert result1["new"] == 2
    assert result2["new"] == 0
    assert result2["skipped"] == 2
    assert db.query(RawItem).filter_by(source_id=source.id).count() == 2


def test_ingest_updates_source_timestamps(db):
    source = _make_rss_source(db)
    assert source.last_polled_at is None
    assert source.last_success_at is None

    with patch("app.ingestion.service.parse_feed", return_value=[]):
        ingest_source(db, source)

    db.refresh(source)
    assert source.last_polled_at is not None
    assert source.last_success_at is not None
    assert source.last_error is None


# ── admin endpoint ────────────────────────────────────────────────────────────

def test_ingest_endpoint_success(client, db):
    source = _make_rss_source(db)
    with patch("app.routers.admin.ingest_source") as mock_ingest:
        mock_ingest.return_value = {"fetched": 2, "new": 2, "skipped": 0, "error": None}
        resp = client.post(f"/admin/sources/{source.id}/ingest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fetched"] == 2
    assert data["new"] == 2
    assert data["error"] is None


def test_ingest_endpoint_source_not_found(client):
    resp = client.post("/admin/sources/00000000-0000-0000-0000-000000000000/ingest")
    assert resp.status_code == 404

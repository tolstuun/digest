"""
Tests for Phase 2A: URL canonicalization and story normalization.
"""
import pytest

from app.normalization.urls import canonicalize_url
from app.normalization.service import normalize_raw_item
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story


# ── URL canonicalization (pure, no DB) ───────────────────────────────────────

def test_canonicalize_lowercases_scheme_and_host():
    assert canonicalize_url("HTTPS://Example.COM/path") == "https://example.com/path"


def test_canonicalize_removes_fragment():
    assert canonicalize_url("https://example.com/article#comments") == "https://example.com/article"


def test_canonicalize_strips_utm_params():
    url = "https://example.com/article?utm_source=twitter&utm_medium=social"
    assert canonicalize_url(url) == "https://example.com/article"


def test_canonicalize_strips_all_tracking_params():
    url = "https://example.com/p?utm_source=x&utm_medium=y&utm_campaign=z&utm_term=a&utm_content=b&fbclid=abc&gclid=def"
    assert canonicalize_url(url) == "https://example.com/p"


def test_canonicalize_preserves_non_tracking_params():
    url = "https://example.com/search?q=security&page=2"
    result = canonicalize_url(url)
    assert "q=security" in result
    assert "page=2" in result


def test_canonicalize_strips_tracking_but_preserves_content_params():
    url = "https://example.com/article?id=123&utm_source=feed"
    result = canonicalize_url(url)
    assert "id=123" in result
    assert "utm_source" not in result


def test_canonicalize_no_query_string_unchanged():
    url = "https://example.com/article"
    assert canonicalize_url(url) == url


def test_canonicalize_handles_invalid_url_gracefully():
    bad = "not a url at all"
    assert canonicalize_url(bad) == bad


def test_canonicalize_empty_string():
    assert canonicalize_url("") == ""


def test_canonicalize_removes_fragment_keeps_query():
    url = "https://example.com/p?id=5#section"
    result = canonicalize_url(url)
    assert "#section" not in result
    assert "id=5" in result


# ── normalization service (DB-backed) ─────────────────────────────────────────

def _make_source(db) -> Source:
    s = Source(name="Feed", type="rss", url="https://example.com/feed", enabled=True)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _make_raw_item(db, source: Source, **kwargs) -> RawItem:
    import hashlib, uuid as _uuid
    defaults = dict(
        source_id=source.id,
        external_id="guid-001",
        content_hash=hashlib.sha256(b"guid-001").hexdigest(),
        title="Vendor A Acquires Vendor B",
        url="https://example.com/article?utm_source=rss",
        raw_payload={"title": "Vendor A Acquires Vendor B"},
    )
    defaults.update(kwargs)
    ri = RawItem(**defaults)
    db.add(ri)
    db.commit()
    db.refresh(ri)
    return ri


def test_normalize_creates_story(db):
    source = _make_source(db)
    raw = _make_raw_item(db, source)

    story, created = normalize_raw_item(db, raw)

    assert created is True
    assert story.raw_item_id == raw.id
    assert story.source_id == source.id
    assert story.title == raw.title
    assert story.url == raw.url


def test_normalize_sets_canonical_url(db):
    source = _make_source(db)
    raw = _make_raw_item(db, source, url="https://Example.COM/article?utm_source=rss#top")

    story, _ = normalize_raw_item(db, raw)

    assert story.canonical_url == "https://example.com/article"


def test_normalize_idempotent_returns_same_story(db):
    source = _make_source(db)
    raw = _make_raw_item(db, source)

    story1, created1 = normalize_raw_item(db, raw)
    story2, created2 = normalize_raw_item(db, raw)

    assert created1 is True
    assert created2 is False
    assert story1.id == story2.id


def test_normalize_idempotent_no_duplicate_in_db(db):
    source = _make_source(db)
    raw = _make_raw_item(db, source)

    normalize_raw_item(db, raw)
    normalize_raw_item(db, raw)

    count = db.query(Story).filter_by(raw_item_id=raw.id).count()
    assert count == 1


def test_normalize_sets_normalized_at(db):
    source = _make_source(db)
    raw = _make_raw_item(db, source)

    story, _ = normalize_raw_item(db, raw)

    assert story.normalized_at is not None


def test_normalize_raw_item_with_no_url(db):
    """Raw items without a URL still produce a story."""
    source = _make_source(db)
    raw = _make_raw_item(db, source, url=None)

    story, created = normalize_raw_item(db, raw)

    assert created is True
    assert story.url is None
    assert story.canonical_url is None


# ── stories API ───────────────────────────────────────────────────────────────

def test_list_stories_empty(client):
    resp = client.get("/stories/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_stories_returns_created(client, db):
    source = _make_source(db)
    raw = _make_raw_item(db, source)
    normalize_raw_item(db, raw)

    resp = client.get("/stories/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Vendor A Acquires Vendor B"


def test_get_story_by_id_found(client, db):
    source = _make_source(db)
    raw = _make_raw_item(db, source)
    story, _ = normalize_raw_item(db, raw)

    resp = client.get(f"/stories/{story.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(story.id)


def test_get_story_by_id_not_found(client):
    resp = client.get("/stories/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# ── admin normalize endpoint ──────────────────────────────────────────────────

def test_normalize_endpoint_source_not_found(client):
    resp = client.post("/admin/sources/00000000-0000-0000-0000-000000000000/normalize")
    assert resp.status_code == 404


def test_normalize_endpoint_no_raw_items(client, db):
    source = _make_source(db)
    resp = client.post(f"/admin/sources/{source.id}/normalize")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["new"] == 0
    assert data["skipped"] == 0


def test_normalize_endpoint_creates_stories(client, db):
    source = _make_source(db)
    # Create two raw items with different content_hashes
    import hashlib
    ri1 = RawItem(
        source_id=source.id, external_id="g1",
        content_hash=hashlib.sha256(b"g1").hexdigest(),
        title="Story One", url="https://example.com/1",
        raw_payload={},
    )
    ri2 = RawItem(
        source_id=source.id, external_id="g2",
        content_hash=hashlib.sha256(b"g2").hexdigest(),
        title="Story Two", url="https://example.com/2",
        raw_payload={},
    )
    db.add_all([ri1, ri2])
    db.commit()

    resp = client.post(f"/admin/sources/{source.id}/normalize")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["new"] == 2
    assert data["skipped"] == 0


def test_normalize_endpoint_idempotent(client, db):
    source = _make_source(db)
    import hashlib
    ri = RawItem(
        source_id=source.id, external_id="g1",
        content_hash=hashlib.sha256(b"g1").hexdigest(),
        title="Article", url="https://example.com/a",
        raw_payload={},
    )
    db.add(ri)
    db.commit()

    resp1 = client.post(f"/admin/sources/{source.id}/normalize")
    resp2 = client.post(f"/admin/sources/{source.id}/normalize")

    assert resp1.json()["new"] == 1
    assert resp2.json()["new"] == 0
    assert resp2.json()["skipped"] == 1

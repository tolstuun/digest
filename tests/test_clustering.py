"""
Tests for Phase 3A: deterministic event clustering from extracted facts.

No LLM calls — clustering is purely rule-based.
"""
import hashlib
import uuid

import pytest

from app.clustering.rules import build_cluster_key
from app.clustering.service import cluster_story
from app.models.event_cluster import EventCluster
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts


# ── test fixtures ─────────────────────────────────────────────────────────────

def _make_story(
    db,
    summary: str = "Acme Corp raised $50M in Series B.",
    title: str = "Acme Corp Raises $50M",
) -> Story:
    """Create a minimal Source → RawItem → Story chain."""
    source = Source(name="Feed", type="rss", url="https://example.com/feed", enabled=True)
    db.add(source)
    db.flush()

    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(summary.encode()).hexdigest(),
        title=title,
        url="https://example.com/article",
        raw_payload={"title": title, "summary": summary},
    )
    db.add(ri)
    db.flush()

    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title=title,
        url="https://example.com/article",
        canonical_url="https://example.com/article",
    )
    db.add(story)
    db.commit()
    db.refresh(story)
    return story


def _make_facts(
    db,
    story: Story,
    event_type: str = "funding",
    company_names: list | None = None,
    amount_text: str | None = "$50M",
    currency: str | None = "USD",
) -> StoryFacts:
    """Create a StoryFacts row for the given story."""
    if company_names is None:
        company_names = ["Acme Corp"]
    facts = StoryFacts(
        story_id=story.id,
        model_name="claude-haiku-4-5-20251001",
        event_type=event_type,
        company_names=company_names,
        person_names=[],
        product_names=[],
        geography_names=[],
        amount_text=amount_text,
        currency=currency,
        source_language="en",
        canonical_summary_en="Acme raised $50M.",
        canonical_summary_ru="Acme привлекла $50M.",
        extraction_confidence=0.92,
    )
    db.add(facts)
    db.commit()
    db.refresh(facts)
    return facts


# ── build_cluster_key (pure, no DB) ──────────────────────────────────────────

def test_cluster_key_valid_facts_returns_string():
    key = build_cluster_key("funding", ["Acme Corp"], "$50M", "USD")
    assert key is not None
    assert isinstance(key, str)
    assert len(key) > 0


def test_cluster_key_event_type_unknown_returns_none():
    key = build_cluster_key("unknown", ["Acme Corp"], "$50M", "USD")
    assert key is None


def test_cluster_key_event_type_other_returns_none():
    key = build_cluster_key("other", ["Acme Corp"], "$50M", "USD")
    assert key is None


def test_cluster_key_empty_companies_returns_none():
    key = build_cluster_key("funding", [], "$50M", "USD")
    assert key is None


def test_cluster_key_companies_sorted_deterministically():
    key1 = build_cluster_key("funding", ["Acme Corp", "Beta Inc"], None, None)
    key2 = build_cluster_key("funding", ["Beta Inc", "Acme Corp"], None, None)
    assert key1 == key2


def test_cluster_key_companies_normalized_case():
    key1 = build_cluster_key("funding", ["ACME CORP"], None, None)
    key2 = build_cluster_key("funding", ["acme corp"], None, None)
    assert key1 == key2


def test_cluster_key_amount_included_when_present():
    key_with = build_cluster_key("funding", ["Acme"], "$50M", None)
    key_without = build_cluster_key("funding", ["Acme"], None, None)
    assert key_with != key_without


def test_cluster_key_currency_included_when_present():
    key_with = build_cluster_key("funding", ["Acme"], None, "USD")
    key_without = build_cluster_key("funding", ["Acme"], None, None)
    assert key_with != key_without


def test_cluster_key_different_event_types_differ():
    key1 = build_cluster_key("funding", ["Acme"], None, None)
    key2 = build_cluster_key("mna", ["Acme"], None, None)
    assert key1 != key2


# ── cluster_story service (DB-backed) ────────────────────────────────────────

def test_first_story_creates_new_cluster(db):
    story = _make_story(db)
    facts = _make_facts(db, story)
    cluster, created = cluster_story(db, story, facts)
    assert created is True
    assert cluster is not None
    assert cluster.event_type == "funding"
    db.refresh(story)
    assert story.event_cluster_id == cluster.id


def test_first_story_becomes_representative(db):
    story = _make_story(db)
    facts = _make_facts(db, story)
    cluster, _ = cluster_story(db, story, facts)
    assert cluster.representative_story_id == story.id


def test_second_matching_story_joins_existing_cluster(db):
    story1 = _make_story(db, summary="Acme story 1")
    facts1 = _make_facts(db, story1)
    cluster1, created1 = cluster_story(db, story1, facts1)

    story2 = _make_story(db, summary="Acme story 2 — different article")
    facts2 = _make_facts(db, story2)
    cluster2, created2 = cluster_story(db, story2, facts2)

    assert created1 is True
    assert created2 is False
    assert cluster1.id == cluster2.id
    db.refresh(story2)
    assert story2.event_cluster_id == cluster1.id


def test_second_story_does_not_replace_representative(db):
    story1 = _make_story(db, summary="Acme story 1")
    facts1 = _make_facts(db, story1)
    cluster1, _ = cluster_story(db, story1, facts1)

    story2 = _make_story(db, summary="Acme story 2 — different article")
    facts2 = _make_facts(db, story2)
    cluster2, _ = cluster_story(db, story2, facts2)

    db.refresh(cluster1)
    assert cluster1.representative_story_id == story1.id  # unchanged


def test_cluster_story_idempotent(db):
    story = _make_story(db)
    facts = _make_facts(db, story)
    cluster1, created1 = cluster_story(db, story, facts)
    cluster2, created2 = cluster_story(db, story, facts)
    assert cluster1.id == cluster2.id
    assert created1 is True
    assert created2 is False
    assert db.query(EventCluster).count() == 1


def test_cluster_story_unknown_event_type_not_clustered(db):
    story = _make_story(db)
    facts = _make_facts(db, story, event_type="unknown")
    result_cluster, created = cluster_story(db, story, facts)
    assert result_cluster is None
    assert created is False
    db.refresh(story)
    assert story.event_cluster_id is None


def test_cluster_story_other_event_type_not_clustered(db):
    story = _make_story(db)
    facts = _make_facts(db, story, event_type="other")
    result_cluster, created = cluster_story(db, story, facts)
    assert result_cluster is None
    assert created is False


def test_cluster_story_no_companies_not_clustered(db):
    story = _make_story(db)
    facts = _make_facts(db, story, company_names=[])
    result_cluster, created = cluster_story(db, story, facts)
    assert result_cluster is None
    assert created is False


def test_different_companies_get_different_clusters(db):
    story1 = _make_story(db, summary="Acme funding")
    facts1 = _make_facts(db, story1, company_names=["Acme Corp"])

    story2 = _make_story(db, summary="Beta Corp funding")
    facts2 = _make_facts(db, story2, company_names=["Beta Corp"])

    cluster1, _ = cluster_story(db, story1, facts1)
    cluster2, _ = cluster_story(db, story2, facts2)
    assert cluster1.id != cluster2.id


def test_different_event_types_get_different_clusters(db):
    story1 = _make_story(db, summary="Acme funding round")
    facts1 = _make_facts(db, story1, event_type="funding")

    story2 = _make_story(db, summary="Acme mna deal")
    facts2 = _make_facts(db, story2, event_type="mna")

    cluster1, _ = cluster_story(db, story1, facts1)
    cluster2, _ = cluster_story(db, story2, facts2)
    assert cluster1.id != cluster2.id


# ── GET /event-clusters/ ─────────────────────────────────────────────────────

def test_list_event_clusters_empty(client):
    resp = client.get("/event-clusters/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_event_clusters_returns_cluster(client, db):
    story = _make_story(db)
    facts = _make_facts(db, story)
    cluster, _ = cluster_story(db, story, facts)

    resp = client.get("/event-clusters/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(cluster.id)
    assert data[0]["event_type"] == "funding"
    assert data[0]["story_count"] == 1
    assert str(story.id) in data[0]["story_ids"]


def test_list_event_clusters_story_count(client, db):
    story1 = _make_story(db, summary="Acme story 1")
    facts1 = _make_facts(db, story1)
    story2 = _make_story(db, summary="Acme story 2 — same event")
    facts2 = _make_facts(db, story2)
    cluster_story(db, story1, facts1)
    cluster_story(db, story2, facts2)

    resp = client.get("/event-clusters/")
    assert resp.json()[0]["story_count"] == 2


# ── GET /event-clusters/{cluster_id} ─────────────────────────────────────────

def test_get_event_cluster_detail(client, db):
    story = _make_story(db)
    facts = _make_facts(db, story)
    cluster, _ = cluster_story(db, story, facts)

    resp = client.get(f"/event-clusters/{cluster.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(cluster.id)
    assert data["cluster_key"] is not None
    assert data["representative_story_id"] == str(story.id)
    assert data["story_count"] == 1
    assert str(story.id) in data["story_ids"]


def test_get_event_cluster_not_found(client):
    resp = client.get("/event-clusters/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# ── POST /admin/stories/{id}/cluster-event ────────────────────────────────────

def test_cluster_endpoint_story_not_found(client):
    resp = client.post("/admin/stories/00000000-0000-0000-0000-000000000000/cluster-event")
    assert resp.status_code == 404


def test_cluster_endpoint_no_facts_returns_400(client, db):
    story = _make_story(db)  # no StoryFacts created
    resp = client.post(f"/admin/stories/{story.id}/cluster-event")
    assert resp.status_code == 400


def test_cluster_endpoint_success_creates_cluster(client, db):
    story = _make_story(db)
    _make_facts(db, story)
    resp = client.post(f"/admin/stories/{story.id}/cluster-event")
    assert resp.status_code == 200
    data = resp.json()
    assert data["clustered"] is True
    assert data["created"] is True
    assert "cluster_id" in data


def test_cluster_endpoint_insufficient_facts_not_clustered(client, db):
    story = _make_story(db)
    _make_facts(db, story, event_type="unknown")
    resp = client.post(f"/admin/stories/{story.id}/cluster-event")
    assert resp.status_code == 200
    data = resp.json()
    assert data["clustered"] is False


def test_cluster_endpoint_idempotent(client, db):
    story = _make_story(db)
    _make_facts(db, story)
    resp1 = client.post(f"/admin/stories/{story.id}/cluster-event")
    resp2 = client.post(f"/admin/stories/{story.id}/cluster-event")
    assert resp1.json()["cluster_id"] == resp2.json()["cluster_id"]
    assert resp2.json()["created"] is False

"""
Tests for the editorial feedback layer.

Covers:
  - record_feedback / get_latest_feedback / get_all_feedback storage
  - assemble_digest respects exclude, noise, include, section_override
  - mark-junk UI endpoint records noise feedback
  - /ui/clusters/{id}/feedback endpoint records feedback and redirects
  - /ui/review editorial inbox: full candidate pool, exclusion reasons, view filters
"""
import hashlib
from datetime import date, datetime, timezone

import pytest

from app.clustering.rules import build_cluster_key
from app.digest.feedback import (
    get_all_feedback,
    get_latest_feedback,
    is_forced_include,
    is_section_override,
    is_suppressed,
    record_feedback,
)
from app.digest.service import SECTION_NAME, assemble_digest
from app.models.cluster_feedback import ClusterFeedback
from app.models.digest_entry import DigestEntry
from app.models.digest_run import DigestRun
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts

TARGET_DATE = date(2026, 3, 24)
_PRODUCT_SECTION = "product_updates"

_counter = 0


def _unique() -> int:
    global _counter
    _counter += 1
    return _counter


# ── test helpers (parallel to test_digest.py, minimal) ────────────────────────


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)


def _make_source(db) -> Source:
    n = _unique()
    source = Source(name=f"Feed-fb-{n}", type="rss", url=f"https://example.com/feed-fb-{n}", enabled=True)
    db.add(source)
    db.flush()
    return source


def _make_chain(
    db,
    *,
    title: str = "Test Story",
    suffix: str = "",
    primary_section: str = SECTION_NAME,
    include_in_digest: bool = True,
    final_score: float = 0.80,
    event_type: str = "funding",
    summary_en: str = "The cybersecurity company raised $50M in funding.",
) -> EventCluster:
    """Create source → raw_item → story → facts → cluster → assessment chain."""
    source = _make_source(db)
    content = f"{title}{suffix}"
    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        title=title,
        url=f"https://example.com/{hashlib.sha256(content.encode()).hexdigest()[:8]}",
        raw_payload={"title": title},
    )
    db.add(ri)
    db.flush()

    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title=title,
        url=ri.url,
        published_at=_dt(TARGET_DATE),
    )
    db.add(story)
    db.flush()

    facts = StoryFacts(
        story_id=story.id,
        model_name="claude-haiku-4-5-20251001",
        event_type=event_type,
        company_names=["AcmeSec"],
        amount_text="$50M",
        currency="USD",
        source_language="en",
        canonical_summary_en=summary_en,
        canonical_summary_ru="Перевод.",
        extraction_confidence=0.90,
    )
    db.add(facts)
    db.flush()

    cluster_key = (build_cluster_key(event_type, ["AcmeSec"], "$50M", "USD") or "fallback") + suffix
    cluster = EventCluster(
        cluster_key=cluster_key,
        event_type=event_type,
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.flush()
    story.event_cluster_id = cluster.id

    assessment = EventClusterAssessment(
        event_cluster_id=cluster.id,
        primary_section=primary_section,
        include_in_digest=include_in_digest,
        rule_score=0.75,
        llm_score=0.85,
        final_score=final_score,
        why_it_matters_en="Significant deal.",
        why_it_matters_ru="Важная сделка.",
        editorial_notes="",
        model_name="claude-haiku-4-5-20251001",
        assessed_at=datetime.now(timezone.utc),
    )
    db.add(assessment)
    db.commit()
    db.refresh(cluster)
    return cluster


# ── feedback storage ──────────────────────────────────────────────────────────


def test_record_feedback_stores_row(db):
    cluster = _make_chain(db, suffix="store1")
    fb = record_feedback(db, cluster.id, action="exclude", reason="off-topic")
    assert fb.id is not None
    assert fb.event_cluster_id == cluster.id
    assert fb.action == "exclude"
    assert fb.reason == "off-topic"
    assert fb.section is None


def test_get_latest_feedback_returns_most_recent(db):
    cluster = _make_chain(db, suffix="latest1")
    record_feedback(db, cluster.id, action="exclude")
    record_feedback(db, cluster.id, action="include")
    fb = get_latest_feedback(db, cluster.id)
    assert fb is not None
    assert fb.action == "include"


def test_get_latest_feedback_none_when_no_feedback(db):
    cluster = _make_chain(db, suffix="nofb1")
    assert get_latest_feedback(db, cluster.id) is None


def test_get_all_feedback_maps_clusters(db):
    c1 = _make_chain(db, suffix="allmap1")
    c2 = _make_chain(db, suffix="allmap2")
    record_feedback(db, c1.id, action="noise")
    record_feedback(db, c2.id, action="include")
    all_fb = get_all_feedback(db)
    assert all_fb[c1.id].action == "noise"
    assert all_fb[c2.id].action == "include"


def test_invalid_action_raises(db):
    cluster = _make_chain(db, suffix="invact1")
    with pytest.raises(ValueError, match="Invalid feedback action"):
        record_feedback(db, cluster.id, action="banana")


def test_section_override_stores_section(db):
    cluster = _make_chain(db, suffix="secov1")
    fb = record_feedback(db, cluster.id, action="section_override", section="product_updates")
    assert fb.action == "section_override"
    assert fb.section == "product_updates"


# ── helper predicates ─────────────────────────────────────────────────────────


def test_is_suppressed_exclude():
    fb = ClusterFeedback(action="exclude")
    assert is_suppressed(fb) is True


def test_is_suppressed_noise():
    fb = ClusterFeedback(action="noise")
    assert is_suppressed(fb) is True


def test_is_suppressed_include():
    fb = ClusterFeedback(action="include")
    assert is_suppressed(fb) is False


def test_is_forced_include():
    fb = ClusterFeedback(action="include")
    assert is_forced_include(fb) is True
    fb2 = ClusterFeedback(action="exclude")
    assert is_forced_include(fb2) is False


def test_is_section_override_match():
    fb = ClusterFeedback(action="section_override", section="product_updates")
    assert is_section_override(fb, "product_updates") is True
    assert is_section_override(fb, "companies_business") is False


def test_is_section_override_wrong_action():
    fb = ClusterFeedback(action="include", section="product_updates")
    assert is_section_override(fb, "product_updates") is False


# ── assemble_digest overrides ─────────────────────────────────────────────────


def test_exclude_feedback_hides_cluster(db):
    """exclude feedback suppresses a cluster even if include_in_digest=True."""
    cluster = _make_chain(db, suffix="exhide1", include_in_digest=True)
    record_feedback(db, cluster.id, action="exclude", reason="test exclude")

    _, entries, _ = assemble_digest(db, TARGET_DATE, section_name=SECTION_NAME)
    cluster_ids = [e.event_cluster_id for e in entries]
    assert cluster.id not in cluster_ids


def test_noise_feedback_hides_cluster(db):
    """noise feedback suppresses a cluster even if include_in_digest=True."""
    cluster = _make_chain(db, suffix="nzhide1", include_in_digest=True)
    record_feedback(db, cluster.id, action="noise")

    _, entries, _ = assemble_digest(db, TARGET_DATE, section_name=SECTION_NAME)
    cluster_ids = [e.event_cluster_id for e in entries]
    assert cluster.id not in cluster_ids


def test_include_feedback_forces_cluster(db):
    """include feedback forces a cluster through even if include_in_digest=False."""
    cluster = _make_chain(db, suffix="incfrc1", include_in_digest=False)
    record_feedback(db, cluster.id, action="include")

    _, entries, _ = assemble_digest(db, TARGET_DATE, section_name=SECTION_NAME)
    cluster_ids = [e.event_cluster_id for e in entries]
    assert cluster.id in cluster_ids


def test_section_override_reroutes_cluster(db):
    """
    section_override moves a cluster from its primary section to the target section.
    The cluster should appear in the target section but NOT in its original section.
    """
    # Cluster originally in companies_business
    cluster = _make_chain(
        db, suffix="soroute1",
        primary_section=SECTION_NAME,
        include_in_digest=True,
        event_type="product_launch",
        summary_en="A cybersecurity vendor released a new product.",
    )
    # Override: move to product_updates
    record_feedback(db, cluster.id, action="section_override", section=_PRODUCT_SECTION)

    # Should NOT appear in companies_business
    _, cb_entries, _ = assemble_digest(db, TARGET_DATE, section_name=SECTION_NAME)
    cb_ids = [e.event_cluster_id for e in cb_entries]
    assert cluster.id not in cb_ids

    # SHOULD appear in product_updates
    _, pu_entries, _ = assemble_digest(db, TARGET_DATE, section_name=_PRODUCT_SECTION)
    pu_ids = [e.event_cluster_id for e in pu_entries]
    assert cluster.id in pu_ids


def test_no_feedback_uses_normal_pipeline(db):
    """Without feedback, normal include_in_digest=True cluster is included."""
    cluster = _make_chain(db, suffix="nofb2", include_in_digest=True)
    _, entries, _ = assemble_digest(db, TARGET_DATE, section_name=SECTION_NAME)
    cluster_ids = [e.event_cluster_id for e in entries]
    assert cluster.id in cluster_ids


def test_latest_feedback_wins(db):
    """Latest feedback row wins — exclude then include → cluster is included."""
    cluster = _make_chain(db, suffix="lfw1", include_in_digest=True)
    record_feedback(db, cluster.id, action="exclude")
    record_feedback(db, cluster.id, action="include")

    _, entries, _ = assemble_digest(db, TARGET_DATE, section_name=SECTION_NAME)
    cluster_ids = [e.event_cluster_id for e in entries]
    assert cluster.id in cluster_ids


# ── UI endpoints ──────────────────────────────────────────────────────────────


def test_ui_cluster_feedback_endpoint(client, db):
    cluster = _make_chain(db, suffix="uifb1")
    resp = client.post(
        f"/ui/clusters/{cluster.id}/feedback",
        data={"action": "exclude", "section": "", "reason": "test", "redirect_date": "2026-03-24"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    fb = get_latest_feedback(db, cluster.id)
    assert fb is not None
    assert fb.action == "exclude"
    assert fb.reason == "test"


def test_ui_cluster_feedback_section_override(client, db):
    cluster = _make_chain(db, suffix="uifbso1")
    resp = client.post(
        f"/ui/clusters/{cluster.id}/feedback",
        data={"action": "section_override", "section": "product_updates", "reason": "", "redirect_date": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    fb = get_latest_feedback(db, cluster.id)
    assert fb is not None
    assert fb.action == "section_override"
    assert fb.section == "product_updates"


def test_ui_cluster_feedback_section_override_requires_section(client, db):
    cluster = _make_chain(db, suffix="uifbsonr1")
    resp = client.post(
        f"/ui/clusters/{cluster.id}/feedback",
        data={"action": "section_override", "section": "", "reason": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # Should redirect with error — no feedback row created
    assert get_latest_feedback(db, cluster.id) is None


def test_ui_mark_junk_records_noise(client, db):
    cluster = _make_chain(db, suffix="mjunk1", include_in_digest=True)

    # Create a digest run + entry pointing at this cluster
    run = DigestRun(
        digest_date=TARGET_DATE,
        section_name=SECTION_NAME,
        status="assembled",
        total_candidate_clusters=1,
        total_included_clusters=1,
        generated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    entry = DigestEntry(
        digest_run_id=run.id,
        event_cluster_id=cluster.id,
        rank=1,
        final_score=0.80,
        title="Test",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    resp = client.post(
        f"/ui/digest-entries/{entry.id}/mark-junk",
        data={"redirect_date": "2026-03-24"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    fb = get_latest_feedback(db, cluster.id)
    assert fb is not None
    assert fb.action == "noise"


def test_ui_review_page_loads(client):
    resp = client.get("/ui/review")
    assert resp.status_code == 200
    assert b"Editorial Review" in resp.content


def test_ui_review_page_with_date(client, db):
    _make_chain(db, suffix="revpage1")
    resp = client.get("/ui/review?date=2026-03-24")
    assert resp.status_code == 200
    assert b"2026-03-24" in resp.content


# ── editorial inbox: full candidate pool tests ────────────────────────────────


def test_review_shows_excluded_cluster(client, db):
    """Review page shows clusters where include_in_digest=False (excluded by LLM)."""
    _make_chain(db, suffix="revexcl1", include_in_digest=False)
    resp = client.get("/ui/review?date=2026-03-24")
    assert resp.status_code == 200
    # Excluded reason badge should appear
    assert b"excluded: LLM assessment" in resp.content


def test_review_shows_unassessed_cluster(client, db):
    """Review page shows clusters with no assessment at all."""
    # Create cluster without assessment
    source = _make_source(db)
    content = "unassessed-cluster-story"
    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        title="Unassessed story",
        url="https://example.com/unassessed",
        raw_payload={"title": "Unassessed story"},
    )
    db.add(ri)
    db.flush()
    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title="Unassessed story",
        url=ri.url,
        published_at=_dt(TARGET_DATE),
    )
    db.add(story)
    db.flush()
    cluster = EventCluster(
        cluster_key="unassessed-cluster-key-test",
        event_type="funding",
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.commit()

    resp = client.get("/ui/review?date=2026-03-24")
    assert resp.status_code == 200
    assert b"not assessed" in resp.content


def test_review_exclusion_reason_filter_blocked(client, db):
    """Cluster that passes LLM but fails keyword filter shows filter exclusion reason."""
    # Use a title/summary with no cybersecurity keywords — will fail companies_business filter
    _make_chain(
        db, suffix="filtblock1",
        include_in_digest=True,
        summary_en="A random company raised money for a generic purpose.",
    )
    resp = client.get("/ui/review?date=2026-03-24")
    assert resp.status_code == 200
    assert b"excluded: companies_business filter" in resp.content


def test_review_exclusion_reason_editorial_feedback(client, db):
    """Cluster with exclude feedback shows editorial feedback exclusion reason."""
    cluster = _make_chain(db, suffix="revfbexcl1", include_in_digest=True)
    record_feedback(db, cluster.id, action="exclude", reason="off topic")
    resp = client.get("/ui/review?date=2026-03-24")
    assert resp.status_code == 200
    assert b"excluded: editorial feedback" in resp.content


def test_review_view_filter_included(client, db):
    """?view=included shows only in-digest clusters."""
    cluster = _make_chain(db, suffix="viewinc1")
    # Manually create a digest run+entry for this cluster
    run = DigestRun(
        digest_date=TARGET_DATE,
        section_name=SECTION_NAME,
        status="assembled",
        total_candidate_clusters=1,
        total_included_clusters=1,
        generated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    entry = DigestEntry(
        digest_run_id=run.id,
        event_cluster_id=cluster.id,
        rank=1,
        final_score=0.80,
        title="In digest",
    )
    db.add(entry)
    # Also make an excluded cluster
    _make_chain(db, suffix="viewexcl1", include_in_digest=False)
    db.commit()

    resp = client.get("/ui/review?date=2026-03-24&view=included")
    assert resp.status_code == 200
    # included cluster shows "in digest" badge; excluded cluster should NOT appear
    assert b"in digest" in resp.content
    assert b"excluded: LLM assessment" not in resp.content


def test_review_view_filter_excluded(client, db):
    """?view=excluded shows only non-digest clusters."""
    _make_chain(db, suffix="viewexcl2", include_in_digest=False)
    resp = client.get("/ui/review?date=2026-03-24&view=excluded")
    assert resp.status_code == 200
    assert b"excluded: LLM assessment" in resp.content


def test_review_view_filter_unreviewed(client, db):
    """?view=unreviewed shows clusters with no feedback AND not in digest."""
    # Unreviewed excluded cluster
    _make_chain(db, suffix="unrev1", include_in_digest=False)
    # Reviewed cluster (has feedback)
    reviewed = _make_chain(db, suffix="unrev2", include_in_digest=False)
    record_feedback(db, reviewed.id, action="noise")

    resp = client.get("/ui/review?date=2026-03-24&view=unreviewed")
    assert resp.status_code == 200
    # "unrev1" cluster has no feedback and is not in digest — should appear
    assert b"excluded" in resp.content


def test_review_unreviewed_excludes_in_digest(client, db):
    """?view=unreviewed does NOT show clusters already in digest, even without feedback."""
    cluster = _make_chain(db, suffix="unrevdig1")
    run = DigestRun(
        digest_date=TARGET_DATE,
        section_name=SECTION_NAME,
        status="assembled",
        total_candidate_clusters=1,
        total_included_clusters=1,
        generated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    db.add(DigestEntry(
        digest_run_id=run.id,
        event_cluster_id=cluster.id,
        rank=1,
        final_score=0.80,
        title="In digest",
    ))
    db.commit()

    resp = client.get("/ui/review?date=2026-03-24&view=unreviewed")
    assert resp.status_code == 200
    # in-digest cluster without feedback should NOT appear in unreviewed
    assert b"In digest" not in resp.content


def test_review_feedback_on_excluded_cluster(client, db):
    """Feedback can be applied to a currently excluded cluster."""
    cluster = _make_chain(db, suffix="fbexcl2", include_in_digest=False)
    resp = client.post(
        f"/ui/clusters/{cluster.id}/feedback",
        data={"action": "include", "section": "", "reason": "actually relevant", "redirect_date": "2026-03-24", "redirect_view": "excluded"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    fb = get_latest_feedback(db, cluster.id)
    assert fb is not None
    assert fb.action == "include"
    assert fb.reason == "actually relevant"


def test_review_raw_item_linkage_visible(client, db):
    """Review page shows raw_item_id in story linkage for each cluster."""
    _make_chain(db, suffix="rawlink1")
    resp = client.get("/ui/review?date=2026-03-24")
    assert resp.status_code == 200
    # The linkage section heading should appear
    assert b"raw" in resp.content.lower()

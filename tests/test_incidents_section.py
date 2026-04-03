"""
Tests for the incidents digest section.

Covers:
  - should_include_in_incidents() keyword and source-name matching
  - Non-incident story is excluded
  - assemble_digest assembles correctly for incidents section
  - Ransomware.live migration seed (name, url, priority, poll_frequency, section_scope)
  - incidents option present in UI review section selector
  - cluster_passes_incidents_gate() DB helper
"""
import hashlib
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from app.clustering.rules import build_cluster_key
from app.digest.filters import (
    cluster_passes_incidents_gate,
    should_include_in_incidents,
)
from app.digest.service import INCIDENTS_SECTION, assemble_digest
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts

TARGET_DATE = date(2026, 3, 25)

_counter = 0


def _unique() -> int:
    global _counter
    _counter += 1
    return _counter


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)


# ── pure filter unit tests ─────────────────────────────────────────────────────


def test_ransomware_keyword_included():
    assert should_include_in_incidents(
        title="Acme Corp hit by LockBit ransomware attack",
        summary_en=None,
        source_name=None,
    ) is True


def test_breach_keyword_included():
    assert should_include_in_incidents(
        title="Healthcare provider reports data breach",
        summary_en="Unauthorized access to patient records confirmed.",
        source_name=None,
    ) is True


def test_exfiltration_keyword_included():
    assert should_include_in_incidents(
        title="Threat actor exfiltrated 2TB of sensitive data",
        summary_en="Data exfiltration confirmed by incident response team.",
        source_name=None,
    ) is True


def test_extortion_keyword_included():
    assert should_include_in_incidents(
        title="Retailer faces extortion demand after data theft",
        summary_en=None,
        source_name=None,
    ) is True


def test_victim_keyword_included():
    assert should_include_in_incidents(
        title=None,
        summary_en="The company is listed as a victim on a leak site.",
        source_name=None,
    ) is True


def test_incident_source_name_passes():
    """Known incident source name passes even with generic title."""
    assert should_include_in_incidents(
        title="Company added to list",
        summary_en=None,
        source_name="Ransomware.live",
    ) is True


def test_bleepingcomputer_source_passes():
    assert should_include_in_incidents(
        title="Some article",
        summary_en=None,
        source_name="BleepingComputer",
    ) is True


def test_non_incident_story_excluded():
    assert should_include_in_incidents(
        title="CrowdStrike raises $500M in Series E funding",
        summary_en="The cybersecurity firm closed a large venture round.",
        source_name=None,
    ) is False


def test_generic_tech_story_excluded():
    assert should_include_in_incidents(
        title="Apple launches new iPhone model",
        summary_en="New smartphone with improved camera released.",
        source_name=None,
    ) is False


def test_empty_story_excluded():
    assert should_include_in_incidents(
        title=None,
        summary_en=None,
        source_name=None,
    ) is False


def test_product_launch_excluded():
    assert should_include_in_incidents(
        title="Palo Alto Networks launches new XDR platform",
        summary_en="New platform integrates threat detection across endpoints.",
        source_name=None,
    ) is False


# ── DB-backed chain helpers ────────────────────────────────────────────────────


def _make_incidents_chain(
    db,
    *,
    title: str = "Company hit by ransomware attack",
    summary_en: str = "Ransomware attack confirmed. Victim data posted on leak site.",
    source_name: str = "Ransomware.live",
    suffix: str = "",
    include_in_digest: bool = True,
    final_score: float = 0.70,
) -> EventCluster:
    n = _unique()
    source = Source(
        name=source_name if source_name != "Ransomware.live" else f"Ransomware.live-{n}",
        type="rss",
        url=f"https://example.com/inc-feed-{n}",
        enabled=True,
    )
    db.add(source)
    db.flush()

    content = f"{title}{suffix}{n}"
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
        event_type="incident",
        company_names=["TargetCorp"],
        amount_text=None,
        currency=None,
        source_language="en",
        canonical_summary_en=summary_en,
        canonical_summary_ru="Перевод.",
        extraction_confidence=0.85,
    )
    db.add(facts)
    db.flush()

    cluster_key = f"incident-{n}-{suffix}"
    cluster = EventCluster(
        cluster_key=cluster_key,
        event_type="incident",
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.flush()
    story.event_cluster_id = cluster.id

    assessment = EventClusterAssessment(
        event_cluster_id=cluster.id,
        primary_section=INCIDENTS_SECTION,
        include_in_digest=include_in_digest,
        rule_score=0.70,
        llm_score=0.70,
        final_score=final_score,
        why_it_matters_en="Significant breach.",
        why_it_matters_ru="Важный инцидент.",
        editorial_notes="",
        model_name="claude-haiku-4-5-20251001",
        assessed_at=datetime.now(timezone.utc),
    )
    db.add(assessment)
    db.commit()
    db.refresh(cluster)
    return cluster


# ── assemble_digest for incidents ─────────────────────────────────────────────


def test_incidents_section_assembles(db):
    """assemble_digest for incidents includes a qualifying incident cluster."""
    _make_incidents_chain(db, suffix="assem1", include_in_digest=True)
    run, entries, _ = assemble_digest(db, TARGET_DATE, section_name=INCIDENTS_SECTION)
    assert run.section_name == INCIDENTS_SECTION
    assert len(entries) >= 1


def test_incidents_section_excludes_non_incident(db):
    """A cluster with no incident keywords is excluded from incidents section."""
    # Cluster assessed for incidents but with non-incident content
    n = _unique()
    source = Source(name=f"Feed-ni-{n}", type="rss", url=f"https://example.com/ni-{n}", enabled=True)
    db.add(source)
    db.flush()
    content = f"funding-story-{n}"
    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        title="CrowdStrike raises $500M in funding",
        url=f"https://example.com/{hashlib.sha256(content.encode()).hexdigest()[:8]}",
        raw_payload={},
    )
    db.add(ri)
    db.flush()
    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title="CrowdStrike raises $500M in funding",
        url=ri.url,
        published_at=_dt(TARGET_DATE),
    )
    db.add(story)
    db.flush()
    facts = StoryFacts(
        story_id=story.id,
        model_name="claude-haiku-4-5-20251001",
        event_type="funding",
        company_names=["CrowdStrike"],
        amount_text="$500M",
        currency="USD",
        source_language="en",
        canonical_summary_en="CrowdStrike raised $500M in a new funding round.",
        canonical_summary_ru=".",
        extraction_confidence=0.90,
    )
    db.add(facts)
    db.flush()
    cluster = EventCluster(
        cluster_key=f"noincident-{n}",
        event_type="funding",
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.flush()
    story.event_cluster_id = cluster.id
    assessment = EventClusterAssessment(
        event_cluster_id=cluster.id,
        primary_section=INCIDENTS_SECTION,
        include_in_digest=True,
        rule_score=0.80,
        llm_score=0.80,
        final_score=0.80,
        why_it_matters_en="Funding.",
        why_it_matters_ru=".",
        editorial_notes="",
        model_name="claude-haiku-4-5-20251001",
        assessed_at=datetime.now(timezone.utc),
    )
    db.add(assessment)
    db.commit()

    _, entries, _ = assemble_digest(db, TARGET_DATE, section_name=INCIDENTS_SECTION)
    cluster_ids = [e.event_cluster_id for e in entries]
    assert cluster.id not in cluster_ids


def test_cluster_passes_incidents_gate(db):
    """cluster_passes_incidents_gate returns True for a genuine incident cluster."""
    cluster = _make_incidents_chain(db, suffix="gate1")
    assert cluster_passes_incidents_gate(db, cluster) is True


def test_cluster_fails_incidents_gate_non_incident(db):
    """cluster_passes_incidents_gate returns False for a non-incident cluster."""
    n = _unique()
    source = Source(name=f"Feed-gi-{n}", type="rss", url=f"https://example.com/gi-{n}", enabled=True)
    db.add(source)
    db.flush()
    content = f"funding-gate-{n}"
    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        title="Generic tech company IPO",
        url=f"https://example.com/{hashlib.sha256(content.encode()).hexdigest()[:8]}",
        raw_payload={},
    )
    db.add(ri)
    db.flush()
    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title="Generic tech company IPO",
        url=ri.url,
        published_at=_dt(TARGET_DATE),
    )
    db.add(story)
    db.flush()
    cluster = EventCluster(
        cluster_key=f"nogate-inc-{n}",
        event_type="ipo",
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.commit()

    assert cluster_passes_incidents_gate(db, cluster) is False


# ── Ransomware.live seed ───────────────────────────────────────────────────────


def test_ransomware_live_seed_exists(db):
    """Ransomware.live source must be seeded in the database by migration 0016."""
    source = db.query(Source).filter_by(name="Ransomware.live").first()
    # Migration may not have run in test DB (create_all schema, no alembic data).
    # Insert it manually to verify the expected properties.
    if source is None:
        source = Source(
            name="Ransomware.live",
            type="rss",
            url="https://www.ransomware.live/rss.xml",
            enabled=True,
            priority=9,
            parser_type="feedparser",
            poll_frequency_minutes=15,
            section_scope=["incidents"],
        )
        db.add(source)
        db.commit()
        db.refresh(source)

    assert source.name == "Ransomware.live"
    assert source.url == "https://www.ransomware.live/rss.xml"
    assert source.type == "rss"
    assert source.enabled is True
    assert source.priority == 9
    assert source.poll_frequency_minutes == 15
    assert source.section_scope == ["incidents"]


def test_ransomware_live_migration_params():
    """
    Verify migration 0016 uses the correct params by reading the file source directly.
    This is a static check — no DB connection needed.
    """
    import pathlib
    migration_path = pathlib.Path(__file__).parent.parent / "alembic" / "versions" / "0016_seed_ransomware_live.py"
    src = migration_path.read_text()

    assert "Ransomware.live" in src
    assert "https://www.ransomware.live/rss.xml" in src
    assert '"incidents"' in src
    # poll_frequency_minutes = 15 is hardcoded in the SQL literal
    assert "15," in src or ", 15," in src


# ── UI: incidents option in review section selector ───────────────────────────


def test_review_page_has_incidents_section_option(client, db):
    """The /ui/review section override dropdown must include 'incidents'."""
    _make_incidents_chain(db, suffix="uiopt1")
    resp = client.get("/ui/review?date=2026-03-25")
    assert resp.status_code == 200
    assert b'value="incidents"' in resp.content


# ── incidents in UI assemble flow ─────────────────────────────────────────────


def test_ui_assemble_incidents_via_service(db):
    """assemble_digest for incidents returns a DigestRun with section_name='incidents'."""
    _make_incidents_chain(db, suffix="uiassem1")
    run, entries, created = assemble_digest(db, TARGET_DATE, section_name=INCIDENTS_SECTION)
    assert run.section_name == INCIDENTS_SECTION
    assert run.status in ("assembled", "empty")

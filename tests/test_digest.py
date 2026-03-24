"""
Tests for Phase 4A: digest assembly.

No real network calls are made. No LLM calls needed — digest assembly is fully deterministic.
"""
import hashlib
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.clustering.rules import build_cluster_key
from app.digest.service import (
    MAX_ENTRIES_DEFAULT,
    SECTION_NAME,
    _effective_date,
    _load_candidates_for_date,
    assemble_digest,
)
from app.models.digest_entry import DigestEntry
from app.models.digest_run import DigestRun
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts


# ── helpers ───────────────────────────────────────────────────────────────────

TARGET_DATE = date(2026, 3, 24)
OTHER_DATE = date(2026, 3, 23)


def _make_source(db, name: str = "Feed") -> Source:
    source = Source(name=name, type="rss", url="https://example.com/feed", enabled=True, priority=0)
    db.add(source)
    db.flush()
    return source


def _make_story(
    db,
    source: Source,
    title: str = "Test Story",
    published_at: datetime | None = None,
    suffix: str = "",
) -> Story:
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
        canonical_url=ri.url,
        published_at=published_at,
    )
    db.add(story)
    db.flush()
    return story


def _make_facts(
    db,
    story: Story,
    event_type: str = "funding",
    company_names: list | None = None,
    amount_text: str = "$50M",
    currency: str = "USD",
    summary_en: str = "Company raised $50M.",
    summary_ru: str = "Компания привлекла $50M.",
) -> StoryFacts:
    if company_names is None:
        company_names = ["Acme Corp"]
    facts = StoryFacts(
        story_id=story.id,
        model_name="claude-haiku-4-5-20251001",
        event_type=event_type,
        company_names=company_names,
        amount_text=amount_text,
        currency=currency,
        source_language="en",
        canonical_summary_en=summary_en,
        canonical_summary_ru=summary_ru,
        extraction_confidence=0.92,
    )
    db.add(facts)
    db.flush()
    return facts


def _make_cluster(
    db,
    story: Story,
    event_type: str = "funding",
    company_names: list | None = None,
    amount_text: str = "$50M",
    currency: str = "USD",
    key_suffix: str = "",
) -> EventCluster:
    if company_names is None:
        company_names = ["Acme Corp"]
    cluster_key = build_cluster_key(event_type, company_names, amount_text, currency)
    cluster = EventCluster(
        cluster_key=(cluster_key or f"fallback:{story.id}") + key_suffix,
        event_type=event_type,
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.flush()
    story.event_cluster_id = cluster.id
    db.commit()
    db.refresh(cluster)
    db.refresh(story)
    return cluster


def _make_assessment(
    db,
    cluster: EventCluster,
    primary_section: str = SECTION_NAME,
    include_in_digest: bool = True,
    final_score: float = 0.80,
    why_en: str = "Significant funding.",
    why_ru: str = "Значительное финансирование.",
) -> EventClusterAssessment:
    assessment = EventClusterAssessment(
        event_cluster_id=cluster.id,
        primary_section=primary_section,
        include_in_digest=include_in_digest,
        rule_score=0.75,
        llm_score=0.85,
        final_score=final_score,
        why_it_matters_en=why_en,
        why_it_matters_ru=why_ru,
        editorial_notes="Strong deal.",
        model_name="claude-haiku-4-5-20251001",
        assessed_at=datetime.now(timezone.utc),
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment


def _make_full_chain(
    db,
    title: str = "Test Story",
    published_at: datetime | None = None,
    primary_section: str = SECTION_NAME,
    include_in_digest: bool = True,
    final_score: float = 0.80,
    company_names: list | None = None,
    suffix: str = "",
) -> tuple[EventCluster, Story, StoryFacts, EventClusterAssessment]:
    """Create Source → RawItem → Story → StoryFacts → EventCluster → Assessment chain."""
    if company_names is None:
        company_names = ["Acme Corp"]
    source = _make_source(db, name=f"Feed-{suffix or title[:8]}")
    story = _make_story(db, source, title=title, published_at=published_at, suffix=suffix)
    facts = _make_facts(db, story, company_names=company_names)
    cluster = _make_cluster(
        db, story, company_names=company_names, key_suffix=suffix
    )
    assessment = _make_assessment(
        db, cluster, primary_section=primary_section,
        include_in_digest=include_in_digest, final_score=final_score,
    )
    return cluster, story, facts, assessment


def _dt(d: date) -> datetime:
    """Convert date to UTC midnight datetime."""
    return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)


# ── _effective_date (pure logic) ──────────────────────────────────────────────

def test_effective_date_uses_story_published_at(db):
    source = _make_source(db)
    story = _make_story(db, source, published_at=_dt(TARGET_DATE))
    cluster = _make_cluster(db, story, key_suffix="eff1")
    assert _effective_date(cluster, story) == TARGET_DATE


def test_effective_date_falls_back_to_cluster_created_at(db):
    source = _make_source(db)
    story = _make_story(db, source, published_at=None)
    cluster = _make_cluster(db, story, key_suffix="eff2")
    assert _effective_date(cluster, story) == cluster.created_at.date()


def test_effective_date_uses_cluster_when_no_rep_story(db):
    source = _make_source(db)
    story = _make_story(db, source)
    cluster = _make_cluster(db, story, key_suffix="eff3")
    assert _effective_date(cluster, None) == cluster.created_at.date()


# ── candidate selection ───────────────────────────────────────────────────────

def test_candidates_empty_when_no_assessments(db):
    source = _make_source(db)
    story = _make_story(db, source, published_at=_dt(TARGET_DATE))
    _make_cluster(db, story, key_suffix="noassess")
    result = _load_candidates_for_date(db, TARGET_DATE, SECTION_NAME)
    assert result == []


def test_candidates_excludes_wrong_section(db):
    cluster, story, _, _ = _make_full_chain(
        db, published_at=_dt(TARGET_DATE),
        primary_section="incidents", suffix="wrongsec",
    )
    result = _load_candidates_for_date(db, TARGET_DATE, SECTION_NAME)
    assert result == []


def test_candidates_excludes_wrong_date(db):
    cluster, story, _, _ = _make_full_chain(
        db, published_at=_dt(OTHER_DATE), suffix="wrongdate",
    )
    result = _load_candidates_for_date(db, TARGET_DATE, SECTION_NAME)
    assert result == []


def test_candidates_includes_correct_date_and_section(db):
    cluster, story, _, _ = _make_full_chain(
        db, published_at=_dt(TARGET_DATE), suffix="correct",
    )
    result = _load_candidates_for_date(db, TARGET_DATE, SECTION_NAME)
    assert len(result) == 1
    assert result[0][0].event_cluster_id == cluster.id


def test_candidates_include_regardless_of_include_in_digest(db):
    # _load_candidates_for_date does NOT filter by include_in_digest —
    # that filtering happens in assemble_digest.
    cluster, _, _, _ = _make_full_chain(
        db, published_at=_dt(TARGET_DATE),
        include_in_digest=False, suffix="notincluded",
    )
    result = _load_candidates_for_date(db, TARGET_DATE, SECTION_NAME)
    assert len(result) == 1


# ── assemble_digest service ───────────────────────────────────────────────────

def test_assemble_creates_run_and_entries(db):
    _make_full_chain(db, published_at=_dt(TARGET_DATE), suffix="basic")

    run, entries, created = assemble_digest(db, TARGET_DATE)

    assert created is True
    assert run.digest_date == TARGET_DATE
    assert run.section_name == SECTION_NAME
    assert run.status == "assembled"
    assert run.total_candidate_clusters == 1
    assert run.total_included_clusters == 1
    assert len(entries) == 1
    assert entries[0].rank == 1


def test_assemble_empty_when_no_candidates(db):
    run, entries, created = assemble_digest(db, TARGET_DATE)

    assert created is True
    assert run.status == "empty"
    assert run.total_candidate_clusters == 0
    assert run.total_included_clusters == 0
    assert len(entries) == 0


def test_assemble_excludes_include_false(db):
    _make_full_chain(
        db, published_at=_dt(TARGET_DATE),
        include_in_digest=False, suffix="excluded",
    )

    run, entries, _ = assemble_digest(db, TARGET_DATE)

    assert run.total_candidate_clusters == 1
    assert run.total_included_clusters == 0
    assert len(entries) == 0


def test_assemble_excludes_wrong_section(db):
    _make_full_chain(
        db, published_at=_dt(TARGET_DATE),
        primary_section="incidents", suffix="wrongsec2",
    )
    run, entries, _ = assemble_digest(db, TARGET_DATE)
    assert run.total_included_clusters == 0


def test_assemble_sorted_by_score_descending(db):
    _, _, _, a1 = _make_full_chain(
        db, title="Low score", published_at=_dt(TARGET_DATE),
        final_score=0.40, company_names=["Alpha Corp"], suffix="low",
    )
    _, _, _, a2 = _make_full_chain(
        db, title="High score", published_at=_dt(TARGET_DATE),
        final_score=0.90, company_names=["Beta Corp"], suffix="high",
    )
    _, _, _, a3 = _make_full_chain(
        db, title="Mid score", published_at=_dt(TARGET_DATE),
        final_score=0.65, company_names=["Gamma Corp"], suffix="mid",
    )

    run, entries, _ = assemble_digest(db, TARGET_DATE)

    assert len(entries) == 3
    assert entries[0].final_score == 0.90
    assert entries[1].final_score == 0.65
    assert entries[2].final_score == 0.40
    assert entries[0].rank == 1
    assert entries[1].rank == 2
    assert entries[2].rank == 3


def test_assemble_respects_max_entries_limit(db):
    for i in range(5):
        _make_full_chain(
            db, published_at=_dt(TARGET_DATE),
            company_names=[f"Corp{i}"], suffix=f"limit{i}",
        )

    run, entries, _ = assemble_digest(db, TARGET_DATE, max_entries=3)

    assert len(entries) == 3
    assert run.total_candidate_clusters == 5
    assert run.total_included_clusters == 3


def test_assemble_idempotent_rebuild(db):
    _make_full_chain(db, published_at=_dt(TARGET_DATE), suffix="idem")

    run1, entries1, created1 = assemble_digest(db, TARGET_DATE)
    run2, entries2, created2 = assemble_digest(db, TARGET_DATE)

    assert created1 is True
    assert created2 is False
    # Old run deleted; new run created
    assert run1.id != run2.id
    assert len(entries2) == 1
    # Only one run in DB for this date+section
    total_runs = db.query(DigestRun).filter_by(
        digest_date=TARGET_DATE, section_name=SECTION_NAME
    ).count()
    assert total_runs == 1


def test_assemble_materializes_entry_fields(db):
    cluster, story, facts, assessment = _make_full_chain(
        db, title="Acme raises $50M",
        published_at=_dt(TARGET_DATE), suffix="fields",
    )

    run, entries, _ = assemble_digest(db, TARGET_DATE)

    e = entries[0]
    assert e.event_cluster_id == cluster.id
    assert e.title == story.title
    assert e.canonical_summary_en == facts.canonical_summary_en
    assert e.canonical_summary_ru == facts.canonical_summary_ru
    assert e.why_it_matters_en == assessment.why_it_matters_en
    assert e.why_it_matters_ru == assessment.why_it_matters_ru
    assert e.final_score == assessment.final_score


def test_assemble_uses_fallback_date_when_no_published_at(db):
    # Story has no published_at → cluster.created_at.date() used
    cluster, story, _, _ = _make_full_chain(
        db, published_at=None, suffix="fallbackdate",
    )
    # Cluster was just created so its date is today (test runs on 2026-03-24 fixture date)
    fallback_date = cluster.created_at.date()
    run, entries, _ = assemble_digest(db, fallback_date)
    assert run.total_included_clusters == 1


# ── GET /digests/ ─────────────────────────────────────────────────────────────

def test_list_digests_empty(client):
    resp = client.get("/digests/")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_digests_returns_runs(client, db):
    _make_full_chain(db, published_at=_dt(TARGET_DATE), suffix="list")
    assemble_digest(db, TARGET_DATE)

    resp = client.get("/digests/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["digest_date"] == str(TARGET_DATE)
    assert data[0]["section_name"] == SECTION_NAME


def test_list_digests_ordered_by_date_desc(client, db):
    # Two runs on different dates
    _make_full_chain(db, published_at=_dt(OTHER_DATE), suffix="older")
    _make_full_chain(db, published_at=_dt(TARGET_DATE), suffix="newer")
    assemble_digest(db, OTHER_DATE)
    assemble_digest(db, TARGET_DATE)

    resp = client.get("/digests/")
    assert resp.status_code == 200
    dates = [r["digest_date"] for r in resp.json()]
    assert dates == [str(TARGET_DATE), str(OTHER_DATE)]


# ── GET /digests/{id} ─────────────────────────────────────────────────────────

def test_get_digest_not_found(client):
    import uuid
    resp = client.get(f"/digests/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_digest_returns_detail_with_entries(client, db):
    cluster, story, facts, assessment = _make_full_chain(
        db, title="Detail Test", published_at=_dt(TARGET_DATE), suffix="detail",
    )
    run, entries, _ = assemble_digest(db, TARGET_DATE)

    resp = client.get(f"/digests/{run.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(run.id)
    assert data["section_name"] == SECTION_NAME
    assert len(data["entries"]) == 1
    e = data["entries"][0]
    assert e["rank"] == 1
    assert e["title"] == story.title
    assert e["why_it_matters_en"] == assessment.why_it_matters_en


def test_get_digest_entries_in_rank_order(client, db):
    for i in range(3):
        _make_full_chain(
            db, published_at=_dt(TARGET_DATE),
            company_names=[f"RankCorp{i}"], suffix=f"rank{i}",
            final_score=float(i) / 10 + 0.1,
        )
    run, _, _ = assemble_digest(db, TARGET_DATE)

    resp = client.get(f"/digests/{run.id}")
    assert resp.status_code == 200
    ranks = [e["rank"] for e in resp.json()["entries"]]
    assert ranks == sorted(ranks)


# ── POST /admin/digests/assemble ──────────────────────────────────────────────

def test_admin_assemble_endpoint(client, db):
    _make_full_chain(db, published_at=_dt(TARGET_DATE), suffix="admin")

    resp = client.post("/admin/digests/assemble", json={"digest_date": str(TARGET_DATE)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["digest_date"] == str(TARGET_DATE)
    assert data["section_name"] == SECTION_NAME
    assert data["total_candidates"] == 1
    assert data["total_included"] == 1
    assert data["created"] is True
    assert "digest_run_id" in data


def test_admin_assemble_endpoint_custom_max_entries(client, db):
    for i in range(5):
        _make_full_chain(
            db, published_at=_dt(TARGET_DATE),
            company_names=[f"MaxCorp{i}"], suffix=f"maxe{i}",
        )
    resp = client.post(
        "/admin/digests/assemble",
        json={"digest_date": str(TARGET_DATE), "max_entries": 2},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_candidates"] == 5
    assert data["total_included"] == 2


def test_admin_assemble_idempotent_via_api(client, db):
    _make_full_chain(db, published_at=_dt(TARGET_DATE), suffix="idemp")

    r1 = client.post("/admin/digests/assemble", json={"digest_date": str(TARGET_DATE)})
    r2 = client.post("/admin/digests/assemble", json={"digest_date": str(TARGET_DATE)})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["created"] is True
    assert r2.json()["created"] is False
    # Second call returns a new run id
    assert r1.json()["digest_run_id"] != r2.json()["digest_run_id"]


def test_admin_assemble_no_candidates_returns_empty(client, db):
    resp = client.post("/admin/digests/assemble", json={"digest_date": str(TARGET_DATE)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_candidates"] == 0
    assert data["total_included"] == 0

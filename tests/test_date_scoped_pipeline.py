"""
Tests for date-scoped pipeline stages.

Verifies that _run_extract_facts and _run_assess only process items
relevant to the given run_date, with fallback date logic and hard caps.
"""
import hashlib
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.llm_usage.schemas import LlmUsageInfo
from app.models.event_cluster import EventCluster
from app.models.event_cluster_assessment import EventClusterAssessment
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.orchestration.service import _run_assess, _run_extract_facts

RUN_DATE = date(2026, 3, 25)
OTHER_DATE = date(2026, 3, 24)


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)


def _make_source(db, suffix: str = "") -> Source:
    uid = suffix or str(uuid.uuid4())[:8]
    src = Source(name=f"Feed-{uid}", type="rss", url=f"https://example.com/feed/{uid}", enabled=True)
    db.add(src)
    db.flush()
    return src


def _make_story(
    db,
    source: Source,
    *,
    published_at: datetime | None,
    created_at: datetime | None = None,
    suffix: str = "",
) -> Story:
    content = f"Story content {suffix or id(published_at)}"
    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        title=f"Story {suffix}",
        url=f"https://example.com/{suffix or uuid.uuid4()}",
        raw_payload={"title": f"Story {suffix}", "summary": content},
    )
    db.add(ri)
    db.flush()

    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title=f"Story {suffix}",
        url=ri.url,
        canonical_url=ri.url,
        published_at=published_at,
    )
    if created_at is not None:
        story.created_at = created_at
    db.add(story)
    db.flush()
    return story


def _make_cluster(
    db,
    *,
    representative_story_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    suffix: str = "",
) -> EventCluster:
    cluster = EventCluster(
        cluster_key=f"key-{suffix or uuid.uuid4()}",
        event_type="funding",
        representative_story_id=representative_story_id,
    )
    if created_at is not None:
        cluster.created_at = created_at
    db.add(cluster)
    db.flush()
    return cluster


def _mock_extraction():
    from app.extraction.schemas import ExtractionResult
    result = ExtractionResult(
        source_language="en",
        event_type="funding",
        company_names=["Acme"],
        person_names=[],
        product_names=[],
        geography_names=[],
        canonical_summary_en="Summary.",
        canonical_summary_ru="Итог.",
        extraction_confidence=0.9,
    )
    usage = LlmUsageInfo(model_name="test-model", input_tokens=10, output_tokens=5)
    return result, usage


def _mock_assessment():
    result = MagicMock(
        primary_section="companies_business",
        llm_score=0.8,
        include_in_digest=True,
        why_it_matters_en="Big deal.",
        why_it_matters_ru="Важно.",
        editorial_notes=None,
    )
    result.model_dump.return_value = {
        "primary_section": "companies_business",
        "llm_score": 0.8,
        "include_in_digest": True,
        "why_it_matters_en": "Big deal.",
        "why_it_matters_ru": "Важно.",
        "editorial_notes": None,
    }
    usage = LlmUsageInfo(model_name="test-model", input_tokens=10, output_tokens=5)
    return result, usage


# ── _run_extract_facts: date scoping ─────────────────────────────────────────


def test_extract_facts_includes_story_with_matching_published_at(db):
    src = _make_source(db)
    _make_story(db, src, published_at=_utc(RUN_DATE), suffix="match")
    db.commit()

    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_extraction()):
        result = _run_extract_facts(db, RUN_DATE, max_facts=10)

    assert result["eligible"] == 1
    assert result["processed"] == 1
    assert result["new"] == 1
    assert result["capped"] is False


def test_extract_facts_excludes_story_with_other_published_at(db):
    src = _make_source(db)
    _make_story(db, src, published_at=_utc(OTHER_DATE), suffix="other")
    db.commit()

    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_extraction()):
        result = _run_extract_facts(db, RUN_DATE, max_facts=10)

    assert result["eligible"] == 0
    assert result["processed"] == 0


def test_extract_facts_fallback_to_created_at_when_published_at_null(db):
    src = _make_source(db)
    _make_story(db, src, published_at=None, created_at=_utc(RUN_DATE), suffix="fallback")
    db.commit()

    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_extraction()):
        result = _run_extract_facts(db, RUN_DATE, max_facts=10)

    assert result["eligible"] == 1
    assert result["processed"] == 1
    assert result["new"] == 1


def test_extract_facts_excludes_story_with_null_published_at_and_wrong_created_at(db):
    src = _make_source(db)
    _make_story(db, src, published_at=None, created_at=_utc(OTHER_DATE), suffix="wrong")
    db.commit()

    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_extraction()):
        result = _run_extract_facts(db, RUN_DATE, max_facts=10)

    assert result["eligible"] == 0


def test_extract_facts_mixed_dates_only_matching_processed(db):
    src = _make_source(db)
    _make_story(db, src, published_at=_utc(RUN_DATE), suffix="a")
    _make_story(db, src, published_at=_utc(OTHER_DATE), suffix="b")
    _make_story(db, src, published_at=None, created_at=_utc(RUN_DATE), suffix="c")
    db.commit()

    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_extraction()):
        result = _run_extract_facts(db, RUN_DATE, max_facts=10)

    assert result["eligible"] == 2  # "a" and "c"
    assert result["processed"] == 2
    assert result["new"] == 2


def test_extract_facts_skips_already_extracted_stories(db):
    src = _make_source(db)
    story = _make_story(db, src, published_at=_utc(RUN_DATE), suffix="done")
    db.commit()

    # Pre-populate StoryFacts so the story is already extracted
    facts = StoryFacts(
        story_id=story.id,
        event_type="funding",
        model_name="test",
        extracted_at=datetime.now(timezone.utc),
    )
    db.add(facts)
    db.commit()

    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_extraction()):
        result = _run_extract_facts(db, RUN_DATE, max_facts=10)

    assert result["eligible"] == 0  # already has facts, filtered out by outerjoin


# ── _run_extract_facts: cap behavior ─────────────────────────────────────────


def test_extract_facts_cap_stops_processing(db):
    src = _make_source(db)
    for i in range(5):
        _make_story(db, src, published_at=_utc(RUN_DATE), suffix=str(i))
    db.commit()

    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_extraction()):
        result = _run_extract_facts(db, RUN_DATE, max_facts=2)

    assert result["eligible"] == 5
    assert result["processed"] == 2
    assert result["capped"] is True


def test_extract_facts_no_cap_hit_when_within_limit(db):
    src = _make_source(db)
    for i in range(3):
        _make_story(db, src, published_at=_utc(RUN_DATE), suffix=str(i))
    db.commit()

    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_extraction()):
        result = _run_extract_facts(db, RUN_DATE, max_facts=10)

    assert result["processed"] == 3
    assert result["capped"] is False


# ── _run_assess: date scoping ─────────────────────────────────────────────────


def _make_cluster_with_rep_story(
    db,
    *,
    rep_published_at: datetime | None,
    rep_created_at: datetime | None = None,
    suffix: str = "",
) -> tuple[Story, EventCluster]:
    src = _make_source(db, suffix=suffix or str(uuid.uuid4())[:8])
    story = _make_story(
        db, src,
        published_at=rep_published_at,
        created_at=rep_created_at,
        suffix=suffix,
    )
    cluster = _make_cluster(
        db,
        representative_story_id=story.id,
        suffix=suffix,
    )
    story.event_cluster_id = cluster.id
    db.flush()
    return story, cluster


def test_assess_includes_cluster_with_matching_rep_published_at(db):
    _, cluster = _make_cluster_with_rep_story(db, rep_published_at=_utc(RUN_DATE), suffix="m")
    db.commit()

    with (
        patch("app.scoring.service.assess_cluster_llm", return_value=_mock_assessment()),
        patch("app.orchestration.service.cluster_passes_any_section_gate", return_value=True),
    ):
        result = _run_assess(db, RUN_DATE, max_assess=10)

    assert result["eligible"] == 1
    assert result["processed"] == 1
    assert result["assessed"] == 1
    assert result["capped"] is False


def test_assess_excludes_cluster_with_other_rep_published_at(db):
    _, cluster = _make_cluster_with_rep_story(db, rep_published_at=_utc(OTHER_DATE), suffix="o")
    db.commit()

    with (
        patch("app.scoring.service.assess_cluster_llm", return_value=_mock_assessment()),
        patch("app.orchestration.service.cluster_passes_any_section_gate", return_value=True),
    ):
        result = _run_assess(db, RUN_DATE, max_assess=10)

    assert result["eligible"] == 0


def test_assess_fallback_to_rep_created_at_when_published_at_null(db):
    _, cluster = _make_cluster_with_rep_story(
        db,
        rep_published_at=None,
        rep_created_at=_utc(RUN_DATE),
        suffix="fb",
    )
    db.commit()

    with (
        patch("app.scoring.service.assess_cluster_llm", return_value=_mock_assessment()),
        patch("app.orchestration.service.cluster_passes_any_section_gate", return_value=True),
    ):
        result = _run_assess(db, RUN_DATE, max_assess=10)

    assert result["eligible"] == 1
    assert result["processed"] == 1


def test_assess_fallback_to_cluster_created_at_when_no_rep_story(db):
    cluster = _make_cluster(
        db,
        representative_story_id=None,
        created_at=_utc(RUN_DATE),
        suffix="noRep",
    )
    db.commit()

    with (
        patch("app.scoring.service.assess_cluster_llm", return_value=_mock_assessment()),
        patch("app.orchestration.service.cluster_passes_any_section_gate", return_value=True),
    ):
        result = _run_assess(db, RUN_DATE, max_assess=10)

    assert result["eligible"] == 1
    assert result["processed"] == 1


def test_assess_skips_clusters_failing_section_gate(db):
    _, _ = _make_cluster_with_rep_story(db, rep_published_at=_utc(RUN_DATE), suffix="g")
    db.commit()

    with (
        patch("app.scoring.service.assess_cluster_llm", return_value=_mock_assessment()),
        patch("app.orchestration.service.cluster_passes_any_section_gate", return_value=False),
    ):
        result = _run_assess(db, RUN_DATE, max_assess=10)

    assert result["eligible"] == 1
    assert result["skipped_gate"] == 1
    assert result["processed"] == 0


def test_assess_skips_already_assessed_clusters(db):
    _, cluster = _make_cluster_with_rep_story(db, rep_published_at=_utc(RUN_DATE), suffix="assessed")
    assessment = EventClusterAssessment(
        event_cluster_id=cluster.id,
        final_score=0.7,
        assessed_at=datetime.now(timezone.utc),
    )
    db.add(assessment)
    db.commit()

    with (
        patch("app.scoring.service.assess_cluster_llm", return_value=_mock_assessment()),
        patch("app.orchestration.service.cluster_passes_any_section_gate", return_value=True),
    ):
        result = _run_assess(db, RUN_DATE, max_assess=10)

    assert result["eligible"] == 0  # already assessed, filtered by outerjoin


# ── _run_assess: cap behavior ─────────────────────────────────────────────────


def test_assess_cap_stops_processing(db):
    for i in range(5):
        _make_cluster_with_rep_story(db, rep_published_at=_utc(RUN_DATE), suffix=f"cap{i}")
    db.commit()

    with (
        patch("app.scoring.service.assess_cluster_llm", return_value=_mock_assessment()),
        patch("app.orchestration.service.cluster_passes_any_section_gate", return_value=True),
    ):
        result = _run_assess(db, RUN_DATE, max_assess=2)

    assert result["eligible"] == 5
    assert result["processed"] == 2
    assert result["capped"] is True


def test_assess_no_cap_hit_when_within_limit(db):
    for i in range(3):
        _make_cluster_with_rep_story(db, rep_published_at=_utc(RUN_DATE), suffix=f"ok{i}")
    db.commit()

    with (
        patch("app.scoring.service.assess_cluster_llm", return_value=_mock_assessment()),
        patch("app.orchestration.service.cluster_passes_any_section_gate", return_value=True),
    ):
        result = _run_assess(db, RUN_DATE, max_assess=10)

    assert result["processed"] == 3
    assert result["capped"] is False


# ── config: cap fields wired from YAML ───────────────────────────────────────


def test_cap_fields_loaded_from_yaml(tmp_path):
    from app.config import load_settings
    f = tmp_path / "settings.yaml"
    f.write_text(
        "digest:\n"
        "  max_extract_facts_per_run: 42\n"
        "  max_assess_per_run: 33\n"
    )
    s = load_settings(config_path=str(f))
    assert s.digest.max_extract_facts_per_run == 42
    assert s.digest.max_assess_per_run == 33


def test_cap_fields_default_to_75():
    from app.config import load_settings
    s = load_settings(config_path="/nonexistent/path")
    assert s.digest.max_extract_facts_per_run == 75
    assert s.digest.max_assess_per_run == 75

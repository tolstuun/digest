"""
Tests for Phase 2B: LLM fact extraction.

All LLM calls are mocked — no real network requests are made.
"""
import hashlib
import pytest
from unittest.mock import patch

from app.extraction.schemas import ExtractionResult
from app.extraction.service import extract_story_facts
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts


# ── test fixtures ─────────────────────────────────────────────────────────────

def _make_story(db, summary: str = "Acme Corp raised $50M in Series B.") -> Story:
    """Create a minimal Source → RawItem → Story chain in the DB."""
    source = Source(name="Feed", type="rss", url="https://example.com/feed", enabled=True)
    db.add(source)
    db.flush()

    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(summary.encode()).hexdigest(),
        title="Acme Corp Raises $50M",
        url="https://example.com/article",
        raw_payload={"title": "Acme Corp Raises $50M", "summary": summary},
    )
    db.add(ri)
    db.flush()

    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title="Acme Corp Raises $50M",
        url="https://example.com/article",
        canonical_url="https://example.com/article",
    )
    db.add(story)
    db.commit()
    db.refresh(story)
    return story


def _mock_result(**overrides) -> ExtractionResult:
    defaults = dict(
        source_language="en",
        event_type="funding",
        company_names=["Acme Corp"],
        person_names=["Jane Smith"],
        product_names=[],
        geography_names=["USA"],
        amount_text="$50M",
        currency="USD",
        canonical_summary_en="Acme Corp raised $50M in Series B funding.",
        canonical_summary_ru="Acme Corp привлекла $50 млн в рамках раунда серии B.",
        extraction_confidence=0.92,
    )
    defaults.update(overrides)
    return ExtractionResult(**defaults)


# ── ExtractionResult schema validation (no DB) ───────────────────────────────

def test_extraction_result_all_valid_event_types():
    valid = [
        "funding", "mna", "earnings", "executive_change", "partnership",
        "product_launch", "breach", "conference", "regulation", "other", "unknown",
    ]
    for event_type in valid:
        result = _mock_result(event_type=event_type)
        assert result.event_type == event_type


def test_extraction_result_invalid_event_type_rejected():
    with pytest.raises(Exception):
        ExtractionResult(
            source_language="en",
            event_type="hacking",   # not in the allowed set
            company_names=[],
            person_names=[],
            product_names=[],
            geography_names=[],
            canonical_summary_en="",
            canonical_summary_ru="",
            extraction_confidence=0.5,
        )


def test_extraction_result_confidence_too_high_rejected():
    with pytest.raises(Exception):
        _mock_result(extraction_confidence=1.5)


def test_extraction_result_confidence_negative_rejected():
    with pytest.raises(Exception):
        _mock_result(extraction_confidence=-0.1)


def test_extraction_result_optional_amount_currency():
    result = _mock_result(amount_text=None, currency=None)
    assert result.amount_text is None
    assert result.currency is None


def test_extraction_result_empty_lists_allowed():
    result = _mock_result(company_names=[], person_names=[], geography_names=[])
    assert result.company_names == []


# ── extraction service (DB-backed, LLM mocked) ───────────────────────────────

def test_extract_creates_story_facts(db):
    story = _make_story(db)
    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_result()):
        facts, created = extract_story_facts(db, story)

    assert created is True
    assert facts.story_id == story.id
    assert facts.event_type == "funding"
    assert facts.company_names == ["Acme Corp"]
    assert facts.person_names == ["Jane Smith"]


def test_extract_persists_to_db(db):
    story = _make_story(db)
    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_result()):
        extract_story_facts(db, story)

    count = db.query(StoryFacts).filter_by(story_id=story.id).count()
    assert count == 1


def test_extract_stores_model_name(db):
    story = _make_story(db)
    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_result()):
        facts, _ = extract_story_facts(db, story)

    assert facts.model_name is not None
    assert len(facts.model_name) > 0


def test_extract_stores_raw_model_output(db):
    story = _make_story(db)
    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_result()):
        facts, _ = extract_story_facts(db, story)

    assert facts.raw_model_output is not None
    assert isinstance(facts.raw_model_output, dict)
    assert "event_type" in facts.raw_model_output


def test_extract_sets_extracted_at(db):
    story = _make_story(db)
    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_result()):
        facts, _ = extract_story_facts(db, story)

    assert facts.extracted_at is not None


def test_extract_repeated_updates_not_duplicates(db):
    story = _make_story(db)

    with patch("app.extraction.service.extract_facts_llm",
               return_value=_mock_result(extraction_confidence=0.7)):
        facts1, created1 = extract_story_facts(db, story)

    with patch("app.extraction.service.extract_facts_llm",
               return_value=_mock_result(extraction_confidence=0.95, event_type="mna")):
        facts2, created2 = extract_story_facts(db, story)

    assert created1 is True
    assert created2 is False          # updated, not inserted
    assert facts1.id == facts2.id    # same DB row
    assert facts2.event_type == "mna"
    assert facts2.extraction_confidence == 0.95
    assert db.query(StoryFacts).filter_by(story_id=story.id).count() == 1


def test_extract_multilingual_source(db):
    """Non-English source language still produces both EN and RU summaries."""
    story = _make_story(db, summary="Компания КиберБез привлекла $10 млн.")
    mock = _mock_result(
        source_language="ru",
        company_names=["КиберБез"],
        canonical_summary_en="CyberSec raised $10M.",
        canonical_summary_ru="КиберБез привлекла $10 млн.",
    )
    with patch("app.extraction.service.extract_facts_llm", return_value=mock):
        facts, _ = extract_story_facts(db, story)

    assert facts.source_language == "ru"
    assert facts.canonical_summary_en == "CyberSec raised $10M."
    assert facts.canonical_summary_ru == "КиберБез привлекла $10 млн."


def test_extract_passes_text_from_raw_payload(db):
    """Service reads summary from raw_payload and passes it to the LLM function."""
    story = _make_story(db, summary="Unique summary text for this article.")

    captured: list = []

    def capture_input(story_input):
        captured.append(story_input)
        return _mock_result()

    with patch("app.extraction.service.extract_facts_llm", side_effect=capture_input):
        extract_story_facts(db, story)

    assert len(captured) == 1
    assert "Unique summary text" in (captured[0].text or "")


# ── GET /stories/{id}/facts ───────────────────────────────────────────────────

def test_get_story_facts_returns_200(client, db):
    story = _make_story(db)
    with patch("app.extraction.service.extract_facts_llm", return_value=_mock_result()):
        extract_story_facts(db, story)

    resp = client.get(f"/stories/{story.id}/facts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["story_id"] == str(story.id)
    assert data["event_type"] == "funding"
    assert data["company_names"] == ["Acme Corp"]
    assert data["canonical_summary_en"] != ""
    assert data["canonical_summary_ru"] != ""


def test_get_story_facts_no_extraction_yet(client, db):
    story = _make_story(db)
    resp = client.get(f"/stories/{story.id}/facts")
    assert resp.status_code == 404


def test_get_story_facts_story_not_found(client):
    resp = client.get("/stories/00000000-0000-0000-0000-000000000000/facts")
    assert resp.status_code == 404


# ── POST /admin/stories/{id}/extract-facts ────────────────────────────────────

def test_extract_endpoint_success(client, db):
    story = _make_story(db)
    with patch("app.routers.admin.extract_story_facts") as mock_fn:
        mock_facts = StoryFacts(
            story_id=story.id,
            event_type="funding",
            model_name="claude-haiku-4-5-20251001",
        )
        mock_fn.return_value = (mock_facts, True)
        resp = client.post(f"/admin/stories/{story.id}/extract-facts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["story_id"] == str(story.id)
    assert data["created"] is True
    assert data["event_type"] == "funding"


def test_extract_endpoint_story_not_found(client):
    resp = client.post("/admin/stories/00000000-0000-0000-0000-000000000000/extract-facts")
    assert resp.status_code == 404

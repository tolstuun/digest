"""
Tests for Phase 5D: digest-writing LLM stage.

LLM calls are mocked — no real network requests.
"""
import hashlib
from unittest.mock import patch

import pytest

from app.clustering.rules import build_cluster_key
from app.digest_writer.schemas import DigestEntryInput, DigestEntryOutput
from app.digest_writer.service import write_digest_entries
from app.llm_usage.schemas import LlmUsageInfo
from app.models.digest_entry import DigestEntry
from app.models.digest_run import DigestRun
from app.models.event_cluster import EventCluster
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.story import Story
from app.models.story_facts import StoryFacts
from app.config import AppConfig, DatabaseConfig, DigestConfig, LLMConfig, SchedulerConfig, Settings, TelegramConfig
from datetime import date, datetime, timezone


SECTION = "companies_business"
TARGET_DATE = date(2026, 3, 26)


def _make_settings(output_language: str = "en") -> Settings:
    return Settings(
        config_path="test",
        app=AppConfig(),
        database=DatabaseConfig(),
        llm=LLMConfig(api_key="test-key"),
        telegram=TelegramConfig(),
        scheduler=SchedulerConfig(),
        digest=DigestConfig(
            output_language=output_language,
            model_writing="claude-haiku-4-5-20251001",
        ),
    )


def _make_run(db) -> DigestRun:
    run = DigestRun(
        digest_date=TARGET_DATE,
        section_name=SECTION,
        status="assembled",
        total_candidate_clusters=1,
        total_included_clusters=1,
        generated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _make_entry(db, run: DigestRun, rank: int = 1) -> DigestEntry:
    entry = DigestEntry(
        digest_run_id=run.id,
        rank=rank,
        title="Acme Corp raises $50M in cybersecurity funding",
        canonical_summary_en="Acme Corp raised $50M in Series B.",
        canonical_summary_ru="Acme Corp привлекла $50M.",
        why_it_matters_en="Significant deal for the market.",
        why_it_matters_ru="Важная сделка для рынка.",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _mock_usage() -> LlmUsageInfo:
    return LlmUsageInfo(model_name="claude-haiku-4-5-20251001", input_tokens=100, output_tokens=50)


def _mock_write_result(lang: str = "en") -> DigestEntryOutput:
    if lang == "ru":
        return DigestEntryOutput(
            final_title="Acme Corp привлекла $50 млн в раунде Series B",
            final_summary="Acme Corp привлекла $50 млн в раунде Series B.",
            final_why_it_matters="Это значимая инвестиция в сектор кибербезопасности.",
        )
    return DigestEntryOutput(
        final_title="Acme Corp Raises $50M in Series B Funding",
        final_summary="Acme Corp secured $50M in Series B funding.",
        final_why_it_matters="This is a notable investment in the cybersecurity sector.",
    )


# ── write_digest_entries service ──────────────────────────────────────────────

def test_write_updates_entry_final_fields(db):
    run = _make_run(db)
    entry = _make_entry(db, run)

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        return_value=(_mock_write_result(), _mock_usage()),
    ):
        result = write_digest_entries(db, run, _make_settings())

    db.refresh(entry)
    assert entry.final_title == "Acme Corp Raises $50M in Series B Funding"
    assert entry.final_summary == "Acme Corp secured $50M in Series B funding."
    assert entry.final_why_it_matters == "This is a notable investment in the cybersecurity sector."
    assert result["written"] == 1
    assert result["skipped"] == 0
    assert result["errors"] == 0


def test_write_skips_already_written_entries(db):
    run = _make_run(db)
    entry = _make_entry(db, run)
    entry.final_summary = "Already written."
    db.commit()

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        return_value=(_mock_write_result(), _mock_usage()),
    ) as mock_llm:
        result = write_digest_entries(db, run, _make_settings())

    assert result["skipped"] == 1
    assert result["written"] == 0
    mock_llm.assert_not_called()


def test_write_force_rewrites_existing(db):
    run = _make_run(db)
    entry = _make_entry(db, run)
    entry.final_summary = "Old summary."
    db.commit()

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        return_value=(_mock_write_result(), _mock_usage()),
    ):
        result = write_digest_entries(db, run, _make_settings(), force=True)

    db.refresh(entry)
    assert entry.final_summary == "Acme Corp secured $50M in Series B funding."
    assert result["written"] == 1
    assert result["skipped"] == 0


def test_write_multiple_entries(db):
    run = _make_run(db)
    entry1 = _make_entry(db, run, rank=1)
    entry2 = _make_entry(db, run, rank=2)

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        return_value=(_mock_write_result(), _mock_usage()),
    ):
        result = write_digest_entries(db, run, _make_settings())

    assert result["total"] == 2
    assert result["written"] == 2


def test_write_records_llm_usage(db):
    from app.models.llm_usage import LlmUsage

    run = _make_run(db)
    _make_entry(db, run)

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        return_value=(_mock_write_result(), _mock_usage()),
    ):
        write_digest_entries(db, run, _make_settings())

    usages = db.query(LlmUsage).filter_by(stage_name="write_digest").all()
    assert len(usages) == 1
    assert usages[0].model_name == "claude-haiku-4-5-20251001"
    assert usages[0].input_tokens == 100
    assert usages[0].output_tokens == 50


def test_write_passes_correct_language(db):
    run = _make_run(db)
    _make_entry(db, run)

    captured_inputs = []

    def capture(entry_input, model_name, api_key):
        captured_inputs.append(entry_input)
        return _mock_write_result(), _mock_usage()

    with patch("app.digest_writer.service.write_digest_entry_llm", side_effect=capture):
        write_digest_entries(db, run, _make_settings(output_language="ru"))

    assert len(captured_inputs) == 1
    assert captured_inputs[0].output_language == "ru"


def test_write_empty_run_returns_zero_counts(db):
    run = _make_run(db)

    with patch("app.digest_writer.service.write_digest_entry_llm") as mock_llm:
        result = write_digest_entries(db, run, _make_settings())

    assert result["total"] == 0
    assert result["written"] == 0
    mock_llm.assert_not_called()


def test_write_llm_error_is_counted_not_raised(db):
    run = _make_run(db)
    _make_entry(db, run)

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        side_effect=Exception("LLM unavailable"),
    ):
        result = write_digest_entries(db, run, _make_settings())

    assert result["errors"] == 1
    assert result["written"] == 0


# ── early relevance gate in write_digest_entries ──────────────────────────────

def _make_entry_with_cluster(db, run: DigestRun, company_names: list[str], event_type: str = "funding") -> DigestEntry:
    """Create a full Source→Story→StoryFacts→EventCluster chain attached to a DigestEntry."""
    source = Source(name="Feed", type="rss", url="https://example.com/feed2", enabled=True)
    db.add(source)
    db.flush()

    title = f"{company_names[0]} {event_type}"
    summary = f"{company_names[0]} raised $50M."
    ri = RawItem(
        source_id=source.id,
        content_hash=hashlib.sha256(summary.encode()).hexdigest(),
        title=title,
        url="https://example.com/article2",
        raw_payload={"title": title, "summary": summary},
    )
    db.add(ri)
    db.flush()

    story = Story(
        raw_item_id=ri.id,
        source_id=source.id,
        title=title,
        url="https://example.com/article2",
        canonical_url="https://example.com/article2",
    )
    db.add(story)
    db.flush()

    facts = StoryFacts(
        story_id=story.id,
        model_name="claude-haiku-4-5-20251001",
        event_type=event_type,
        company_names=company_names,
        person_names=[],
        product_names=[],
        geography_names=[],
        amount_text="$50M",
        currency="USD",
        source_language="en",
        canonical_summary_en=summary,
        canonical_summary_ru=summary,
        extraction_confidence=0.9,
    )
    db.add(facts)
    db.flush()

    cluster_key = build_cluster_key(event_type, company_names, "$50M", "USD")
    cluster = EventCluster(
        cluster_key=cluster_key or f"{event_type}-{company_names[0]}-fallback",
        event_type=event_type,
        representative_story_id=story.id,
    )
    db.add(cluster)
    db.flush()
    story.event_cluster_id = cluster.id
    db.flush()

    entry = DigestEntry(
        digest_run_id=run.id,
        event_cluster_id=cluster.id,
        rank=1,
        title=title,
        canonical_summary_en=summary,
        source_name="Feed",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def test_write_skips_irrelevant_cluster_before_llm(db):
    """Entries linked to clusters that fail the relevance gate are skipped before LLM."""
    run = _make_run(db)
    _make_entry_with_cluster(db, run, company_names=["Starbucks Coffee"])

    with patch("app.digest_writer.service.write_digest_entry_llm") as mock_llm:
        result = write_digest_entries(db, run, _make_settings())

    mock_llm.assert_not_called()
    assert result["skipped"] == 1
    assert result["written"] == 0


def test_write_processes_relevant_cluster(db):
    """Entries linked to clusters that pass the relevance gate are written via LLM."""
    run = _make_run(db)
    _make_entry_with_cluster(db, run, company_names=["CrowdStrike"])

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        return_value=(_mock_write_result(), _mock_usage()),
    ):
        result = write_digest_entries(db, run, _make_settings())

    assert result["written"] == 1
    assert result["skipped"] == 0


# ── write_digest retry on AnthropicOverloadedError ────────────────────────────

def test_write_digest_retries_on_overloaded_then_succeeds(db):
    """write_digest retries on AnthropicOverloadedError and succeeds on a later attempt."""
    from app.llm_usage.errors import AnthropicOverloadedError
    run = _make_run(db)
    entry = _make_entry(db, run)

    overloaded = AnthropicOverloadedError("overloaded", original=Exception("Overloaded"))
    success = (_mock_write_result(), _mock_usage())

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        side_effect=[overloaded, success],
    ) as mock_llm, \
         patch("app.digest_writer.service.time.sleep") as mock_sleep:
        result = write_digest_entries(db, run, _make_settings())

    assert result["written"] == 1
    assert result["errors"] == 0
    assert mock_llm.call_count == 2     # one retry
    mock_sleep.assert_called_once()     # backoff applied


def test_write_digest_raises_partial_overload_after_max_retries(db):
    """write_digest raises WriteDigestPartialOverloadError after exhausting all retries."""
    from app.llm_usage.errors import AnthropicOverloadedError, WriteDigestPartialOverloadError
    from app.digest_writer.service import _OVERLOAD_MAX_RETRIES
    run = _make_run(db)
    _make_entry(db, run)

    overloaded = AnthropicOverloadedError("overloaded", original=Exception("Overloaded"))

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        side_effect=overloaded,
    ) as mock_llm, \
         patch("app.digest_writer.service.time.sleep"):
        with pytest.raises(WriteDigestPartialOverloadError):
            write_digest_entries(db, run, _make_settings())

    # WriteDigestPartialOverloadError IS-A AnthropicOverloadedError
    assert mock_llm.call_count == _OVERLOAD_MAX_RETRIES


def test_write_digest_retry_budget_is_seven_attempts(db):
    """write_digest makes exactly 7 total attempts before giving up."""
    from app.llm_usage.errors import AnthropicOverloadedError, WriteDigestPartialOverloadError
    from app.digest_writer.service import _OVERLOAD_MAX_RETRIES

    assert _OVERLOAD_MAX_RETRIES == 7, "Expected 7 total attempts for async digest path"

    run = _make_run(db)
    _make_entry(db, run)
    overloaded = AnthropicOverloadedError("overloaded", original=Exception("Overloaded"))

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        side_effect=overloaded,
    ) as mock_llm, \
         patch("app.digest_writer.service.time.sleep"):
        with pytest.raises(WriteDigestPartialOverloadError):
            write_digest_entries(db, run, _make_settings())

    assert mock_llm.call_count == 7


def test_write_digest_partial_overload_carries_state(db):
    """WriteDigestPartialOverloadError carries accurate partial-state fields."""
    from app.llm_usage.errors import AnthropicOverloadedError, WriteDigestPartialOverloadError

    run = _make_run(db)
    entry1 = _make_entry(db, run, rank=1)
    entry2 = _make_entry(db, run, rank=2)

    overloaded = AnthropicOverloadedError("overloaded", original=Exception("Overloaded"))
    success = (_mock_write_result(), _mock_usage())

    # entry1 succeeds, entry2 permanently overloaded across all retry attempts
    from app.digest_writer.service import _OVERLOAD_MAX_RETRIES
    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        side_effect=[success] + [overloaded] * _OVERLOAD_MAX_RETRIES,
    ), patch("app.digest_writer.service.time.sleep"):
        with pytest.raises(WriteDigestPartialOverloadError) as exc_info:
            write_digest_entries(db, run, _make_settings())

    exc = exc_info.value
    assert exc.written == 1               # entry1 committed
    assert exc.remaining_unwritten == 0   # entry2 was last; nothing after it
    assert exc.skipped == 0
    assert exc.errors == 0

    db.refresh(entry1)
    db.refresh(entry2)
    assert entry1.final_summary is not None   # committed
    assert entry2.final_summary is None       # rolled back


def test_write_digest_overloaded_does_not_write_current_entry(db):
    """The entry that triggered the overload abort is not partially written."""
    from app.llm_usage.errors import AnthropicOverloadedError, WriteDigestPartialOverloadError
    run = _make_run(db)
    entry = _make_entry(db, run)

    overloaded = AnthropicOverloadedError("overloaded", original=Exception("Overloaded"))

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        side_effect=overloaded,
    ), patch("app.digest_writer.service.time.sleep"):
        with pytest.raises(WriteDigestPartialOverloadError):
            write_digest_entries(db, run, _make_settings())

    db.refresh(entry)
    assert entry.final_summary is None      # rolled back cleanly


def test_write_digest_resume_skips_already_written_entries(db):
    """A retry after partial overload skips committed entries and only writes missing ones."""
    from app.llm_usage.errors import AnthropicOverloadedError, WriteDigestPartialOverloadError

    run = _make_run(db)
    entry1 = _make_entry(db, run, rank=1)
    entry2 = _make_entry(db, run, rank=2)

    overloaded = AnthropicOverloadedError("overloaded", original=Exception("Overloaded"))
    success = (_mock_write_result(), _mock_usage())

    # First call: entry1 succeeds, entry2 permanently overloaded across all retry attempts
    from app.digest_writer.service import _OVERLOAD_MAX_RETRIES
    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        side_effect=[success] + [overloaded] * _OVERLOAD_MAX_RETRIES,
    ), patch("app.digest_writer.service.time.sleep"):
        with pytest.raises(WriteDigestPartialOverloadError):
            write_digest_entries(db, run, _make_settings())

    db.refresh(entry1)
    db.refresh(entry2)
    assert entry1.final_summary is not None
    assert entry2.final_summary is None

    # Second call: entry1 skipped (already written), entry2 written
    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        return_value=success,
    ) as mock_llm, \
         patch("app.digest_writer.service.time.sleep"):
        result = write_digest_entries(db, run, _make_settings())

    assert result["written"] == 1     # only entry2
    assert result["skipped"] == 1     # entry1 skipped
    assert mock_llm.call_count == 1   # LLM called only for entry2


def test_write_digest_backoff_sequence_matches_spec(db):
    """Backoff sequence is exactly [5, 15, 30, 60, 120, 240] and all sleeps are applied."""
    from app.llm_usage.errors import AnthropicOverloadedError, WriteDigestPartialOverloadError
    from app.digest_writer.service import _OVERLOAD_BACKOFF_SECONDS

    assert _OVERLOAD_BACKOFF_SECONDS == [5, 15, 30, 60, 120, 240]

    run = _make_run(db)
    _make_entry(db, run)
    overloaded = AnthropicOverloadedError("overloaded", original=Exception("Overloaded"))
    sleep_calls = []

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        side_effect=overloaded,
    ), patch("app.digest_writer.service.time.sleep", side_effect=sleep_calls.append):
        with pytest.raises(WriteDigestPartialOverloadError):
            write_digest_entries(db, run, _make_settings())

    assert sleep_calls == _OVERLOAD_BACKOFF_SECONDS  # all 6 waits applied


# ── final_title tests (Part A: titles in output_language) ────────────────────

def test_final_title_written_for_english_output(db):
    """write_digest_entries persists final_title from LLM output (English)."""
    run = _make_run(db)
    entry = _make_entry(db, run)

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        return_value=(_mock_write_result("en"), _mock_usage()),
    ):
        write_digest_entries(db, run, _make_settings("en"))

    db.refresh(entry)
    assert entry.final_title == "Acme Corp Raises $50M in Series B Funding"
    assert entry.final_title == entry.final_title  # sanity — not None


def test_final_title_written_for_russian_output(db):
    """write_digest_entries persists final_title from LLM output (Russian)."""
    run = _make_run(db)
    entry = _make_entry(db, run)

    with patch(
        "app.digest_writer.service.write_digest_entry_llm",
        return_value=(_mock_write_result("ru"), _mock_usage()),
    ):
        write_digest_entries(db, run, _make_settings("ru"))

    db.refresh(entry)
    assert entry.final_title == "Acme Corp привлекла $50 млн в раунде Series B"


def test_render_uses_final_title_when_set():
    """HTML renderer uses final_title when set, not the raw original title."""
    from app.rendering.html import _render_entry

    entry = DigestEntry(
        rank=1,
        title="原始中文标题",           # Chinese original title
        final_title="English Translated Title",
        final_summary="Summary text.",
        final_why_it_matters="Why it matters.",
    )
    html = _render_entry(entry, "en")
    assert "English Translated Title" in html
    assert "原始中文标题" not in html


def test_render_falls_back_to_title_when_final_title_absent():
    """HTML renderer falls back to title when final_title is not set (old rows)."""
    from app.rendering.html import _render_entry

    entry = DigestEntry(
        rank=1,
        title="Original Title Without Translation",
        final_title=None,
        final_summary="Summary text.",
        final_why_it_matters="Why it matters.",
    )
    html = _render_entry(entry, "en")
    assert "Original Title Without Translation" in html
